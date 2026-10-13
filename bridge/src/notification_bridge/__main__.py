"""The `notification-bridge` command: load the configuration, then serve the API."""

import os
import sys

import uvicorn

from notification_bridge.app import create_app
from notification_bridge.config import ConfigError, load_config
from notification_bridge.logs import log_config


def main() -> None:
    try:
        config = load_config(os.environ)
    except ConfigError as error:
        sys.exit(f"notification-bridge refuses to start: {error}")
    # The pod's Service reaches the bridge on the pod IP, so it binds every interface.
    uvicorn.run(
        create_app(config),
        host="0.0.0.0",
        port=config.port,
        access_log=False,
        log_config=log_config(),
    )


if __name__ == "__main__":
    main()
