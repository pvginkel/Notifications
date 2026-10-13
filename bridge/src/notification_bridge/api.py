"""The API's request bodies: their fields, their limits, and the refusal a bad one gets.

A field sent as JSON `null` is taken as omitted.
"""

import json
from dataclasses import dataclass

from notification_bridge.render import Priority

MAX_SEQUENCE_ID = 256
MAX_TITLE = 250
MAX_MESSAGE = 4096
PRIORITIES = {priority.name.lower(): priority for priority in Priority}


class Refusal(Exception):
    """A request the bridge refuses: the status it answers and why."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class Submission:
    sequence_id: str | None  # None for a one-shot
    priority: Priority
    title: str | None
    message: str


def parse_submission(body: bytes) -> Submission:
    fields = _fields(body, {"sequence_id", "priority", "title", "message"})
    sequence_id = _sequence_id(fields)

    priority = fields.get("priority", "default")
    if not isinstance(priority, str) or priority not in PRIORITIES:
        raise Refusal(400, f"priority must be one of {', '.join(PRIORITIES)}")

    title = _string(fields, "title")
    if title is not None and len(title) > MAX_TITLE:
        raise Refusal(400, f"title is {len(title)} characters, over the limit of {MAX_TITLE}")

    message = _string(fields, "message")
    if message is None:
        raise Refusal(400, "message is missing")
    if len(message) > MAX_MESSAGE:
        raise Refusal(413, f"message is {len(message)} characters, over the limit of {MAX_MESSAGE}")
    if not message.strip():
        raise Refusal(400, "message is blank")

    return Submission(sequence_id, PRIORITIES[priority], title, message)


def parse_dismissal(body: bytes) -> str:
    """The sequence id a dismiss names."""
    sequence_id = _sequence_id(_fields(body, {"sequence_id"}))
    if sequence_id is None:
        raise Refusal(400, "sequence_id is missing")
    return sequence_id


def _fields(body: bytes, known: set[str]) -> dict[str, object]:
    try:
        fields = json.loads(body)
    except ValueError as error:
        raise Refusal(400, f"the body is not JSON: {error}") from error
    if not isinstance(fields, dict):
        raise Refusal(400, "the body must be a JSON object")
    unknown = sorted(set(fields) - known)
    if unknown:
        raise Refusal(400, f"unknown fields: {', '.join(unknown)}")
    return {key: value for key, value in fields.items() if value is not None}


def _sequence_id(fields: dict[str, object]) -> str | None:
    sequence_id = _string(fields, "sequence_id")
    if sequence_id is not None and not 1 <= len(sequence_id) <= MAX_SEQUENCE_ID:
        raise Refusal(400, f"sequence_id must be 1 to {MAX_SEQUENCE_ID} characters")
    return sequence_id


def _string(fields: dict[str, object], key: str) -> str | None:
    value = fields.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise Refusal(400, f"{key} must be a string")
    # JSON's \ud800 escapes decode to a lone surrogate, which has no UTF-8 form for the id's hash,
    # ntfy's body or Telegram's JSON.
    try:
        value.encode()
    except UnicodeEncodeError as error:
        raise Refusal(400, f"{key} holds a lone surrogate") from error
    return value
