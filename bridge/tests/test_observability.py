import json
import logging
import subprocess
import sys

import pytest
from notification_bridge.logs import JsonLines


def test_every_registration_and_channel_counts_from_0(bridge):
    for registration in ["jenkins", "alerts"]:
        for outcome in ["ok", "error"]:
            labels = {"registration": registration, "channel": "ntfy", "outcome": outcome}
            assert bridge.metric("notifications_total", **labels) == 0


def test_submits_and_dismisses_are_counted_by_outcome_never_by_the_error_text(bridge, fake_ntfy):
    bridge.submit(sequence_id="slice-061", message="m")
    bridge.dismiss(sequence_id="slice-061")
    fake_ntfy.refuse("POST", 403, "forbidden")
    bridge.submit(message="m")
    fake_ntfy.go_down()
    bridge.submit(message="m")

    def count(outcome: str) -> float:
        return bridge.metric(
            "notifications_total", registration="jenkins", channel="ntfy", outcome=outcome
        )

    assert (count("ok"), count("error")) == (2, 2)
    other = {"registration": "alerts", "channel": "ntfy", "outcome": "ok"}
    assert bridge.metric("notifications_total", **other) == 0


def test_a_refused_request_is_not_counted(bridge):
    bridge.submit({"message": "m" * 4097})
    bridge.post("/v1/submit", {"message": "m"}, None)

    for outcome in ["ok", "error"]:
        labels = {"registration": "jenkins", "channel": "ntfy", "outcome": outcome}
        assert bridge.metric("notifications_total", **labels) == 0


def test_the_gauge_counts_the_open_rows(bridge):
    def open_rows(registration: str) -> float:
        return bridge.metric("notifications_open_rows", registration=registration, channel="ntfy")

    assert (open_rows("jenkins"), open_rows("alerts")) == (0, 0)

    bridge.submit(sequence_id="a", message="m")
    bridge.submit(sequence_id="b", message="m")
    bridge.submit(sequence_id="b", priority="max", message="m")
    bridge.submit(message="one-shot")
    bridge.submit(sequence_id="a", message="m", registration="alerts")
    assert (open_rows("jenkins"), open_rows("alerts")) == (2, 1)

    bridge.dismiss(sequence_id="a")
    assert (open_rows("jenkins"), open_rows("alerts")) == (1, 1)


@pytest.fixture
def events(caplog):
    """The bridge's log records, as (level, event, fields)."""
    caplog.set_level(logging.INFO, logger="notification_bridge")

    def read() -> list[tuple[str, str, dict]]:
        return [
            (record.levelname, record.getMessage(), record.fields)
            for record in caplog.records
            if record.name == "notification_bridge" and record.getMessage() != "prune"
        ]

    return read


def test_each_submit_and_dismiss_logs_one_line_with_each_channels_outcome(
    bridge, fake_ntfy, events
):
    bridge.submit(sequence_id="slice-061", message="m")
    bridge.submit(message="one-shot")
    fake_ntfy.refuse("PUT")
    bridge.dismiss(sequence_id="slice-061")

    assert events() == [
        (
            "INFO",
            "submit",
            {"registration": "jenkins", "sequence_id": "slice-061", "outcome": {"ntfy": "ok"}},
        ),
        (
            "INFO",
            "submit",
            {"registration": "jenkins", "sequence_id": None, "outcome": {"ntfy": "ok"}},
        ),
        (
            "WARNING",
            "dismiss",
            {
                "registration": "jenkins",
                "sequence_id": "slice-061",
                "outcome": {"ntfy": "error: clear: HTTP 403: forbidden"},
            },
        ),
    ]


def test_a_refused_request_logs_its_status_and_why(bridge, events):
    bridge.submit({"message": "m", "title": "t" * 251})
    bridge.post("/v1/dismiss", {"sequence_id": "s"}, None)

    assert events() == [
        (
            "WARNING",
            "submit",
            {
                "registration": "jenkins",
                "status": 400,
                "error": "title is 251 characters, over the limit of 250",
            },
        ),
        (
            "WARNING",
            "dismiss",
            {"registration": None, "status": 401, "error": "unknown bearer token"},
        ),
    ]


def test_the_prune_logs_what_it_removed(start_bridge, caplog):
    caplog.set_level(logging.INFO, logger="notification_bridge")

    start_bridge()

    assert [(r.getMessage(), r.fields) for r in caplog.records] == [("prune", {"removed": 0})]


def test_a_record_formats_as_one_json_object():
    record = logging.makeLogRecord(
        {
            "levelname": "WARNING",
            "msg": "submit",
            "fields": {"registration": "jenkins", "outcome": {"ntfy": "error: why\nnot"}},
        }
    )

    line = JsonLines().format(record)

    assert "\n" not in line
    assert json.loads(line) == {
        "level": "warning",
        "event": "submit",
        "registration": "jenkins",
        "outcome": {"ntfy": "error: why\nnot"},
    }


def test_the_served_log_configuration_writes_the_bridges_lines_as_json():
    script = (
        "import logging.config\n"
        "from notification_bridge.logs import log, log_config\n"
        "logging.config.dictConfig(log_config())\n"
        "log.info('dismiss', extra={'fields': {'registration': 'jenkins'}})\n"
        "log.debug('unseen', extra={'fields': {}})\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "level": "info",
        "event": "dismiss",
        "registration": "jenkins",
    }
