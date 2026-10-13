"""Delivery to ntfy: publish to and clear on the registration's topic, as the bridge's ntfy user.

A sequence publishes under the id of its current generation, so a resubmit replaces its
notification in place, and the phone does not alert on that. A rise in priority clears that id and
publishes under the next generation's, which the phone alerts on as a new notification. It never
clears and republishes under one id: the Android app runs the two events concurrently, and the
clear can land last.
"""

import httpx2

from notification_bridge.api import Submission
from notification_bridge.config import Registration
from notification_bridge.render import ntfy_headers, ntfy_id
from notification_bridge.state import NtfyRow, State

# How much of a refusal's body that is not ntfy's JSON error the outcome quotes.
QUOTED = 200


class Ntfy:
    name = "ntfy"

    def __init__(self, client: httpx2.AsyncClient, state: State, icon_base_url: str) -> None:
        self._client = client
        self._state = state
        self._icon_base_url = icon_base_url

    def serves(self, registration: Registration) -> bool:
        return registration.ntfy is not None

    async def submit(self, registration: Registration, submission: Submission) -> str | None:
        """Publish the submission. None when ntfy took it, else why not."""
        sequence_id = submission.sequence_id
        if sequence_id is None:
            return await self._publish(registration, submission, None)

        row = self._state.ntfy_row(registration.name, sequence_id)
        generation = 0 if row is None else row.generation
        failures = []
        if row is not None and submission.priority > row.priority:
            # The publish goes ahead whatever the clear answers.
            failures.append(await self._clear(registration, sequence_id, generation))
            generation += 1
        published = await self._publish(
            registration, submission, ntfy_id(registration.name, sequence_id, generation)
        )
        failures.append(published)
        if published is None:
            self._state.put_ntfy_row(
                registration.name, sequence_id, NtfyRow(submission.priority, generation)
            )
        return "; ".join(failure for failure in failures if failure) or None

    async def dismiss(self, registration: Registration, sequence_id: str) -> str | None:
        """Clear the sequence's notification. None when ntfy took it, else why not.

        With no row (never stored, or pruned), it clears generation 0's id.
        """
        row = self._state.ntfy_row(registration.name, sequence_id)
        failure = await self._clear(registration, sequence_id, 0 if row is None else row.generation)
        if failure is None:
            self._state.delete_ntfy_row(registration.name, sequence_id)
        return failure

    async def _publish(
        self, registration: Registration, submission: Submission, sequence: str | None
    ) -> str | None:
        headers = ntfy_headers(
            icon_base_url=self._icon_base_url,
            icon=registration.ntfy.icon,
            priority=submission.priority,
            title=submission.title,
            sequence=sequence,
        )
        return await self._call(
            "publish",
            "POST",
            f"/{registration.ntfy.topic}",
            headers=headers,
            content=submission.message.encode(),
        )

    async def _clear(
        self, registration: Registration, sequence_id: str, generation: int
    ) -> str | None:
        sequence = ntfy_id(registration.name, sequence_id, generation)
        return await self._call("clear", "PUT", f"/{registration.ntfy.topic}/{sequence}/clear")

    async def _call(self, step: str, method: str, path: str, **request: object) -> str | None:
        try:
            response = await self._client.request(method, path, **request)
        except httpx2.HTTPError as error:
            return f"{step}: {type(error).__name__}: {error}"
        if response.is_success:
            return None
        return f"{step}: HTTP {response.status_code}: {_refusal(response)}"


def _refusal(response: httpx2.Response) -> str:
    """What ntfy said. ntfy answers a refusal with a JSON object whose `error` says why."""
    try:
        return str(response.json()["error"])
    except (ValueError, TypeError, KeyError):
        return response.text.strip()[:QUOTED] or response.reason_phrase
