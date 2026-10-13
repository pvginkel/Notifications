from pathlib import Path

import pytest
from notification_bridge.config import (
    ConfigError,
    NtfyChannel,
    Registration,
    TelegramChannel,
    load_config,
)

NTFY = "ntfy: { topic: t, icon: jenkins.png }"

REGISTRATIONS = """
    registrations:
      jenkins:
        token: $JENKINS_TOKEN
        ntfy: { topic: jenkins, icon: jenkins.png }
        telegram: { botToken: $JENKINS_TELEGRAM_BOT_TOKEN, chatIds: $JENKINS_TELEGRAM_CHAT_IDS }
      alertmanager:
        token: $ALERTMANAGER_TOKEN
        ntfy: { topic: alerts, icon: alerts.png }
      operator:
        token: invented-operator-token
        telegram: { botToken: $OPERATOR_TELEGRAM_BOT_TOKEN, chatIds: "1003" }
"""

SECRETS = {
    "JENKINS_TOKEN": "invented-jenkins-token",
    "JENKINS_TELEGRAM_BOT_TOKEN": "invented-jenkins-bot-token",
    "JENKINS_TELEGRAM_CHAT_IDS": "1001, -1002",
    "ALERTMANAGER_TOKEN": "invented-alertmanager-token",
    "OPERATOR_TELEGRAM_BOT_TOKEN": "invented-operator-bot-token",
}


def test_reads_the_registrations_and_substitutes_their_placeholders(make_env):
    config = load_config(make_env(REGISTRATIONS, **SECRETS))

    assert config.registrations == (
        Registration(
            name="jenkins",
            token="invented-jenkins-token",
            ntfy=NtfyChannel(topic="jenkins", icon="jenkins.png"),
            telegram=TelegramChannel(
                bot_token="invented-jenkins-bot-token", chat_ids=("1001", "-1002")
            ),
        ),
        Registration(
            name="alertmanager",
            token="invented-alertmanager-token",
            ntfy=NtfyChannel(topic="alerts", icon="alerts.png"),
            telegram=None,
        ),
        Registration(
            name="operator",
            token="invented-operator-token",
            ntfy=None,
            telegram=TelegramChannel(bot_token="invented-operator-bot-token", chat_ids=("1003",)),
        ),
    )


def test_the_container_contract_is_the_default(make_env):
    env = make_env(REGISTRATIONS, **SECRETS)
    del env["BRIDGE_STATE_DIR"]

    config = load_config(env)

    assert config.port == 8090
    assert config.state_dir == Path("/var/lib/notification-bridge")
    assert (config.ntfy_url, config.ntfy_token, config.icon_base_url) == (
        "http://ntfy.invalid",
        "tk_invented",
        "https://icons.invalid/registration-icons",
    )


def test_the_registrations_file_defaults_to_the_contract_path(make_env):
    env = make_env(REGISTRATIONS, **SECRETS)
    del env["BRIDGE_REGISTRATIONS_FILE"]

    with pytest.raises(ConfigError, match="registrations file /etc/notification-bridge/"):
        load_config(env)


def test_icons_default_to_the_contract_directory(make_env):
    env = make_env(REGISTRATIONS, **SECRETS)
    del env["BRIDGE_ICONS_DIR"]

    with pytest.raises(ConfigError, match="is not a file under /icons$"):
        load_config(env)


def test_the_port_and_the_paths_are_overridable(make_env, tmp_path):
    config = load_config(make_env(REGISTRATIONS, BRIDGE_PORT="18090", **SECRETS))

    assert config.port == 18090
    assert config.state_dir == tmp_path / "state"


def test_two_registrations_may_share_a_topic(make_env):
    config = load_config(
        make_env("""
            registrations:
              kubecoder: { token: invented-a, ntfy: { topic: kubecoder, icon: jenkins.png } }
              kubecoder-dev: { token: invented-b, ntfy: { topic: kubecoder, icon: alerts.png } }
        """)
    )

    assert [(r.name, r.ntfy.topic) for r in config.registrations] == [
        ("kubecoder", "kubecoder"),
        ("kubecoder-dev", "kubecoder"),
    ]


def test_a_name_of_64_bytes_starts(make_env):
    name = "é" * 32

    config = load_config(make_env(f"registrations: {{ {name}: {{ token: invented, {NTFY} }} }}"))

    assert config.registrations[0].name == name


@pytest.mark.parametrize("variable", ["NTFY_URL", "NTFY_TOKEN", "ICON_BASE_URL"])
def test_refuses_without_a_contract_variable(make_env, variable):
    env = make_env(REGISTRATIONS, **SECRETS)
    del env[variable]

    with pytest.raises(ConfigError) as refusal:
        load_config(env)

    assert str(refusal.value) == f"{variable} is not set"


