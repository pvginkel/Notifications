import pytest
import uvicorn
from notification_bridge.__main__ import main
from notification_bridge.app import create_app
from starlette.testclient import TestClient


def test_healthz_answers_ok():
    response = TestClient(create_app()).get("/healthz")

    assert (response.status_code, response.text) == (200, "ok")


@pytest.fixture
def served(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: calls.append(options))
    return calls


def test_starts_on_the_contract_port(make_env, monkeypatch, served):
    env = make_env(
        "registrations: { a: { token: invented, ntfy: { topic: t, icon: jenkins.png } } }"
    )
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("BRIDGE_PORT", raising=False)

    main()

    assert served == [{"host": "0.0.0.0", "port": 8090, "access_log": False}]


def test_refuses_to_start_naming_the_registration_and_the_reason(make_env, monkeypatch, served):
    for name, value in make_env("registrations: { lonely: { token: invented } }").items():
        monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit) as refusal:
        main()

    assert refusal.value.code == (
        "notification-bridge refuses to start: "
        "registration 'lonely': it has neither an ntfy nor a telegram channel"
    )
    assert served == []
