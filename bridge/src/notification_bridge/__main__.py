"""The `notification-bridge` command: load the configuration, then serve the API."""

import os
import sys

import uvicorn

from notification_bridge.app import create_app
from notification_bridge.config import ConfigError, load_config


def main() -> None:
    try:
        config = load_config(os.environ)
    except ConfigError as error:
        sys.exit(f"notification-bridge refuses to start: {error}")
    # The pod's Service reaches the bridge on the pod IP, so it binds every interface.
    uvicorn.run(create_app(), host="0.0.0.0", port=config.port, access_log=False)


if __name__ == "__main__":
    main()
