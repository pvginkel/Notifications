from datetime import timedelta

import pytest
from notification_bridge.render import ntfy_id

# Invented chats and bots: a group of the `jenkins` registration's, a private chat of its, and the
# one chat of `dm`, which has no ntfy.
GROUP = "-100111"
PRIVATE = "222"
DM = "333"
JENKINS_BOT = "1000:invented-jenkins-bot"
DM_BOT = "2000:invented-dm-bot"

REGISTRATIONS = f"""
    registrations:
      jenkins:
        token: invented-jenkins-token
        ntfy: {{ topic: builds, icon: jenkins.png }}
        telegram: {{ botToken: "{JENKINS_BOT}", chatIds: "{GROUP}, {PRIVATE}" }}
      dm:
        token: invented-dm-token
        telegram: {{ botToken: "{DM_BOT}", chatIds: "{DM}" }}
"""

SENT = {"parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}

NOON = "2026-10-13T12:00:00+00:00"
ONE = "2026-10-13T13:00:00+00:00"

KICKED = "Forbidden: bot was kicked from the group chat"
BLOCKED = "Forbidden: bot was blocked by the user"
TOO_OLD = "Bad Request: message can't be deleted for everyone"


@pytest.fixture
def bridge(start_bridge):
    return start_bridge(REGISTRATIONS)


def telegram_rows(bridge) -> list[tuple[str, str, int, int, str]]:
    """Each Telegram row's sequence id, chat, message id, priority and `last_updated`."""
    return [
        (
            row["sequence_id"],
            row["chat_id"],
            row["message_id"],
            row["priority"],
            row["last_updated"],
        )
        for row in bridge.rows()
        if row["channel"] == "telegram"
    ]


def test_a_first_submit_sends_to_every_chat_and_writes_a_row_for_each(bridge, fake_telegram):
    response = bridge.submit(
        sequence_id="slice-061", priority="high", title="Ansible 061", message="**061** done"
    )

    assert response.json() == {"ntfy": "ok", "telegram": "ok"}
    text = "\N{LARGE ORANGE CIRCLE} <b>Ansible 061</b>\n<b>061</b> done"
    assert [(call.bot_token, call.method, call.fields) for call in fake_telegram.calls] == [
        (
            JENKINS_BOT,
            "sendMessage",
            {"chat_id": chat, "text": text, **SENT, "disable_notification": False},
        )
        for chat in (GROUP, PRIVATE)
    ]
    assert telegram_rows(bridge) == [
        ("slice-061", GROUP, 1, 4, NOON),
        ("slice-061", PRIVATE, 2, 4, NOON),
    ]
    assert all(row["generation"] is None for row in bridge.rows() if row["channel"] == "telegram")


@pytest.mark.parametrize(
    ("priority", "silent"),
    [("min", True), ("low", True), ("default", False), ("high", False), ("max", False)],
)
def test_low_and_min_are_sent_silently(bridge, fake_telegram, priority, silent):
    bridge.submit(priority=priority, message="m", registration="dm")

    [call] = fake_telegram.calls
    assert call.fields["disable_notification"] is silent


def test_a_registration_with_only_telegram_answers_for_telegram_alone_with_its_own_bot(
    bridge, fake_ntfy, fake_telegram
):
    response = bridge.submit(sequence_id="slice-061", message="m", registration="dm")

    assert response.json() == {"telegram": "ok"}
    assert fake_ntfy.calls == []
    [call] = fake_telegram.calls
    assert (call.bot_token, call.fields["chat_id"]) == (DM_BOT, DM)
    assert [row["channel"] for row in bridge.rows()] == ["telegram"]


def test_a_one_shot_goes_to_every_chat_and_leaves_no_row(bridge, fake_telegram):
    response = bridge.submit(priority="max", title="Disk full", message="/var at 99%")

    assert response.json() == {"ntfy": "ok", "telegram": "ok"}
    assert fake_telegram.sent() == [("sendMessage", GROUP, None), ("sendMessage", PRIVATE, None)]
    assert bridge.rows() == []


def test_an_update_at_the_same_or_a_lower_priority_edits_in_place_with_the_mark_following(
    bridge, fake_telegram, clock
):
    bridge.submit(sequence_id="slice-061", priority="high", title="Ansible 061", message="started")
    fake_telegram.sent()
    clock.advance(timedelta(hours=1))

    response = bridge.submit(
        sequence_id="slice-061", priority="low", title="Ansible 061", message="done"
    )

    assert response.json() == {"ntfy": "ok", "telegram": "ok"}
    text = "\N{LARGE BLUE CIRCLE} <b>Ansible 061</b>\ndone"
    assert [(call.method, call.fields) for call in fake_telegram.calls] == [
        ("editMessageText", {"chat_id": GROUP, "message_id": 1, "text": text, **SENT}),
        ("editMessageText", {"chat_id": PRIVATE, "message_id": 2, "text": text, **SENT}),
    ]
    assert fake_telegram.chats == {GROUP: {1: text}, PRIVATE: {2: text}}
    assert telegram_rows(bridge) == [
        ("slice-061", GROUP, 1, 2, ONE),
        ("slice-061", PRIVATE, 2, 2, ONE),
    ]


def test_an_update_that_changes_nothing_is_not_modified_and_counts_as_taken(bridge, fake_telegram):
    bridge.submit(sequence_id="slice-061", message="started")
    fake_telegram.sent()

    response = bridge.submit(sequence_id="slice-061", message="started")

    assert response.json() == {"ntfy": "ok", "telegram": "ok"}
    assert fake_telegram.sent() == [
        ("editMessageText", GROUP, 1),
        ("editMessageText", PRIVATE, 2),
    ]
    assert [row[2] for row in telegram_rows(bridge)] == [1, 2]


def test_a_rise_deletes_the_message_and_sends_it_fresh_so_that_it_notifies(
    bridge, fake_telegram, clock
):
    bridge.submit(sequence_id="slice-061", priority="low", title="Ansible 061", message="started")
    fake_telegram.sent()
    clock.advance(timedelta(hours=1))

    response = bridge.submit(
        sequence_id="slice-061", priority="max", title="Ansible 061", message="failed"
    )

    assert response.json() == {"ntfy": "ok", "telegram": "ok"}
    text = "\N{POLICE CARS REVOLVING LIGHT} <b>Ansible 061</b>\nfailed"
    assert [(call.method, call.fields) for call in fake_telegram.calls] == [
        ("deleteMessage", {"chat_id": GROUP, "message_id": 1}),
        ("sendMessage", {"chat_id": GROUP, "text": text, **SENT, "disable_notification": False}),
        ("deleteMessage", {"chat_id": PRIVATE, "message_id": 2}),
        ("sendMessage", {"chat_id": PRIVATE, "text": text, **SENT, "disable_notification": False}),
    ]
    assert fake_telegram.chats == {GROUP: {3: text}, PRIVATE: {4: text}}
    assert telegram_rows(bridge) == [
        ("slice-061", GROUP, 3, 5, ONE),
        ("slice-061", PRIVATE, 4, 5, ONE),
    ]


def test_a_rise_compares_with_the_stored_priority_after_a_lowering(bridge, fake_telegram):
    bridge.submit(sequence_id="slice-061", priority="high", message="failing", registration="dm")
    bridge.submit(sequence_id="slice-061", priority="min", message="acked", registration="dm")
    fake_telegram.sent()

    bridge.submit(sequence_id="slice-061", priority="default", message="again", registration="dm")

    assert fake_telegram.sent() == [("deleteMessage", DM, 1), ("sendMessage", DM, None)]


def test_a_rise_whose_delete_is_refused_supersedes_the_old_message_and_sends_fresh(
    bridge, fake_telegram
):
    bridge.submit(sequence_id="slice-061", priority="low", message="started")
    fake_telegram.sent()
    fake_telegram.refuse("deleteMessage", TOO_OLD, chat=PRIVATE)

    response = bridge.submit(sequence_id="slice-061", priority="high", message="failed")

    assert response.json() == {"ntfy": "ok", "telegram": "ok"}
    assert fake_telegram.sent() == [
        ("deleteMessage", GROUP, 1),
        ("sendMessage", GROUP, None),
        ("deleteMessage", PRIVATE, 2),
        ("editMessageText", PRIVATE, 2),
        ("sendMessage", PRIVATE, None),
    ]
    text = "\N{LARGE ORANGE CIRCLE} failed"
    assert fake_telegram.chats == {GROUP: {3: text}, PRIVATE: {2: "<i>Superseded.</i>", 4: text}}
    assert [row[1:4] for row in telegram_rows(bridge)] == [(GROUP, 3, 4), (PRIVATE, 4, 4)]


def test_a_rise_whose_take_down_fails_still_sends_and_reports_the_take_down(bridge, fake_telegram):
    bridge.submit(sequence_id="slice-061", priority="low", message="started", registration="dm")
    fake_telegram.refuse("deleteMessage", "Internal Server Error", status=500)
    fake_telegram.refuse("editMessageText", "Internal Server Error", status=500)

    response = bridge.submit(
        sequence_id="slice-061", priority="high", message="failed", registration="dm"
    )

    failure = "HTTP 500: Internal Server Error"
    assert response.json() == {"telegram": f"error: {DM}: delete: {failure}; {DM}: edit: {failure}"}
    assert [row[1:4] for row in telegram_rows(bridge)] == [(DM, 2, 4)]


def test_a_rise_retried_after_its_send_failed_finds_the_old_message_superseded(
    bridge, fake_telegram
):
    bridge.submit(sequence_id="slice-061", priority="low", message="started", registration="dm")
    fake_telegram.refuse("deleteMessage", TOO_OLD)
    fake_telegram.refuse("sendMessage", BLOCKED, status=403)

    response = bridge.submit(
        sequence_id="slice-061", priority="high", message="failed", registration="dm"
    )

    assert response.json() == {"telegram": f"error: {DM}: send: HTTP 403: {BLOCKED}"}
    fake_telegram.recover()
    fake_telegram.refuse("deleteMessage", TOO_OLD)
    fake_telegram.sent()

    response = bridge.submit(
        sequence_id="slice-061", priority="high", message="failed", registration="dm"
    )

    assert response.json() == {"telegram": "ok"}
    assert fake_telegram.sent() == [
        ("deleteMessage", DM, 1),
        ("editMessageText", DM, 1),
        ("sendMessage", DM, None),
    ]
    assert fake_telegram.chats == {
        DM: {1: "<i>Superseded.</i>", 2: "\N{LARGE ORANGE CIRCLE} failed"}
    }


def test_a_rise_after_a_delete_by_hand_sends_fresh(bridge, fake_telegram):
    bridge.submit(sequence_id="slice-061", priority="low", message="started", registration="dm")
    fake_telegram.delete_by_hand(DM, 1)
    fake_telegram.sent()

    response = bridge.submit(
        sequence_id="slice-061", priority="high", message="failed", registration="dm"
    )

    assert response.json() == {"telegram": "ok"}
    assert fake_telegram.sent() == [("deleteMessage", DM, 1), ("sendMessage", DM, None)]
    assert [row[1:4] for row in telegram_rows(bridge)] == [(DM, 2, 4)]


def test_an_update_after_a_delete_by_hand_sends_fresh_and_stores_the_new_id(bridge, fake_telegram):
    bridge.submit(sequence_id="slice-061", priority="high", message="started")
    fake_telegram.delete_by_hand(GROUP, 1)
    fake_telegram.sent()

    response = bridge.submit(sequence_id="slice-061", priority="high", message="half way")

    assert response.json() == {"ntfy": "ok", "telegram": "ok"}
    assert fake_telegram.sent() == [
        ("editMessageText", GROUP, 1),
        ("sendMessage", GROUP, None),
        ("editMessageText", PRIVATE, 2),
    ]
    text = "\N{LARGE ORANGE CIRCLE} half way"
    assert fake_telegram.chats == {GROUP: {3: text}, PRIVATE: {2: text}}
    assert [row[1:3] for row in telegram_rows(bridge)] == [(GROUP, 3), (PRIVATE, 2)]


def test_a_dismiss_deletes_the_message_in_each_chat_and_removes_the_rows(
    bridge, fake_ntfy, fake_telegram
):
    bridge.submit(sequence_id="slice-061", message="started")
    fake_ntfy.sent()
    fake_telegram.sent()

    response = bridge.dismiss(sequence_id="slice-061")

    assert response.json() == {"ntfy": "ok", "telegram": "ok"}
    assert fake_telegram.sent() == [("deleteMessage", GROUP, 1), ("deleteMessage", PRIVATE, 2)]
    assert fake_telegram.chats == {GROUP: {}, PRIVATE: {}}
    assert fake_ntfy.sent() == [
        ("PUT", f"/builds/{ntfy_id('jenkins', 'slice-061', 0)}/clear", None)
    ]
    assert bridge.rows() == []


def test_a_dismiss_whose_delete_is_refused_edits_the_message_to_dismissed(bridge, fake_telegram):
    bridge.submit(sequence_id="slice-061", message="started")
    fake_telegram.sent()
    fake_telegram.refuse("deleteMessage", TOO_OLD, chat=PRIVATE)

    response = bridge.dismiss(sequence_id="slice-061")

    assert response.json() == {"ntfy": "ok", "telegram": "ok"}
    assert fake_telegram.sent() == [
        ("deleteMessage", GROUP, 1),
        ("deleteMessage", PRIVATE, 2),
        ("editMessageText", PRIVATE, 2),
    ]
    assert fake_telegram.chats == {GROUP: {}, PRIVATE: {2: "<i>Dismissed.</i>"}}
    assert bridge.rows() == []


def test_a_dismiss_of_a_message_deleted_by_hand_is_taken(bridge, fake_telegram):
    bridge.submit(sequence_id="slice-061", message="started", registration="dm")
    fake_telegram.delete_by_hand(DM, 1)

    response = bridge.dismiss(sequence_id="slice-061", registration="dm")

    assert response.json() == {"telegram": "ok"}
    assert bridge.rows() == []


def test_a_dismiss_whose_delete_fails_finds_a_message_deleted_by_hand_gone(bridge, fake_telegram):
    bridge.submit(sequence_id="slice-061", message="started", registration="dm")
    fake_telegram.delete_by_hand(DM, 1)
    fake_telegram.refuse("deleteMessage", "Internal Server Error", status=500)
    fake_telegram.sent()

    response = bridge.dismiss(sequence_id="slice-061", registration="dm")

    assert response.json() == {"telegram": "ok"}
    assert fake_telegram.sent() == [("deleteMessage", DM, 1), ("editMessageText", DM, 1)]
    assert bridge.rows() == []


def test_a_dismiss_with_no_row_does_nothing_on_telegram_and_still_clears_on_ntfy(
    bridge, fake_ntfy, fake_telegram
):
    response = bridge.dismiss(sequence_id="never-stored")

    assert response.json() == {"ntfy": "ok", "telegram": "ok"}
    assert fake_telegram.calls == []
    assert fake_ntfy.sent() == [
        ("PUT", f"/builds/{ntfy_id('jenkins', 'never-stored', 0)}/clear", None)
    ]


def test_a_dismiss_that_cannot_take_the_message_down_reports_why_and_keeps_the_row(
    bridge, fake_telegram
):
    bridge.submit(sequence_id="slice-061", message="started")
    fake_telegram.refuse("deleteMessage", KICKED, chat=GROUP, status=403)
    fake_telegram.refuse("editMessageText", KICKED, chat=GROUP, status=403)

    response = bridge.dismiss(sequence_id="slice-061")

    assert response.json() == {
        "ntfy": "ok",
        "telegram": (
            f"error: {GROUP}: delete: HTTP 403: {KICKED}; {GROUP}: edit: HTTP 403: {KICKED}"
        ),
    }
    assert [row[1] for row in telegram_rows(bridge)] == [GROUP]

    fake_telegram.recover()
    fake_telegram.sent()
    bridge.dismiss(sequence_id="slice-061")

    assert fake_telegram.sent() == [("deleteMessage", GROUP, 1)]
    assert bridge.rows() == []


def test_with_two_chats_one_refusing_only_that_chat_reports_an_error(bridge, fake_telegram):
    fake_telegram.refuse("sendMessage", BLOCKED, chat=PRIVATE, status=403)

    response = bridge.submit(sequence_id="slice-061", message="m")

    assert response.json() == {
        "ntfy": "ok",
        "telegram": f"error: {PRIVATE}: send: HTTP 403: {BLOCKED}",
    }
    assert [row[1:3] for row in telegram_rows(bridge)] == [(GROUP, 1)]


def test_telegram_down_costs_neither_ntfy_nor_a_retry(bridge, fake_ntfy, fake_telegram):
    fake_telegram.go_down()

    response = bridge.submit(sequence_id="slice-061", message="m")

    refused = "send: ConnectError: [Errno 111] Connection refused"
    assert response.json() == {
        "ntfy": "ok",
        "telegram": f"error: {GROUP}: {refused}; {PRIVATE}: {refused}",
    }
    assert len(fake_telegram.calls) == 2
    assert len(fake_ntfy.calls) == 1
    assert [row["channel"] for row in bridge.rows()] == ["ntfy"]


def test_ntfy_down_costs_telegram_nothing(bridge, fake_ntfy, fake_telegram):
    fake_ntfy.go_down()

    response = bridge.submit(sequence_id="slice-061", message="m")

    assert response.json() == {
        "ntfy": "error: publish: ConnectError: [Errno 111] Connection refused",
        "telegram": "ok",
    }
    assert [row[1] for row in telegram_rows(bridge)] == [GROUP, PRIVATE]


def test_a_refused_update_keeps_the_row_as_it_was(bridge, fake_telegram, clock):
    bridge.submit(sequence_id="slice-061", priority="high", message="started", registration="dm")
    fake_telegram.refuse("editMessageText", "Too Many Requests: retry after 5", status=429)
    clock.advance(timedelta(hours=1))

    response = bridge.submit(
        sequence_id="slice-061", priority="low", message="done", registration="dm"
    )

    assert response.json() == {
        "telegram": f"error: {DM}: edit: HTTP 429: Too Many Requests: retry after 5"
    }
    assert telegram_rows(bridge) == [("slice-061", DM, 1, 4, NOON)]


def test_a_rise_whose_send_is_refused_rises_again_on_the_next_submit(bridge, fake_telegram):
    bridge.submit(sequence_id="slice-061", priority="low", message="started", registration="dm")
    fake_telegram.refuse("sendMessage", BLOCKED, status=403)

    response = bridge.submit(
        sequence_id="slice-061", priority="high", message="failed", registration="dm"
    )

    assert response.json() == {"telegram": f"error: {DM}: send: HTTP 403: {BLOCKED}"}
    assert [row[1:4] for row in telegram_rows(bridge)] == [(DM, 1, 2)]

    fake_telegram.recover()
    fake_telegram.sent()
    response = bridge.submit(
        sequence_id="slice-061", priority="high", message="failed", registration="dm"
    )

    assert response.json() == {"telegram": "ok"}
    assert fake_telegram.sent() == [("deleteMessage", DM, 1), ("sendMessage", DM, None)]
    assert [row[1:4] for row in telegram_rows(bridge)] == [(DM, 2, 4)]


def test_a_refusal_that_is_not_telegrams_json_is_quoted(bridge, fake_telegram):
    fake_telegram.answer("sendMessage", 502, "<html>Bad Gateway</html>")

    response = bridge.submit(message="m", registration="dm")

    assert response.json() == {"telegram": f"error: {DM}: send: HTTP 502: <html>Bad Gateway</html>"}


def test_every_send_and_edit_is_html_with_the_link_preview_disabled(bridge, fake_telegram):
    message = "see https://example.invalid/build/61"
    bridge.submit(sequence_id="a", priority="low", message=message)
    bridge.submit(sequence_id="a", priority="min", message=f"{message} again")
    fake_telegram.refuse("deleteMessage", TOO_OLD)
    bridge.submit(sequence_id="a", priority="max", message=message)
    bridge.dismiss(sequence_id="a")

    texts = [call for call in fake_telegram.calls if call.method != "deleteMessage"]
    assert [call.method for call in texts] == [
        "sendMessage",
        "sendMessage",
        "editMessageText",
        "editMessageText",
        "editMessageText",
        "sendMessage",
        "editMessageText",
        "sendMessage",
        "editMessageText",
        "editMessageText",
    ]
    assert all(
        call.fields["parse_mode"] == "HTML"
        and call.fields["link_preview_options"] == {"is_disabled": True}
        for call in texts
    )


def test_the_state_holds_no_title_and_no_message_text(bridge):
    bridge.submit(sequence_id="slice-061", title="Ansible 061", message="**061** done")

    values = {str(value) for row in bridge.rows() for value in row.values()}
    assert not any("Ansible" in value or "061** done" in value for value in values)


def test_telegrams_outcomes_are_counted_and_its_rows_gauged_per_chat(bridge, fake_telegram):
    def count(registration: str, outcome: str) -> float:
        labels = {"registration": registration, "channel": "telegram", "outcome": outcome}
        return bridge.metric("notifications_total", **labels)

    def open_rows(registration: str) -> float:
        labels = {"registration": registration, "channel": "telegram"}
        return bridge.metric("notifications_open_rows", **labels)

    assert [count(r, o) for r in ("jenkins", "dm") for o in ("ok", "error")] == [0, 0, 0, 0]
    assert (open_rows("jenkins"), open_rows("dm")) == (0, 0)

    bridge.submit(sequence_id="a", message="m")
    bridge.submit(sequence_id="b", message="m")
    fake_telegram.refuse("sendMessage", BLOCKED, chat=PRIVATE, status=403)
    bridge.submit(sequence_id="c", message="m")
    bridge.dismiss(sequence_id="a")

    assert (count("jenkins", "ok"), count("jenkins", "error")) == (3, 1)
    assert (open_rows("jenkins"), open_rows("dm")) == (3, 0)


def test_the_log_line_carries_telegrams_outcome(bridge, fake_telegram, caplog):
    fake_telegram.refuse("sendMessage", BLOCKED, chat=PRIVATE, status=403)

    with caplog.at_level("INFO", logger="notification_bridge"):
        bridge.submit(sequence_id="slice-061", message="m")

    [record] = [record for record in caplog.records if record.getMessage() == "submit"]
    assert (record.levelname, record.fields) == (
        "WARNING",
        {
            "registration": "jenkins",
            "sequence_id": "slice-061",
            "outcome": {"ntfy": "ok", "telegram": f"error: {PRIVATE}: send: HTTP 403: {BLOCKED}"},
        },
    )
