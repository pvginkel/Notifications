from datetime import timedelta

import pytest
from notification_bridge.render import ntfy_id
from notification_bridge.telegram import DISMISSED, SUPERSEDED

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
NO_BUTTONS = {"inline_keyboard": []}
EDITED = {**SENT, "reply_markup": NO_BUTTONS}

URL = "https://jenkins.example.invalid/job/ansible/61/"
# A host with no dot, whose button Telegram refuses.
DOTLESS = "http://grafana/d/61"
REFUSED_BUTTON = f"Bad Request: inline keyboard button URL '{DOTLESS}' is invalid: Wrong HTTP URL"


def open_button(url: str) -> dict:
    return {"inline_keyboard": [[{"text": "Open", "url": url}]]}


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
        ("editMessageText", {"chat_id": GROUP, "message_id": 1, "text": text, **EDITED}),
        ("editMessageText", {"chat_id": PRIVATE, "message_id": 2, "text": text, **EDITED}),
    ]
    assert fake_telegram.chats == {GROUP: {1: text}, PRIVATE: {2: text}}
    assert telegram_rows(bridge) == [
        ("slice-061", GROUP, 1, 2, ONE),
        ("slice-061", PRIVATE, 2, 2, ONE),
    ]


def test_an_update_that_changes_nothing_is_not_modified_and_counts_as_taken(
    bridge, fake_telegram, clock
):
    bridge.submit(sequence_id="slice-061", message="started")
    fake_telegram.sent()
    clock.advance(timedelta(hours=1))

    response = bridge.submit(sequence_id="slice-061", message="started")

    assert response.json() == {"ntfy": "ok", "telegram": "ok"}
    assert fake_telegram.sent() == [
        ("editMessageText", GROUP, 1),
        ("editMessageText", PRIVATE, 2),
    ]
    # The row is written again, so that a sequence resubmitted unchanged outlives the prune.
    assert [(row[2], row[4]) for row in telegram_rows(bridge)] == [(1, ONE), (2, ONE)]


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


def test_a_submit_with_a_url_sends_the_same_text_with_one_open_button(bridge, fake_telegram):
    submit = {"priority": "high", "title": "Ansible 061", "message": "**061** done"}
    bridge.submit(**submit, url=URL, registration="dm")
    bridge.submit(**submit, registration="dm")

    opened, bare = fake_telegram.calls
    assert (opened.method, bare.method) == ("sendMessage", "sendMessage")
    assert opened.fields == {**bare.fields, "reply_markup": open_button(URL)}
    assert "reply_markup" not in bare.fields
    assert fake_telegram.keyboards == {DM: {1: open_button(URL)["inline_keyboard"]}}


def test_a_resubmit_with_the_url_keeps_the_button_and_one_without_it_takes_it_off(
    bridge, fake_ntfy, fake_telegram
):
    bridge.submit(sequence_id="slice-061", message="started", url=URL)
    fake_telegram.sent()

    response = bridge.submit(sequence_id="slice-061", message="half way", url=URL)

    assert response.json() == {"ntfy": "ok", "telegram": "ok"}
    assert [(call.method, call.fields["reply_markup"]) for call in fake_telegram.calls] == [
        ("editMessageText", open_button(URL))
    ] * 2
    button = open_button(URL)["inline_keyboard"]
    assert fake_telegram.keyboards == {GROUP: {1: button}, PRIVATE: {2: button}}
    fake_telegram.sent()

    response = bridge.submit(sequence_id="slice-061", message="done")

    assert response.json() == {"ntfy": "ok", "telegram": "ok"}
    assert [(call.method, call.fields["reply_markup"]) for call in fake_telegram.calls] == [
        ("editMessageText", NO_BUTTONS)
    ] * 2
    assert fake_telegram.keyboards == {GROUP: {}, PRIVATE: {}}
    assert fake_telegram.chats == {GROUP: {1: "done"}, PRIVATE: {2: "done"}}
    assert [call.headers.get("X-Click") for call in fake_ntfy.calls] == [URL, URL, None]


def test_a_resubmit_that_only_drops_the_url_takes_the_button_off(bridge, fake_telegram):
    bridge.submit(sequence_id="slice-061", message="m", url=URL, registration="dm")
    unchanged = bridge.submit(sequence_id="slice-061", message="m", url=URL, registration="dm")

    assert unchanged.json() == {"telegram": "ok"}
    assert fake_telegram.keyboards == {DM: {1: open_button(URL)["inline_keyboard"]}}

    response = bridge.submit(sequence_id="slice-061", message="m", registration="dm")

    assert response.json() == {"telegram": "ok"}
    assert fake_telegram.keyboards == {DM: {}}
    assert fake_telegram.chats == {DM: {1: "m"}}


def test_a_rise_with_a_url_supersedes_without_the_button_and_sends_fresh_with_it(
    bridge, fake_telegram
):
    bridge.submit(
        sequence_id="slice-061", priority="low", message="started", url=URL, registration="dm"
    )
    fake_telegram.refuse("deleteMessage", TOO_OLD)
    fake_telegram.sent()

    response = bridge.submit(
        sequence_id="slice-061", priority="high", message="failed", url=URL, registration="dm"
    )

    assert response.json() == {"telegram": "ok"}
    assert [(call.method, call.fields.get("reply_markup")) for call in fake_telegram.calls] == [
        ("deleteMessage", None),
        ("editMessageText", NO_BUTTONS),
        ("sendMessage", open_button(URL)),
    ]
    text = "\N{LARGE ORANGE CIRCLE} failed"
    assert fake_telegram.chats == {DM: {1: SUPERSEDED, 2: text}}
    assert fake_telegram.keyboards == {DM: {2: open_button(URL)["inline_keyboard"]}}


