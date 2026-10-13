import asyncio
import contextlib
import itertools
import json
import sqlite3
import textwrap
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from notification_bridge.app import create_app
from notification_bridge.config import load_config
from prometheus_client.parser import text_string_to_metric_families
from starlette.applications import Starlette
from starlette.testclient import TestClient

# Invented values only: this repo is public.
CONTRACT_ENV = {
    "NTFY_URL": "http://ntfy.invalid",
    "NTFY_TOKEN": "tk_invented",
    "ICON_BASE_URL": "https://icons.invalid/registration-icons",
}


@pytest.fixture
def icons_dir(tmp_path: Path) -> Path:
    icons = tmp_path / "icons"
    icons.mkdir()
    for name in ("jenkins.png", "alerts.png"):
        (icons / name).write_bytes(b"invented icon")
    return icons


@pytest.fixture
def make_env(tmp_path: Path, icons_dir: Path) -> Callable[..., dict[str, str]]:
    """Write a registrations file and return an environment that points the bridge at it."""

    def make(registrations: str, **variables: str) -> dict[str, str]:
        path = tmp_path / "registrations.yaml"
        path.write_text(textwrap.dedent(registrations))
        (tmp_path / "state").mkdir(exist_ok=True)
        return {
            **CONTRACT_ENV,
            "BRIDGE_REGISTRATIONS_FILE": str(path),
            "BRIDGE_ICONS_DIR": str(icons_dir),
            "BRIDGE_STATE_DIR": str(tmp_path / "state"),
            **variables,
        }

    return make


@dataclass(frozen=True)
class NtfyCall:
    method: str
    path: str
    headers: httpx2.Headers
    body: str

    @property
    def sequence(self) -> str | None:
        return self.headers.get("X-Sequence-ID")


