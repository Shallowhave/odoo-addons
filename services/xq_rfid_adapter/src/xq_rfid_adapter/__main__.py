"""Command-line entry point for the RFID adapter."""

from __future__ import annotations

import argparse
import ssl
import sys
from pathlib import Path

from .api import create_server
from .config import ConfigError, load_config, load_secret


class _UnavailableService:
    """Fail-closed service boundary until Tasks 8-9 supply runtime services."""

    def _unavailable(self, *args, **kwargs):
        del args, kwargs
        raise RuntimeError("service unavailable")

    test_connection = _unavailable
    get_device = _unavailable
    submit_operation = _unavailable
    get_operation = _unavailable


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

    server = create_server(
        (config.bind.host, config.bind.port),
        _UnavailableService(),
        secret,
        config.sqlite_path,
        frozenset(config.devices),
    )
    if config.tls is not None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(config.tls.cert_file, config.tls.key_file)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
