import asyncio
import base64
from datetime import timedelta

import httpx2
from notification_bridge.render import ntfy_id


def jenkins(sequence_id: str, generation: int) -> str:
    return ntfy_id("jenkins", sequence_id, generation)


def ntfy_row(priority: int, generation: int, last_updated: str, sequence_id: str = "slice-061"):
    return {
        "registration": "jenkins",
        "sequence_id": sequence_id,
        "channel": "ntfy",
        "chat_id": "",
        "message_id": None,
        "generation": generation,
        "priority": priority,
        "last_updated": last_updated,
    }


NOON = "2026-10-13T12:00:00+00:00"
ONE = "2026-10-13T13:00:00+00:00"
TWO = "2026-10-13T14:00:00+00:00"


def test_a_first_submit_publishes_under_generation_0_and_writes_the_row(bridge, fake_ntfy):
    bridge.submit(
        sequence_id="slice-061", priority="high", title="Ansible 061", message="**061** done"
    )

    [call] = fake_ntfy.calls
    assert (call.method, call.path, call.body) == ("POST", "/builds", "**061** done")
    assert {name: call.headers[name] for name in call.headers if name.startswith("x-")} == {
        "x-sequence-id": jenkins("slice-061", 0),
        "x-priority": "4",
        "x-title": f"=?utf-8?b?{base64.b64encode(b'Ansible 061').decode()}?=",
        "x-markdown": "yes",
        "x-icon": "https://icons.invalid/registration-icons/jenkins.png",
    }
    assert call.headers["Authorization"] == "Bearer tk_invented"
    assert bridge.rows() == [ntfy_row(4, 0, NOON)]


def test_a_submit_with_no_priority_publishes_at_default(bridge, fake_ntfy):
    bridge.submit(sequence_id="slice-061", message="m")

    [call] = fake_ntfy.calls
    assert call.headers["X-Priority"] == "3"


def test_a_resubmit_at_the_same_or_a_lower_priority_publishes_again_in_place(
    bridge, fake_ntfy, clock
):
    bridge.submit(sequence_id="slice-061", priority="high", message="started")
    clock.advance(timedelta(hours=1))
    bridge.submit(sequence_id="slice-061", priority="high", message="half way")
    assert bridge.rows() == [ntfy_row(4, 0, ONE)]
    clock.advance(timedelta(hours=1))
    bridge.submit(sequence_id="slice-061", priority="low", message="done")

    assert fake_ntfy.sent() == [("POST", "/builds", jenkins("slice-061", 0))] * 3
    assert bridge.rows() == [ntfy_row(2, 0, TWO)]


def test_a_rise_clears_the_notification_and_publishes_under_the_next_generation(
    bridge, fake_ntfy, clock
):
    bridge.submit(sequence_id="slice-061", priority="low", message="started")
    fake_ntfy.sent()
    clock.advance(timedelta(hours=1))

    response = bridge.submit(sequence_id="slice-061", priority="max", message="failed")

    assert response.json() == {"ntfy": "ok"}
    assert fake_ntfy.sent() == [
        ("PUT", f"/builds/{jenkins('slice-061', 0)}/clear", None),
        ("POST", "/builds", jenkins("slice-061", 1)),
    ]
    assert bridge.rows() == [ntfy_row(5, 1, ONE)]


def test_after_a_rise_an_update_stays_on_the_risen_id(bridge, fake_ntfy):
    bridge.submit(sequence_id="slice-061", priority="low", message="started")
    bridge.submit(sequence_id="slice-061", priority="high", message="failing")
    fake_ntfy.sent()

    bridge.submit(sequence_id="slice-061", priority="high", message="still failing")
    bridge.submit(sequence_id="slice-061", priority="default", message="recovering")

    assert fake_ntfy.sent() == [("POST", "/builds", jenkins("slice-061", 1))] * 2
    assert [row["generation"] for row in bridge.rows()] == [1]


def test_a_rise_compares_with_the_stored_priority_after_a_lowering(bridge, fake_ntfy):
    bridge.submit(sequence_id="slice-061", priority="high", message="failing")
    bridge.submit(sequence_id="slice-061", priority="min", message="acknowledged")
    fake_ntfy.sent()

    bridge.submit(sequence_id="slice-061", priority="default", message="failing again")

    assert fake_ntfy.sent() == [
        ("PUT", f"/builds/{jenkins('slice-061', 0)}/clear", None),
        ("POST", "/builds", jenkins("slice-061", 1)),
    ]