class FakeNtfy:
    """ntfy as the bridge reaches it: records every call, and answers 200 unless told to refuse."""

    def __init__(self) -> None:
        self.calls: list[NtfyCall] = []
        self._refusals: dict[str, Callable[[httpx2.Request], httpx2.Response]] = {}

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def refuse(self, method: str, status: int = 403, error: str = "forbidden") -> None:
        """Refuse every `method` call (POST publishes, PUT clears) the way ntfy refuses."""
        body = {"code": status * 100 + 1, "http": status, "error": error}
        self._refusals[method] = lambda request: httpx2.Response(status, json=body)

    def answer(self, method: str, status: int, text: str) -> None:
        """Answer every `method` call with `status` and a body that is not ntfy's."""
        self._refusals[method] = lambda request: httpx2.Response(status, text=text)

    def go_down(self) -> None:
        def refuse_connection(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("[Errno 111] Connection refused", request=request)

        self._refusals = {"POST": refuse_connection, "PUT": refuse_connection}

    def recover(self) -> None:
        self._refusals = {}

    def sent(self) -> list[tuple[str, str, str | None]]:
        """Each call's method, path and sequence id; the record is cleared."""
        calls = [(call.method, call.path, call.sequence) for call in self.calls]
        self.calls.clear()
        return calls

    async def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append(
            NtfyCall(request.method, request.url.path, request.headers, request.content.decode())
        )
        # Lets another request run meanwhile, as a real ntfy's answer would.
        await asyncio.sleep(0)
        refusal = self._refusals.get(request.method)
        if refusal is None:
            return httpx2.Response(200, json={"id": "invented"})
        return refusal(request)


@dataclass(frozen=True)
class TelegramCall:
    bot_token: str
    method: str
    fields: dict[str, Any]


class FakeTelegram:
    """The Bot API as the bridge reaches it: records every call and keeps each chat's messages.

    It answers an edit or a delete of a message the chat does not have as Telegram does (not
    found), and an edit that changes nothing (not modified). It takes every other call unless told
    to refuse.
    """

    def __init__(self) -> None:
        self.calls: list[TelegramCall] = []
        # Each chat's messages: message id to text.
        self.chats: dict[str, dict[int, str]] = {}
        self._message_ids = itertools.count(1)
        self._refusals: dict[
            tuple[str, str | None], Callable[[httpx2.Request], httpx2.Response]
        ] = {}

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def refuse(
        self, method: str, description: str, *, chat: str | None = None, status: int = 400
    ) -> None:
        """Refuse every `method` call, in `chat` or in any, the way Telegram refuses."""
        body = {"ok": False, "error_code": status, "description": description}
        self._refusals[(method, chat)] = lambda request: httpx2.Response(status, json=body)

    def answer(self, method: str, status: int, text: str) -> None:
        """Answer every `method` call with `status` and a body that is not Telegram's."""
        self._refusals[(method, None)] = lambda request: httpx2.Response(status, text=text)

    def go_down(self) -> None:
        def refuse_connection(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("[Errno 111] Connection refused", request=request)

        self._refusals = {
            (method, None): refuse_connection
            for method in ("sendMessage", "editMessageText", "deleteMessage")
        }

    def recover(self) -> None:
        self._refusals = {}

    def delete_by_hand(self, chat: str, message_id: int) -> None:
        del self.chats[chat][message_id]

    def sent(self) -> list[tuple[str, str, int | None]]:
        """Each call's method, chat and message id (None on a send); the record is cleared."""
        calls = [
            (call.method, call.fields["chat_id"], call.fields.get("message_id"))
            for call in self.calls
        ]
        self.calls.clear()
        return calls

    async def _handle(self, request: httpx2.Request) -> httpx2.Response:
        bot_token, _, method = request.url.path.removeprefix("/bot").partition("/")
        fields = json.loads(request.content)
        self.calls.append(TelegramCall(bot_token, method, fields))
        # Lets another request run meanwhile, as Telegram's answer would.
        await asyncio.sleep(0)
        chat = fields["chat_id"]
        refusal = self._refusals.get((method, chat)) or self._refusals.get((method, None))
        if refusal is not None:
            return refusal(request)
        messages = self.chats.setdefault(chat, {})
        if method == "sendMessage":
            message_id = next(self._message_ids)
            messages[message_id] = fields["text"]
            return _taken({"message_id": message_id, "chat": {"id": chat}, "text": fields["text"]})
        message_id = fields["message_id"]
        if method == "editMessageText":
            if message_id not in messages:
                return _refused("Bad Request: message to edit not found")
            if messages[message_id] == fields["text"]:
                return _refused(
                    "Bad Request: message is not modified: specified new message content and"
                    " reply markup are exactly the same as a current content and reply markup of"
                    " the message"
                )
            messages[message_id] = fields["text"]
            return _taken({"message_id": message_id, "chat": {"id": chat}, "text": fields["text"]})
        if method == "deleteMessage":
            if message_id not in messages:
                return _refused("Bad Request: message to delete not found")
            del messages[message_id]
            return _taken(True)
        raise AssertionError(f"the bridge called {method}")


def _taken(result: object) -> httpx2.Response:
    return httpx2.Response(200, json={"ok": True, "result": result})


def _refused(description: str) -> httpx2.Response:
    return httpx2.Response(400, json={"ok": False, "error_code": 400, "description": description})


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def fake_ntfy() -> FakeNtfy:
    return FakeNtfy()


@pytest.fixture
def fake_telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def clock() -> Clock:
    return Clock()


# Two registrations on one topic, each with its own icon.
REGISTRATIONS = """
    registrations:
      jenkins:
        token: invented-jenkins-token
        ntfy: { topic: builds, icon: jenkins.png }
      alerts:
        token: invented-alerts-token
        ntfy: { topic: builds, icon: alerts.png }
"""


class Bridge:
    """A running bridge, called as a registration calls it."""

    def __init__(self, app: Starlette, client: TestClient, state_db: Path) -> None:
        self.app = app
        self.client = client
        self.state_db = state_db

    def submit(self, body: object = None, registration: str = "jenkins", **fields: object):
        return self.post("/v1/submit", fields if body is None else body, registration)

    def dismiss(self, body: object = None, registration: str = "jenkins", **fields: object):
        return self.post("/v1/dismiss", fields if body is None else body, registration)

    def post(self, path: str, body: object, registration: str | None):
        """POST `body`, as JSON or as the bytes given, under the registration's token."""
        headers = {} if registration is None else self.authorization(registration)
        if isinstance(body, bytes):
            return self.client.post(path, content=body, headers=headers)
        return self.client.post(path, json=body, headers=headers)

    @staticmethod
    def authorization(registration: str) -> dict[str, str]:
        return {"Authorization": f"Bearer invented-{registration}-token"}

    def rows(self) -> list[dict[str, object]]:
        with contextlib.closing(sqlite3.connect(self.state_db)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM sequence ORDER BY registration, sequence_id")
            return [dict(row) for row in rows]

    def metric(self, name: str, **labels: str) -> float:
        families = text_string_to_metric_families(self.client.get("/metrics").text)
        [value] = [
            sample.value
            for family in families
            for sample in family.samples
            if sample.name == name and sample.labels == labels
        ]
        return value


@pytest.fixture
def start_bridge(make_env, fake_ntfy, fake_telegram, clock) -> Iterator[Callable[[str], Bridge]]:
    """Start a bridge on a registrations file, against the fakes and the clock."""
    with contextlib.ExitStack() as running:

        def start(registrations: str = REGISTRATIONS) -> Bridge:
            config = load_config(make_env(registrations))
            app = create_app(
                config,
                ntfy_transport=fake_ntfy.transport,
                telegram_transport=fake_telegram.transport,
                now=clock,
            )
            client = running.enter_context(TestClient(app))
            return Bridge(app, client, config.state_dir / "state.db")

        yield start


@pytest.fixture
def bridge(start_bridge) -> Bridge:
    return start_bridge()
