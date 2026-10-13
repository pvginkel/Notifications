"""The bridge's configuration, read once at start: the environment and the registrations file.

The container contract fixes the port and the paths. Each has a `BRIDGE_*` override so that tests
and a local run can point the bridge at their own files. A registration's string values may be a
whole-value `$NAME` placeholder, replaced by that environment variable. An invalid registration set
raises `ConfigError` rather than starting with part of it.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_PORT = "8090"
DEFAULT_REGISTRATIONS_FILE = "/etc/notification-bridge/registrations.yaml"
DEFAULT_STATE_DIR = "/var/lib/notification-bridge"
DEFAULT_ICONS_DIR = "/icons"

# The registration name keys the BLAKE2b hash of the ntfy id, and BLAKE2b keys are at most 64 bytes.
MAX_NAME_BYTES = 64
# ntfy's topic name rule (ntfy server/server.go, topicRegex).
TOPIC = re.compile(r"[-_A-Za-z0-9]{1,64}")
PLACEHOLDER = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


class ConfigError(Exception):
    """The configuration refuses the start. The message says what is wrong and where."""


@dataclass(frozen=True)
class NtfyChannel:
    topic: str
    icon: str  # a file name in the icons directory


@dataclass(frozen=True)
class TelegramChannel:
    bot_token: str
    chat_ids: tuple[str, ...]


@dataclass(frozen=True)
class Registration:
    name: str
    token: str
    ntfy: NtfyChannel | None
    telegram: TelegramChannel | None


@dataclass(frozen=True)
class Config:
    port: int
    ntfy_url: str
    ntfy_token: str
    icon_base_url: str
    state_dir: Path
    registrations: tuple[Registration, ...]


def load_config(env: Mapping[str, str]) -> Config:
    port = env.get("BRIDGE_PORT", DEFAULT_PORT)
    if not port.isdecimal():
        raise ConfigError(f"BRIDGE_PORT {port!r} is not a port number")
    return Config(
        port=int(port),
        ntfy_url=_required(env, "NTFY_URL"),
        ntfy_token=_required(env, "NTFY_TOKEN"),
        icon_base_url=_required(env, "ICON_BASE_URL"),
        state_dir=Path(env.get("BRIDGE_STATE_DIR", DEFAULT_STATE_DIR)),
        registrations=load_registrations(
            Path(env.get("BRIDGE_REGISTRATIONS_FILE", DEFAULT_REGISTRATIONS_FILE)),
            env,
            Path(env.get("BRIDGE_ICONS_DIR", DEFAULT_ICONS_DIR)),
        ),
    )


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise ConfigError(f"{name} is not set")
    return value


def load_registrations(
    path: Path, env: Mapping[str, str], icons_dir: Path
) -> tuple[Registration, ...]:
    try:
        document = yaml.safe_load(path.read_text())
    except OSError as error:
        raise ConfigError(f"cannot read the registrations file {path}: {error.strerror}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"the registrations file {path} is not valid YAML: {error}") from error

    if (
        not isinstance(document, dict)
        or set(document) != {"registrations"}
        or not isinstance(document["registrations"], dict)
        or not document["registrations"]
    ):
        raise ConfigError(
            f"the registrations file {path} must hold one non-empty 'registrations' mapping"
        )

    registrations = tuple(
        _registration(name, raw, env, icons_dir) for name, raw in document["registrations"].items()
    )

    owners: dict[str, str] = {}
    for registration in registrations:
        owner = owners.setdefault(registration.token, registration.name)
        if owner != registration.name:
            raise ConfigError(
                f"registration {registration.name!r}: its token is also registration {owner!r}'s"
            )
    return registrations


def _registration(
    name: object, raw: object, env: Mapping[str, str], icons_dir: Path
) -> Registration:
    if not isinstance(name, str) or not name:
        raise ConfigError(f"registration {name!r}: a name must be a non-empty string")

    def refuse(reason: str) -> ConfigError:
        return ConfigError(f"registration {name!r}: {reason}")

    def mapping(value: object, where: str, keys: set[str]) -> dict:
        if not isinstance(value, dict):
            raise refuse(f"{where} must be a mapping")
        unknown = sorted(str(key) for key in value if key not in keys)
        if unknown:
            raise refuse(f"{where} has unknown keys: {', '.join(unknown)}")
        return value

    def string(fields: dict, key: str, prefix: str = "") -> str:
        if key not in fields:
            raise refuse(f"{prefix}{key} is missing")
        value = fields[key]
        if not isinstance(value, str):
            raise refuse(f"{prefix}{key} must be a string")
        placeholder = PLACEHOLDER.fullmatch(value)
        if placeholder:
            variable = placeholder.group(1)
            if variable not in env:
                raise refuse(f"{prefix}{key} names ${variable}, which is not set")
            value = env[variable]
        if not value:
            raise refuse(f"{prefix}{key} is empty")
        return value

    name_bytes = len(name.encode())
    if name_bytes > MAX_NAME_BYTES:
        raise refuse(f"the name is {name_bytes} bytes, over the limit of {MAX_NAME_BYTES}")

    fields = mapping(raw, "the registration", {"token", "ntfy", "telegram"})
    token = string(fields, "token")
    if "ntfy" not in fields and "telegram" not in fields:
        raise refuse("it has neither an ntfy nor a telegram channel")

    ntfy = None
    if "ntfy" in fields:
        ntfy_fields = mapping(fields["ntfy"], "ntfy", {"topic", "icon"})
        topic = string(ntfy_fields, "topic", "ntfy.")
        if not TOPIC.fullmatch(topic):
            raise refuse(f"ntfy.topic {topic!r} is not a valid ntfy topic name")
        icon = string(ntfy_fields, "icon", "ntfy.")
        if Path(icon).name != icon or not (icons_dir / icon).is_file():
            raise refuse(f"ntfy.icon {icon!r} is not a file under {icons_dir}")
        ntfy = NtfyChannel(topic=topic, icon=icon)

    telegram = None
    if "telegram" in fields:
        telegram_fields = mapping(fields["telegram"], "telegram", {"botToken", "chatIds"})
        bot_token = string(telegram_fields, "botToken", "telegram.")
        chat_ids = tuple(
            chat.strip() for chat in string(telegram_fields, "chatIds", "telegram.").split(",")
        )
        if "" in chat_ids:
            raise refuse(
                "telegram.chatIds must list one or more chat ids, comma-separated, none empty"
            )
        if len(set(chat_ids)) != len(chat_ids):
            raise refuse("telegram.chatIds lists a chat more than once")
        telegram = TelegramChannel(bot_token=bot_token, chat_ids=chat_ids)

    return Registration(name=name, token=token, ntfy=ntfy, telegram=telegram)
