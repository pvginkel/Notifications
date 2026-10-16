"""The API as the adapters code against it: its paths, fields, limits, status codes and result.

A change to what this file pins is a change to the contract the Jenkins, Alertmanager and KubeCoder
adapters follow.
"""

import pytest

PRIORITIES = ["min", "low", "default", "high", "max"]

URL = "https://jenkins.example.invalid/job/ansible/61/"
# 2048 characters, the most a url may have.
LONGEST_URL = "https://example.invalid/" + "u" * 2024


def test_the_paths_and_their_methods(bridge):
    assert bridge.client.get("/healthz").status_code == 200
    assert bridge.client.get("/metrics").status_code == 200
    assert bridge.submit(message="m").status_code == 200
    assert bridge.dismiss(sequence_id="s").status_code == 200
    assert bridge.client.get("/v1/submit").status_code == 405
    assert bridge.client.get("/v1/dismiss").status_code == 405


def test_a_submit_answers_one_outcome_per_configured_channel(bridge):
    response = bridge.submit(
        sequence_id="slice-061", priority="high", title="Ansible 061", message="**061** done"
    )

    assert (response.status_code, response.json()) == (200, {"ntfy": "ok"})


def test_a_dismiss_answers_one_outcome_per_configured_channel(bridge):
    response = bridge.dismiss(sequence_id="slice-061")

    assert (response.status_code, response.json()) == (200, {"ntfy": "ok"})


@pytest.mark.parametrize(
    "body",
    [
        {"message": "m"},
        {"message": "m", "sequence_id": "s" * 256},
        {"message": "m", "title": "t" * 250},
        {"message": "m", "title": ""},
        {"message": "m" * 4096},
        # The limit counts characters, not bytes or UTF-16 units.
        {"message": "\N{POLICE CARS REVOLVING LIGHT}" * 4096},
        *({"message": "m", "priority": priority} for priority in PRIORITIES),
        {"message": "m", "url": URL},
        {"message": "m", "url": LONGEST_URL},
        {"message": "m", "url": "http://example.invalid/caf%C3%A9?q=a%20b#top"},
        # A host without a dot is taken: Telegram's refusal of it costs only the button.
        {"message": "m", "url": "http://grafana/d/61"},
        {"message": "m", "url": "http://10.1.0.29:9090/graph"},
        # null is taken as omitted.
        {"message": "m", "sequence_id": None, "priority": None, "title": None, "url": None},
    ],
)
def test_a_submit_takes_every_field_within_its_limit(bridge, body):
    assert bridge.submit(body).status_code == 200