def test_refuses_a_port_that_is_not_a_number(make_env):
    with pytest.raises(ConfigError) as refusal:
        load_config(make_env(REGISTRATIONS, BRIDGE_PORT="http", **SECRETS))

    assert str(refusal.value) == "BRIDGE_PORT 'http' is not a port number"


@pytest.mark.parametrize(
    ("registrations", "reason"),
    [
        pytest.param(
            "a: { token: invented }",
            "registration 'a': it has neither an ntfy nor a telegram channel",
            id="neither channel",
        ),
        pytest.param(
            f"a: {{ token: invented, {NTFY} }}\n  b: {{ token: invented, {NTFY} }}",
            "registration 'b': its token is also registration 'a''s",
            id="duplicate token",
        ),
        pytest.param(
            f"{'n' * 65}: {{ token: invented, {NTFY} }}",
            f"registration '{'n' * 65}': the name is 65 bytes, over the limit of 64",
            id="name over 64 bytes",
        ),
        pytest.param(
            f"{'é' * 33}: {{ token: invented, {NTFY} }}",
            f"registration '{'é' * 33}': the name is 66 bytes, over the limit of 64",
            id="name over 64 bytes in 33 characters",
        ),
        pytest.param(
            "a: { token: invented, ntfy: { topic: t, icon: missing.png } }",
            "registration 'a': ntfy.icon 'missing.png' is not a file under {icons}",
            id="icon missing",
        ),
        pytest.param(
            "a: { token: invented, ntfy: { topic: t, icon: ../registrations.yaml } }",
            "registration 'a': ntfy.icon '../registrations.yaml' is not a file under {icons}",
            id="icon outside the icons directory",
        ),
        pytest.param(
            f"a: {{ token: $UNSET_TOKEN, {NTFY} }}",
            "registration 'a': token names $UNSET_TOKEN, which is not set",
            id="unset token variable",
        ),
        pytest.param(
            "a: { token: invented, telegram: { botToken: $UNSET_BOT, chatIds: '1' } }",
            "registration 'a': telegram.botToken names $UNSET_BOT, which is not set",
            id="unset bot token variable",
        ),
        pytest.param(
            "a: { token: invented, telegram: { botToken: invented, chatIds: $UNSET_CHATS } }",
            "registration 'a': telegram.chatIds names $UNSET_CHATS, which is not set",
            id="unset chat ids variable",
        ),
        pytest.param(
            "a: { token: invented, telegram: { botToken: invented, chatIds: '1,,2' } }",
            "registration 'a': telegram.chatIds must list one or more chat ids, "
            "comma-separated, none empty",
            id="empty chat id",
        ),
        pytest.param(
            "a: { token: invented, telegram: { botToken: invented, chatIds: '1, 1' } }",
            "registration 'a': telegram.chatIds lists a chat more than once",
            id="chat listed twice",
        ),
        pytest.param(
            "a: { token: invented, telegram: { botToken: invented, chatIds: 1 } }",
            "registration 'a': telegram.chatIds must be a string",
            id="chat ids not a string",
        ),
        pytest.param(
            "a: { token: invented, ntfy: { topic: 'no spaces', icon: jenkins.png } }",
            "registration 'a': ntfy.topic 'no spaces' is not a valid ntfy topic name",
            id="invalid topic",
        ),
        pytest.param(
            f"a: {{ {NTFY} }}",
            "registration 'a': token is missing",
            id="token missing",
        ),
        pytest.param(
            f"a: {{ token: '', {NTFY} }}",
            "registration 'a': token is empty",
            id="token empty",
        ),
        pytest.param(
            f"a: {{ token: invented, tokn: invented, {NTFY} }}",
            "registration 'a': the registration has unknown keys: tokn",
            id="unknown key",
        ),
        pytest.param(
            "a: { token: invented, ntfy: jenkins }",
            "registration 'a': ntfy must be a mapping",
            id="channel not a mapping",
        ),
    ],
)
def test_refuses_an_invalid_registration(make_env, icons_dir, registrations, reason):
    env = make_env(f"registrations:\n  {registrations}\n")

    with pytest.raises(ConfigError) as refusal:
        load_config(env)

    assert str(refusal.value) == reason.replace("{icons}", str(icons_dir))


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("", id="empty"),
        pytest.param("registrations: {}", id="no registration"),
        pytest.param("registrations: [jenkins]", id="registrations not a mapping"),
        pytest.param("registrations: { a: { token: x } }\nextra: 1", id="unknown top-level key"),
    ],
)
def test_refuses_a_registrations_file_of_the_wrong_shape(make_env, document):
    env = make_env(document)

    with pytest.raises(ConfigError) as refusal:
        load_config(env)

    assert str(refusal.value) == (
        f"the registrations file {env['BRIDGE_REGISTRATIONS_FILE']} "
        "must hold one non-empty 'registrations' mapping"
    )


def test_refuses_a_registrations_file_that_is_not_yaml(make_env):
    with pytest.raises(ConfigError, match="is not valid YAML"):
        load_config(make_env("registrations: {"))