def test_each_rise_takes_the_next_generation(bridge, fake_ntfy):
    for priority in ["min", "low", "default", "high", "max"]:
        bridge.submit(sequence_id="slice-061", priority=priority, message=priority)

    published = [sequence for method, _, sequence in fake_ntfy.sent() if method == "POST"]
    assert published == [jenkins("slice-061", generation) for generation in range(5)]
    assert len(set(published)) == 5


def test_a_dismiss_clears_the_stored_generation_and_removes_the_row(bridge, fake_ntfy):
    bridge.submit(sequence_id="slice-061", message="started")
    fake_ntfy.sent()

    response = bridge.dismiss(sequence_id="slice-061")

    assert response.json() == {"ntfy": "ok"}
    assert fake_ntfy.sent() == [("PUT", f"/builds/{jenkins('slice-061', 0)}/clear", None)]
    assert bridge.rows() == []


def test_a_dismiss_after_a_rise_clears_the_risen_id(bridge, fake_ntfy):
    bridge.submit(sequence_id="slice-061", priority="low", message="started")
    bridge.submit(sequence_id="slice-061", priority="high", message="failed")
    fake_ntfy.sent()

    bridge.dismiss(sequence_id="slice-061")

    assert fake_ntfy.sent() == [("PUT", f"/builds/{jenkins('slice-061', 1)}/clear", None)]
    assert bridge.rows() == []


def test_a_dismiss_with_no_row_clears_generation_0(bridge, fake_ntfy):
    response = bridge.dismiss(sequence_id="never-stored")

    assert response.json() == {"ntfy": "ok"}
    assert fake_ntfy.sent() == [("PUT", f"/builds/{jenkins('never-stored', 0)}/clear", None)]
    assert bridge.rows() == []


def test_a_one_shot_publishes_with_no_id_and_leaves_no_row(bridge, fake_ntfy):
    response = bridge.submit(priority="max", title="Disk full", message="/var at 99%")

    assert response.json() == {"ntfy": "ok"}
    [call] = fake_ntfy.calls
    assert (call.method, call.path, call.sequence, call.headers["X-Priority"]) == (
        "POST",
        "/builds",
        None,
        "5",
    )
    assert bridge.rows() == []


def test_two_registrations_on_one_topic_publish_under_distinct_ids_with_their_own_icons(
    bridge, fake_ntfy
):
    bridge.submit(sequence_id="nightly", message="m", registration="jenkins")
    bridge.submit(sequence_id="nightly", message="m", registration="alerts")

    first, second = fake_ntfy.calls
    assert (first.path, second.path) == ("/builds", "/builds")
    assert first.sequence == ntfy_id("jenkins", "nightly", 0)
    assert second.sequence == ntfy_id("alerts", "nightly", 0)
    assert first.sequence != second.sequence
    assert first.headers["X-Icon"].endswith("/jenkins.png")
    assert second.headers["X-Icon"].endswith("/alerts.png")
    assert [(row["registration"], row["sequence_id"]) for row in bridge.rows()] == [
        ("alerts", "nightly"),
        ("jenkins", "nightly"),
    ]


def test_the_state_holds_no_title_and_no_message_text(bridge):
    bridge.submit(sequence_id="slice-061", title="Ansible 061", message="**061** done")

    values = {str(value) for row in bridge.rows() for value in row.values()}
    assert not any("Ansible" in value or "061** done" in value for value in values)


def test_a_refused_first_publish_reports_the_refusal_and_writes_no_row(bridge, fake_ntfy):
    fake_ntfy.refuse("POST", 403, "forbidden")

    response = bridge.submit(sequence_id="slice-061", message="m")

    assert (response.status_code, response.json()) == (
        200,
        {"ntfy": "error: publish: HTTP 403: forbidden"},
    )
    assert bridge.rows() == []


def test_ntfy_down_is_reported_and_not_retried(bridge, fake_ntfy):
    fake_ntfy.go_down()

    response = bridge.submit(sequence_id="slice-061", message="m")

    assert response.json() == {
        "ntfy": "error: publish: ConnectError: [Errno 111] Connection refused"
    }
    assert len(fake_ntfy.calls) == 1