@pytest.mark.parametrize(
    ("body", "status", "error"),
    [
        ([], 400, "the body must be a JSON object"),
        ({}, 400, "message is missing"),
        ({"message": None}, 400, "message is missing"),
        ({"message": "m", "colour": "red"}, 400, "unknown fields: colour"),
        ({"message": 61}, 400, "message must be a string"),
        ({"message": " \n\t"}, 400, "message is blank"),
        ({"message": "m" * 4097}, 413, "message is 4097 characters, over the limit of 4096"),
        ({"message": "m", "sequence_id": ""}, 400, "sequence_id must be 1 to 256 characters"),
        (
            {"message": "m", "sequence_id": "s" * 257},
            400,
            "sequence_id must be 1 to 256 characters",
        ),
        ({"message": "m", "sequence_id": 61}, 400, "sequence_id must be a string"),
        (
            {"message": "m", "title": "t" * 251},
            400,
            "title is 251 characters, over the limit of 250",
        ),
        ({"message": "m", "title": ["t"]}, 400, "title must be a string"),
        *(
            (
                {"message": "m", "priority": priority},
                400,
                "priority must be one of min, low, default, high, max",
            )
            for priority in ["urgent", "HIGH", 4, ""]
        ),
        # A malformed body is refused as malformed, whatever the message's length.
        (
            {"message": "m" * 4097, "title": "t" * 251},
            400,
            "title is 251 characters, over the limit of 250",
        ),
        (b'{"message": "\\ud800"}', 400, "message holds a lone surrogate"),
        (b'{"message": "m", "sequence_id": "\\udfff"}', 400, "sequence_id holds a lone surrogate"),
        ({"message": "m", "url": 61}, 400, "url must be a string"),
        *(
            ({"message": "m", "url": url}, 400, "url must be an absolute http or https URL")
            for url in [
                "",
                "/job/ansible/61/",
                "jenkins.example.invalid/job/ansible/61/",
                "//jenkins.example.invalid/job/ansible/61/",
                "ftp://example.invalid/build.log",
                "tg://resolve?domain=invented_bot",
                "javascript:alert(1)",
                "https://",
                "https:///job/ansible/61/",
                "https://[::1/job",
            ]
        ),
        (
            {"message": "m", "url": "https://example.invalid/café"},
            400,
            "url must be ASCII, the rest percent-encoded",
        ),
        *(
            ({"message": "m", "url": url}, 400, "url holds whitespace or a control character")
            for url in [
                "https://example.invalid/a b",
                "https://example.invalid/a\tb",
                "https://example.invalid/\r\nX-Injected: 1",
                "https://example.invalid/\x00",
                "https://example.invalid/\x7f",
                " https://example.invalid/",
            ]
        ),
        (
            {"message": "m", "url": LONGEST_URL + "u"},
            400,
            "url is 2049 characters, over the limit of 2048",
        ),
        (b'{"message": "m", "url": "https://invented/\\ud800"}', 400, "url holds a lone surrogate"),
    ],
)
def test_a_submit_refuses_a_malformed_or_oversized_body(bridge, fake_ntfy, body, status, error):
    response = bridge.submit(body)

    assert (response.status_code, response.json()) == (status, {"error": error})
    assert fake_ntfy.calls == []


# A body nested past the interpreter's recursion limit is refused like any other malformed one.
DEEP = b'{"message": ' + b"[" * 200_000 + b"]" * 200_000 + b"}"


@pytest.mark.parametrize("body", [b"", b"{", b"\xff", pytest.param(DEEP, id="nested-too-deep")])
def test_a_submit_refuses_a_body_that_is_not_json(bridge, body):
    response = bridge.submit(body)

    assert response.status_code == 400
    assert response.json()["error"].startswith("the body is not JSON: ")


def test_a_dismiss_takes_a_sequence_id_within_its_limit(bridge):
    assert bridge.dismiss(sequence_id="s" * 256).status_code == 200


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({}, "sequence_id is missing"),
        ({"sequence_id": None}, "sequence_id is missing"),
        ({"sequence_id": ""}, "sequence_id must be 1 to 256 characters"),
        ({"sequence_id": "s" * 257}, "sequence_id must be 1 to 256 characters"),
        ({"sequence_id": "s", "message": "m"}, "unknown fields: message"),
        (["s"], "the body must be a JSON object"),
    ],
)
def test_a_dismiss_refuses_a_malformed_body(bridge, fake_ntfy, body, error):
    response = bridge.dismiss(body)

    assert (response.status_code, response.json()) == (400, {"error": error})
    assert fake_ntfy.calls == []


@pytest.mark.parametrize("path", ["/v1/submit", "/v1/dismiss"])
@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Bearer invented-unknown-token",
        "Basic invented-jenkins-token",
        "invented-jenkins-token",
    ],
)
def test_an_unknown_token_is_refused_before_the_body_is_read(
    bridge, fake_ntfy, path, authorization
):
    headers = {} if authorization is None else {"Authorization": authorization}

    response = bridge.client.post(path, content=b"not even JSON", headers=headers)

    assert (response.status_code, response.json()) == (401, {"error": "unknown bearer token"})
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert fake_ntfy.calls == []


def test_the_scheme_is_case_insensitive(bridge):
    response = bridge.client.post(
        "/v1/submit",
        json={"message": "m"},
        headers={"Authorization": "bearer invented-alerts-token"},
    )

    assert response.status_code == 200
