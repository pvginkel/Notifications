import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

# Invented values only: this repo is public.
CONTRACT_ENV = {
    "NTFY_URL": "http://ntfy.invalid",
    "NTFY_TOKEN": "tk_invented",
    "ICON_BASE_URL": "https://icons.invalid/registration-icons",
}


@pytest.fixture
def icons_dir(tmp_path: Path) -> Path:
    icons = tmp_path / "icons"
    icons.mkdir()
    for name in ("jenkins.png", "alerts.png"):
        (icons / name).write_bytes(b"invented icon")
    return icons


@pytest.fixture
def make_env(tmp_path: Path, icons_dir: Path) -> Callable[..., dict[str, str]]:
    """Write a registrations file and return an environment that points the bridge at it."""

    def make(registrations: str, **variables: str) -> dict[str, str]:
        path = tmp_path / "registrations.yaml"
        path.write_text(textwrap.dedent(registrations))
        return {
            **CONTRACT_ENV,
            "BRIDGE_REGISTRATIONS_FILE": str(path),
            "BRIDGE_ICONS_DIR": str(icons_dir),
            "BRIDGE_STATE_DIR": str(tmp_path / "state"),
            **variables,
        }

    return make