def test_a_dismiss_that_edits_the_message_to_dismissed_takes_the_button_off(bridge, fake_telegram):
    bridge.submit(sequence_id="slice-061", message="started", url=URL, registration="dm")
    fake_telegram.refuse("deleteMessage", TOO_OLD)
    fake_telegram.sent()

    response = bridge.dismiss(sequence_id="slice-061", registration="dm")

    assert response.json() == {"telegram": "ok"}
    assert [(call.method, call.fields.get("reply_markup")) for call in fake_telegram.calls] == [
        ("deleteMessage", None),
        ("editMessageText", NO_BUTTONS),
    ]
    assert fake_telegram.chats == {DM: {1: DISMISSED}}
    assert fake_telegram.keyboards == {DM: {}}


def test_a_refused_button_is_dropped_and_the_message_sent_without_it(
    bridge, fake_ntfy, fake_telegram, caplog
):
    fake_telegram.refuse_buttons(REFUSED_BUTTON)

    with caplog.at_level("INFO", logger="notification_bridge"):
        response = bridge.submit(sequence_id="slice-061", message="m", url=DOTLESS)

    assert response.json() == {"ntfy": "ok", "telegram": "ok"}
    calls = fake_telegram.calls
    assert [(c.method, c.fields["chat_id"], c.fields.get("reply_markup")) for c in calls] == [
        ("sendMessage", GROUP, open_button(DOTLESS)),
        ("sendMessage", GROUP, None),
        ("sendMessage", PRIVATE, open_button(DOTLESS)),
        ("sendMessage", PRIVATE, None),
    ]
    assert calls[0].fields == {**calls[1].fields, "reply_markup": open_button(DOTLESS)}
    assert fake_telegram.chats == {GROUP: {1: "m"}, PRIVATE: {2: "m"}}
    assert fake_telegram.keyboards == {GROUP: {}, PRIVATE: {}}
    assert [row[1:3] for row in telegram_rows(bridge)] == [(GROUP, 1), (PRIVATE, 2)]
    [publish] = fake_ntfy.calls
    assert publish.headers["X-Click"] == DOTLESS
    assert [(r.levelname, r.getMessage(), r.fields) for r in caplog.records] == [
        (
            "WARNING",
            "button refused",
            {
                "registration": "jenkins",
                "sequence_id": "slice-061",
                "chat": chat,
                "error": f"send: HTTP 400: {REFUSED_BUTTON}",
            },
        )
        for chat in (GROUP, PRIVATE)
    ] + [
        (
            "INFO",
            "submit",
            {
                "registration": "jenkins",
                "sequence_id": "slice-061",
                "outcome": {"ntfy": "ok", "telegram": "ok"},
            },
        )
    ]
    labels = {"registration": "jenkins", "channel": "telegram"}
    assert bridge.metric("notifications_total", **labels, outcome="ok") == 1
    assert bridge.metric("notifications_total", **labels, outcome="error") == 0


def test_a_refused_button_on_an_edit_is_dropped_with_the_one_already_there(bridge, fake_telegram):
    bridge.submit(sequence_id="slice-061", message="started", url=URL, registration="dm")
    fake_telegram.refuse_buttons(REFUSED_BUTTON)
    fake_telegram.sent()

    response = bridge.submit(
        sequence_id="slice-061", message="done", url=DOTLESS, registration="dm"
    )

    assert response.json() == {"telegram": "ok"}
    assert [(call.method, call.fields["reply_markup"]) for call in fake_telegram.calls] == [
        ("editMessageText", open_button(DOTLESS)),
        ("editMessageText", NO_BUTTONS),
    ]
    assert fake_telegram.chats == {DM: {1: "done"}}
    assert fake_telegram.keyboards == {DM: {}}


def test_a_one_shot_whose_button_is_refused_logs_no_sequence_id(bridge, fake_telegram, caplog):
    fake_telegram.refuse_buttons(REFUSED_BUTTON)

    with caplog.at_level("INFO", logger="notification_bridge"):
        response = bridge.submit(message="m", url=DOTLESS, registration="dm")

    assert response.json() == {"telegram": "ok"}
    [refused] = [r for r in caplog.records if r.getMessage() == "button refused"]
    assert (refused.levelname, refused.fields["sequence_id"]) == ("WARNING", None)


@pytest.mark.parametrize(
    "fail",
    [
        pytest.param(lambda fake: fake.go_down(), id="transport"),
        pytest.param(lambda fake: fake.refuse("sendMessage", BLOCKED, status=403), id="403"),
        pytest.param(
            lambda fake: fake.refuse("sendMessage", "Too Many Requests: retry after 5", status=429),
            id="429",
        ),
        pytest.param(
            lambda fake: fake.refuse("sendMessage", "Internal Server Error", status=500), id="500"
        ),
        pytest.param(lambda fake: fake.answer("sendMessage", 400, "<html>Bad</html>"), id="html"),
    ],
)
def test_a_button_call_that_fails_but_as_telegrams_bad_request_is_not_made_again(
    bridge, fake_telegram, fail
):
    fail(fake_telegram)

    response = bridge.submit(message="m", url=URL, registration="dm")

    assert response.json()["telegram"].startswith(f"error: {DM}: send: ")
    [call] = fake_telegram.calls
    assert call.fields["reply_markup"] == open_button(URL)


def test_the_state_holds_no_title_no_message_text_and_no_url(bridge):
    bridge.submit(sequence_id="slice-061", title="Ansible 061", message="**061** done", url=URL)

    values = {str(value) for row in bridge.rows() for value in row.values()}
    assert not any(
        "Ansible" in value or "061** done" in value or "example.invalid" in value
        for value in values
    )


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
