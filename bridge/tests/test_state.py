import asyncio
from datetime import timedelta

import pytest
from notification_bridge.app import prune_daily
from notification_bridge.render import Priority
from notification_bridge.state import NtfyRow, State


@pytest.fixture
def state(tmp_path, clock):
    state = State(tmp_path / "state.db", clock)
    yield state
    state.close()


def test_the_prune_deletes_the_rows_last_updated_over_90_days_ago(state, clock):
    state.put_ntfy_row("jenkins", "old", NtfyRow(Priority.HIGH, 0))
    clock.advance(timedelta(days=2))
    state.put_ntfy_row("jenkins", "recent", NtfyRow(Priority.HIGH, 0))
    state.put_ntfy_row("alerts", "touched", NtfyRow(Priority.LOW, 0))
    clock.advance(timedelta(days=10))
    state.put_ntfy_row("alerts", "touched", NtfyRow(Priority.LOW, 1))
    clock.advance(timedelta(days=78, seconds=1))

    assert state.prune() == 1

    assert state.ntfy_row("jenkins", "old") is None
    assert state.ntfy_row("jenkins", "recent") == NtfyRow(Priority.HIGH, 0)
    assert state.ntfy_row("alerts", "touched") == NtfyRow(Priority.LOW, 1)


def test_the_prune_keeps_a_row_of_exactly_90_days(state, clock):
    state.put_ntfy_row("jenkins", "slice-061", NtfyRow(Priority.HIGH, 0))
    clock.advance(timedelta(days=90))

    assert state.prune() == 0


def test_the_open_rows_are_counted_per_registration_and_channel(state):
    state.put_ntfy_row("jenkins", "a", NtfyRow(Priority.HIGH, 0))
    state.put_ntfy_row("jenkins", "b", NtfyRow(Priority.HIGH, 0))
    state.put_ntfy_row("alerts", "a", NtfyRow(Priority.HIGH, 0))
    state.delete_ntfy_row("jenkins", "b")

    assert state.open_rows() == {("jenkins", "ntfy"): 1, ("alerts", "ntfy"): 1}


def test_the_state_survives_a_restart(tmp_path, clock):
    first = State(tmp_path / "state.db", clock)
    first.put_ntfy_row("jenkins", "slice-061", NtfyRow(Priority.MAX, 3))
    first.close()

    second = State(tmp_path / "state.db", clock)
    assert second.ntfy_row("jenkins", "slice-061") == NtfyRow(Priority.MAX, 3)
    second.close()


def test_the_prune_runs_at_once_and_then_daily(state, clock):
    state.put_ntfy_row("jenkins", "slice-061", NtfyRow(Priority.HIGH, 0))
    clock.advance(timedelta(days=89, hours=12))
    slept: list[tuple[float, bool]] = []

    class Stop(Exception):
        pass

    async def sleep(seconds: float) -> None:
        slept.append((seconds, state.ntfy_row("jenkins", "slice-061") is not None))
        if len(slept) == 2:
            raise Stop
        clock.advance(timedelta(seconds=seconds))

    with pytest.raises(Stop):
        asyncio.run(prune_daily(state, sleep))

    # 89.5 days old at the first prune, 90.5 at the next, a day later.
    assert slept == [(86400, True), (86400, False)]


def test_the_bridge_prunes_when_it_starts(start_bridge, tmp_path, clock):
    (tmp_path / "state").mkdir()
    state = State(tmp_path / "state" / "state.db", clock)
    state.put_ntfy_row("jenkins", "stale", NtfyRow(Priority.HIGH, 0))
    clock.advance(timedelta(days=60))
    state.put_ntfy_row("jenkins", "fresh", NtfyRow(Priority.HIGH, 0))
    state.close()
    clock.advance(timedelta(days=31))

    bridge = start_bridge()

    assert [row["sequence_id"] for row in bridge.rows()] == ["fresh"]
