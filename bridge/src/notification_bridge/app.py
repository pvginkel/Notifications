"""The bridge's HTTP API on the one port: submit, dismiss, the health probe and the metrics."""

import asyncio
import contextlib
import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol
from weakref import WeakValueDictionary

import httpx2
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from notification_bridge.api import Refusal, Submission, parse_dismissal, parse_submission
from notification_bridge.config import Config, Registration
from notification_bridge.logs import log
from notification_bridge.metrics import Metrics
from notification_bridge.ntfy import Ntfy
from notification_bridge.state import State

PRUNE_EVERY = timedelta(days=1)
NTFY_TIMEOUT = 10  # seconds, per call


class Channel(Protocol):
    """A delivery channel. Its `name` keys the response, the metrics and the state's rows.

    `submit` and `dismiss` answer None when the channel took the call, else why not: a channel's
    failure is the outcome it reports, never an exception and never retried.
    """

    name: str

    def serves(self, registration: Registration) -> bool: ...

    async def submit(self, registration: Registration, submission: Submission) -> str | None: ...

    async def dismiss(self, registration: Registration, sequence_id: str) -> str | None: ...


def create_app(
    config: Config,
    *,
    ntfy_transport: httpx2.AsyncBaseTransport | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Starlette:
    """The bridge. `ntfy_transport` and `now` stand in for the network and the clock in tests."""
    state = State(config.state_dir / "state.db", now)
    ntfy_client = httpx2.AsyncClient(
        base_url=config.ntfy_url,
        headers={"Authorization": f"Bearer {config.ntfy_token}"},
        timeout=NTFY_TIMEOUT,
        transport=ntfy_transport,
    )
    bridge = _Bridge(config.registrations, [Ntfy(ntfy_client, state, config.icon_base_url)], state)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        pruning = asyncio.create_task(prune_daily(state))
        yield
        pruning.cancel()
        # Raises what stopped the prune, if anything did before the cancel.
        with contextlib.suppress(asyncio.CancelledError):
            await pruning
        await ntfy_client.aclose()
        state.close()

    return Starlette(
        routes=[
            Route("/healthz", healthz),
            Route("/metrics", bridge.metrics),
            Route("/v1/submit", bridge.submit, methods=["POST"]),
            Route("/v1/dismiss", bridge.dismiss, methods=["POST"]),
        ],
        lifespan=lifespan,
    )


async def healthz(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def prune_daily(
    state: State, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
) -> None:
    while True:
        log.info("prune", extra={"fields": {"removed": state.prune()}})
        await sleep(PRUNE_EVERY.total_seconds())


class _Bridge:
    def __init__(
        self, registrations: Sequence[Registration], channels: Sequence[Channel], state: State
    ) -> None:
        self._registrations = registrations
        self._channels = channels
        self._metrics = Metrics(
            state,
            ((r.name, c.name) for r in registrations for c in channels if c.serves(r)),
        )
        # A submit or a dismiss reads a sequence's rows, calls the channels, then writes the rows:
        # two calls of one sequence at once would fork it, so they take turns.
        self._locks: WeakValueDictionary[tuple[str, str], asyncio.Lock] = WeakValueDictionary()

    async def submit(self, request: Request) -> Response:
        registration = self._authenticate(request)
        if registration is None:
            return _refuse("submit", None, Refusal(401, "unknown bearer token"))
        try:
            submission = parse_submission(await request.body())
        except Refusal as refusal:
            return _refuse("submit", registration, refusal)

        async with self._turn(registration, submission.sequence_id):
            outcome = {
                channel.name: self._record(
                    registration, channel, await channel.submit(registration, submission)
                )
                for channel in self._channels
                if channel.serves(registration)
            }
        return _answer("submit", registration, submission.sequence_id, outcome)

    async def dismiss(self, request: Request) -> Response:
        registration = self._authenticate(request)
        if registration is None:
            return _refuse("dismiss", None, Refusal(401, "unknown bearer token"))
        try:
            sequence_id = parse_dismissal(await request.body())
        except Refusal as refusal:
            return _refuse("dismiss", registration, refusal)

        async with self._turn(registration, sequence_id):
            outcome = {
                channel.name: self._record(
                    registration, channel, await channel.dismiss(registration, sequence_id)
                )
                for channel in self._channels
                if channel.serves(registration)
            }
        return _answer("dismiss", registration, sequence_id, outcome)

    async def metrics(self, request: Request) -> Response:
        return Response(generate_latest(self._metrics.registry), media_type=CONTENT_TYPE_LATEST)

    def _authenticate(self, request: Request) -> Registration | None:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer":
            return None
        found = None
        # Every registration's token is compared, each in constant time: no match exits early.
        for registration in self._registrations:
            if hmac.compare_digest(registration.token.encode(), token.encode()):
                found = registration
        return found

    @contextlib.asynccontextmanager
    async def _turn(
        self, registration: Registration, sequence_id: str | None
    ) -> AsyncIterator[None]:
        if sequence_id is None:
            yield
            return
        key = (registration.name, sequence_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        async with lock:
            yield

    def _record(self, registration: Registration, channel: Channel, failure: str | None) -> str:
        self._metrics.count(registration.name, channel.name, failure is None)
        return "ok" if failure is None else f"error: {failure}"


def _answer(
    event: str, registration: Registration, sequence_id: str | None, outcome: dict[str, str]
) -> JSONResponse:
    level = logging.INFO if all(value == "ok" for value in outcome.values()) else logging.WARNING
    fields = {"registration": registration.name, "sequence_id": sequence_id, "outcome": outcome}
    log.log(level, event, extra={"fields": fields})
    return JSONResponse(outcome)


def _refuse(event: str, registration: Registration | None, refusal: Refusal) -> JSONResponse:
    fields = {
        "registration": None if registration is None else registration.name,
        "status": refusal.status,
        "error": refusal.reason,
    }
    log.warning(event, extra={"fields": fields})
    headers = {"WWW-Authenticate": "Bearer"} if refusal.status == 401 else None
    return JSONResponse({"error": refusal.reason}, status_code=refusal.status, headers=headers)
