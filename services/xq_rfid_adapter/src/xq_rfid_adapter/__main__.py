"""Command-line entry point for the RFID adapter."""

from __future__ import annotations

import argparse
import sqlite3
import ssl
import sys
from pathlib import Path

from .api import create_server
from .config import AdapterConfig, ConfigError, load_config, load_secret
from .drivers.fake import FakeDriver
from .queue import DeviceQueue, QueueError
from .store import OperationStore, StoreError


def _replay_path(sqlite_path: Path) -> Path:
    return sqlite_path.with_name(sqlite_path.name + ".replay")


def _create_runtime_service(config: AdapterConfig) -> DeviceQueue:
    drivers = {}
    capabilities = {}
    for device_id, device in config.devices.items():
        if device.driver != "fake" or config.production:
            raise ConfigError("configured RFID driver is unavailable")
        driver = FakeDriver()
        drivers[device_id] = driver
        capabilities[device_id] = driver.capabilities
    if not drivers:
        raise ConfigError("at least one RFID device is required")
    store = OperationStore(config.sqlite_path)
    try:
        return DeviceQueue(store, drivers, capabilities=capabilities)
    except BaseException:
        for driver in drivers.values():
            driver.close()
        store.close()
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Authenticated XQ RFID adapter")
    parser.add_argument("command", nargs="?", choices=("serve",), help="serve the adapter API")
    parser.add_argument("--config", type=Path, help="path to strict JSON configuration")
    parser.add_argument(
        "--check-config", action="store_true",
        help="validate configuration and secret without binding or creating SQLite",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None and not arguments.check_config:
        parser.error("serve or --check-config is required")
    if arguments.config is None:
        parser.error("--config is required")
    try:
        config = load_config(arguments.config)
        secret = load_secret()
    except ConfigError as error:
        parser.exit(2, f"configuration error: {error}\n")
    if arguments.check_config:
        print("configuration is valid")
        return 0

    server = None
    service = None
    try:
        service = _create_runtime_service(config)
        server = create_server(
            (config.bind.host, config.bind.port),
            service,
            secret,
            _replay_path(config.sqlite_path),
            frozenset(config.devices),
        )
        if config.tls is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(config.tls.cert_file, config.tls.key_file)
            server.socket = context.wrap_socket(server.socket, server_side=True)
        server.serve_forever()
    except (ConfigError, OSError, QueueError, StoreError, sqlite3.Error, ssl.SSLError):
        parser.exit(2, "configuration error: server configuration is invalid\n")
    except KeyboardInterrupt:
        return 0
    finally:
        if server is not None:
            server.server_close()
        if service is not None:
            service.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