def test_a_refusal_that_is_not_ntfys_json_is_quoted(bridge, fake_ntfy):
    fake_ntfy.answer("POST", 502, "<html>Bad Gateway</html>")

    response = bridge.submit(message="m")

    assert response.json() == {"ntfy": "error: publish: HTTP 502: <html>Bad Gateway</html>"}


def test_a_refused_update_keeps_the_row_as_it_was(bridge, fake_ntfy, clock):
    bridge.submit(sequence_id="slice-061", priority="high", message="started")
    fake_ntfy.refuse("POST")
    clock.advance(timedelta(hours=1))

    bridge.submit(sequence_id="slice-061", priority="low", message="done")

    assert bridge.rows() == [ntfy_row(4, 0, NOON)]


def test_a_rise_whose_clear_is_refused_still_publishes_and_reports_the_clear(bridge, fake_ntfy):
    bridge.submit(sequence_id="slice-061", priority="low", message="started")
    fake_ntfy.sent()
    fake_ntfy.refuse("PUT", 403, "forbidden")

    response = bridge.submit(sequence_id="slice-061", priority="high", message="failed")

    assert response.json() == {"ntfy": "error: clear: HTTP 403: forbidden"}
    assert fake_ntfy.sent() == [
        ("PUT", f"/builds/{jenkins('slice-061', 0)}/clear", None),
        ("POST", "/builds", jenkins("slice-061", 1)),
    ]
    assert [(row["priority"], row["generation"]) for row in bridge.rows()] == [(4, 1)]


def test_a_rise_whose_publish_is_refused_rises_again_on_the_next_submit(bridge, fake_ntfy):
    bridge.submit(sequence_id="slice-061", priority="low", message="started")
    fake_ntfy.refuse("POST", 500, "internal server error")

    response = bridge.submit(sequence_id="slice-061", priority="high", message="failed")

    assert response.json() == {"ntfy": "error: publish: HTTP 500: internal server error"}
    assert [(row["priority"], row["generation"]) for row in bridge.rows()] == [(2, 0)]

    fake_ntfy.recover()
    fake_ntfy.sent()
    bridge.submit(sequence_id="slice-061", priority="high", message="failed")

    assert fake_ntfy.sent() == [
        ("PUT", f"/builds/{jenkins('slice-061', 0)}/clear", None),
        ("POST", "/builds", jenkins("slice-061", 1)),
    ]


def test_a_rise_with_ntfy_down_reports_both_steps(bridge, fake_ntfy):
    bridge.submit(sequence_id="slice-061", priority="low", message="started")
    fake_ntfy.go_down()

    response = bridge.submit(sequence_id="slice-061", priority="high", message="failed")

    refused = "ConnectError: [Errno 111] Connection refused"
    assert response.json() == {"ntfy": f"error: clear: {refused}; publish: {refused}"}


def test_a_refused_dismiss_keeps_the_row_for_the_next_dismiss(bridge, fake_ntfy):
    bridge.submit(sequence_id="slice-061", priority="low", message="started")
    bridge.submit(sequence_id="slice-061", priority="high", message="failed")
    fake_ntfy.refuse("PUT")
    fake_ntfy.sent()

    response = bridge.dismiss(sequence_id="slice-061")

    assert response.json() == {"ntfy": "error: clear: HTTP 403: forbidden"}
    assert len(bridge.rows()) == 1

    fake_ntfy.recover()
    bridge.dismiss(sequence_id="slice-061")

    assert [path for _, path, _ in fake_ntfy.sent()] == [
        f"/builds/{jenkins('slice-061', 1)}/clear"
    ] * 2
    assert bridge.rows() == []


def test_two_submits_of_one_sequence_at_once_take_turns(bridge, fake_ntfy):
    async def submit_both():
        transport = httpx2.ASGITransport(app=bridge.app)
        headers = bridge.authorization("jenkins")
        async with httpx2.AsyncClient(transport=transport, base_url="http://bridge") as client:
            await asyncio.gather(
                *(
                    client.post(
                        "/v1/submit",
                        json={"sequence_id": "slice-061", "priority": priority, "message": "m"},
                        headers=headers,
                    )
                    for priority in ["low", "high"]
                )
            )

    asyncio.run(submit_both())

    # The second submit read the first one's row, so it rose.
    assert fake_ntfy.sent() == [
        ("POST", "/builds", jenkins("slice-061", 0)),
        ("PUT", f"/builds/{jenkins('slice-061', 0)}/clear", None),
        ("POST", "/builds", jenkins("slice-061", 1)),
    ]
