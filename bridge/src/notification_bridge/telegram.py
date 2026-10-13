"""Delivery to Telegram: a message in each chat of the registration, sent by the registration's bot.

A resubmit edits a sequence's message in place, which never notifies. A rise in priority deletes
the message and sends it fresh, so that it notifies. A dismiss deletes it. Telegram refuses to
delete a message past 48 hours in a private chat, and the message is then edited to a fixed line:
the state holds no text to edit it to. A message Telegram no longer has, deleted by hand, is sent
fresh on the next resubmit.
"""

from dataclasses import dataclass
from typing import Any

import httpx2

from notification_bridge.api import Submission
from notification_bridge.config import Registration
from notification_bridge.render import Priority, telegram_html
from notification_bridge.state import State, TelegramRow

API_URL = "https://api.telegram.org"

# The refusals the bridge acts on, matched by case-insensitive substring of Telegram's description:
# the Bot API documents none of them.
NOT_MODIFIED = "message is not modified"
EDIT_NOT_FOUND = "message to edit not found"
DELETE_NOT_FOUND = "message to delete not found"

# The fixed lines a message is edited to when Telegram refuses to delete it.
DISMISSED = "<i>Dismissed.</i>"
SUPERSEDED = "<i>Superseded.</i>"

NO_PREVIEW = {"is_disabled": True}

# How much of an answer that is not Telegram's JSON the outcome quotes.
QUOTED = 200


class Telegram:
    name = "telegram"

    def __init__(self, client: httpx2.AsyncClient, state: State) -> None:
        self._client = client
        self._state = state

    def serves(self, registration: Registration) -> bool:
        return registration.telegram is not None

    async def submit(self, registration: Registration, submission: Submission) -> str | None:
        """Show the submission in every chat. None when each chat took it, else why not, per chat.

        A chat's row follows the message that shows the submission, and stays as it was when none
        does.
        """
        text = telegram_html(submission.title, submission.message, submission.priority)
        sequence_id = submission.sequence_id
        rows = (
            {} if sequence_id is None else self._state.telegram_rows(registration.name, sequence_id)
        )
        failures = []
        for chat in self._chats(registration):
            message_id, chat_failures = await _show(
                chat, rows.get(chat.id), text, submission.priority
            )
            failures += [f"{chat.id}: {failure}" for failure in chat_failures]
            if sequence_id is not None and message_id is not None:
                self._state.put_telegram_row(
                    registration.name,
                    sequence_id,
                    chat.id,
                    TelegramRow(submission.priority, message_id),
                )
        return "; ".join(failures) or None

    async def dismiss(self, registration: Registration, sequence_id: str) -> str | None:
        """Take the sequence's message down in each chat that has a row. None when each chat took
        the dismiss, else why not, per chat.

        A chat's row goes when its message is deleted, gone already, or edited to `DISMISSED`, and
        stays for the next dismiss when none of that happened.
        """
        rows = self._state.telegram_rows(registration.name, sequence_id)
        failures = []
        for chat in self._chats(registration):
            row = rows.get(chat.id)
            if row is None:
                continue
            chat_failures = await _take_down(chat, row.message_id, DISMISSED)
            failures += [f"{chat.id}: {failure}" for failure in chat_failures]
            if not chat_failures:
                self._state.delete_telegram_row(registration.name, sequence_id, chat.id)
        return "; ".join(failures) or None

    def _chats(self, registration: Registration) -> list["_Chat"]:
        telegram = registration.telegram
        return [_Chat(self._client, telegram.bot_token, chat_id) for chat_id in telegram.chat_ids]


async def _show(
    chat: "_Chat", row: TelegramRow | None, text: str, priority: Priority
) -> tuple[int | None, list[str]]:
    """The id of the message that shows `text` in the chat, None when none does, and what failed.

    With a row, a priority above the row's takes the old message down and sends a fresh one, and
    any other priority edits the old one.
    """
    failures = []
    if row is not None:
        if priority > row.priority:
            # The fresh message goes out whatever the take-down answered.
            failures = await _take_down(chat, row.message_id, SUPERSEDED)
        else:
            edited = await chat.edit(row.message_id, text)
            if edited.failure is None or edited.says(NOT_MODIFIED):
                return row.message_id, []
            if not edited.says(EDIT_NOT_FOUND):
                return None, [edited.failure]
    sent = await chat.send(text, priority)
    if sent.failure is not None:
        return None, [*failures, sent.failure]
    return sent.result["message_id"], failures


async def _take_down(chat: "_Chat", message_id: int, line: str) -> list[str]:
    """Delete the message, or edit it to `line` when Telegram refuses. What failed, if both did."""
    deleted = await chat.delete(message_id)
    if deleted.failure is None or deleted.says(DELETE_NOT_FOUND):
        return []
    edited = await chat.edit(message_id, line)
    if edited.failure is None or edited.says(NOT_MODIFIED) or edited.says(EDIT_NOT_FOUND):
        return []
    return [deleted.failure, edited.failure]


@dataclass(frozen=True)
class _Answer:
    result: Any = None
    failure: str | None = None  # why Telegram did not take the call; None when it did
    description: str = ""  # Telegram's description of a refusal, lower-cased

    def says(self, text: str) -> bool:
        return text in self.description


class _Chat:
    """One chat, as the registration's bot reaches it."""

    def __init__(self, client: httpx2.AsyncClient, bot_token: str, chat_id: str) -> None:
        self.id = chat_id
        self._client = client
        self._bot_token = bot_token

    async def send(self, text: str, priority: Priority) -> _Answer:
        return await self._call(
            "send",
            "sendMessage",
            text=text,
            parse_mode="HTML",
            link_preview_options=NO_PREVIEW,
            disable_notification=priority <= Priority.LOW,
        )

    async def edit(self, message_id: int, text: str) -> _Answer:
        # An edit that leaves out the link preview options can bring the preview back.
        return await self._call(
            "edit",
            "editMessageText",
            message_id=message_id,
            text=text,
            parse_mode="HTML",
            link_preview_options=NO_PREVIEW,
        )

    async def delete(self, message_id: int) -> _Answer:
        return await self._call("delete", "deleteMessage", message_id=message_id)

    async def _call(self, step: str, method: str, **fields: object) -> _Answer:
        try:
            response = await self._client.post(
                f"/bot{self._bot_token}/{method}", json={"chat_id": self.id, **fields}
            )
        except httpx2.HTTPError as error:
            return _Answer(failure=f"{step}: {type(error).__name__}: {error}")
        return _answer(step, response)


def _answer(step: str, response: httpx2.Response) -> _Answer:
    """Telegram answers with a JSON object: `ok` and the call's `result`, or a refusal's
    `description`."""
    try:
        answer = response.json()
        if answer["ok"] is True:
            return _Answer(result=answer["result"])
        description = str(answer["description"])
    except (ValueError, TypeError, KeyError):
        quoted = response.text.strip()[:QUOTED] or response.reason_phrase
        return _Answer(failure=f"{step}: HTTP {response.status_code}: {quoted}")
    return _Answer(
        failure=f"{step}: HTTP {response.status_code}: {description}",
        description=description.lower(),
    )
