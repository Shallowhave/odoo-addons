"""Strict, immutable adapter configuration and secret loading."""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


class ConfigError(ValueError):
    """A safe configuration error that never contains secret material."""


@dataclass(frozen=True, slots=True)
class BindConfig:
    host: str
    port: int


@dataclass(frozen=True, slots=True)
class TlsConfig:
    cert_file: Path
    key_file: Path


@dataclass(frozen=True, slots=True)
class DeviceConfig:
    driver: str


@dataclass(frozen=True, slots=True)
class AdapterConfig:
    bind: BindConfig
    sqlite_path: Path
    production: bool
    tls: TlsConfig | None
    devices: Mapping[str, DeviceConfig]


_TOP_LEVEL_KEYS = frozenset({"bind", "sqlite_path", "production", "tls", "devices"})
_BIND_KEYS = frozenset({"host", "port"})
_TLS_KEYS = frozenset({"cert_file", "key_file"})
_DEVICE_KEYS = frozenset({"driver"})
_IDENTIFIER_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _object(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a JSON object")
    return value


def _exact_keys(value: dict, allowed: frozenset[str], name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"{name} contains unknown keys")


def _safe_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ConfigError(f"{name} is invalid")
    if any(character not in _IDENTIFIER_CHARS for character in value):
        raise ConfigError(f"{name} is invalid")
    return value


def _resolve_config_path(config_file: Path, value: object, name: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ConfigError(f"{name} must be a non-empty path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = config_file.parent / path
    return path.resolve(strict=False)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("JSON objects must not contain duplicate keys")
        result[key] = value
    return result


def load_config(path: str | os.PathLike[str]) -> AdapterConfig:
    config_file = Path(path).expanduser().resolve(strict=True)
    try:
        raw = json.loads(
            config_file.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigError("configuration file is unreadable or invalid") from error
    root = _object(raw, "configuration")
    _exact_keys(root, _TOP_LEVEL_KEYS, "configuration")
    if set(root) != _TOP_LEVEL_KEYS:
        raise ConfigError("configuration is missing required keys")

    bind_raw = _object(root["bind"], "bind")
    _exact_keys(bind_raw, _BIND_KEYS, "bind")
    if set(bind_raw) != _BIND_KEYS:
        raise ConfigError("bind is missing required keys")
    host = bind_raw["host"]
    port = bind_raw["port"]
    if not isinstance(host, str) or not host:
        raise ConfigError("bind host is invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ConfigError("bind host must be a fixed IP address") from error
    if type(port) is not int or not 0 <= port <= 65535:
        raise ConfigError("bind port is invalid")

    production = root["production"]
    if type(production) is not bool:
        raise ConfigError("production must be a boolean")

    tls_raw = root["tls"]
    tls = None
    if tls_raw is not None:
        tls_object = _object(tls_raw, "tls")
        _exact_keys(tls_object, _TLS_KEYS, "tls")
        if set(tls_object) != _TLS_KEYS:
            raise ConfigError("tls is missing required keys")
        tls = TlsConfig(
            cert_file=_resolve_config_path(config_file, tls_object["cert_file"], "tls cert_file"),
            key_file=_resolve_config_path(config_file, tls_object["key_file"], "tls key_file"),
        )
    if not address.is_loopback and tls is None:
        raise ConfigError("plaintext TCP bind must use loopback")

    devices_raw = _object(root["devices"], "devices")
    devices: dict[str, DeviceConfig] = {}
    for raw_device_id, raw_device in devices_raw.items():
        device_id = _safe_identifier(raw_device_id, "device id")
        device_object = _object(raw_device, "device")
        _exact_keys(device_object, _DEVICE_KEYS, "device")
        if set(device_object) != _DEVICE_KEYS:
            raise ConfigError("device is missing required keys")
        driver = _safe_identifier(device_object["driver"], "driver")
        if production and driver == "fake":
            raise ConfigError("fake driver is forbidden in production")
        devices[device_id] = DeviceConfig(driver=driver)

    return AdapterConfig(
        bind=BindConfig(host=str(address), port=port),
        sqlite_path=_resolve_config_path(config_file, root["sqlite_path"], "sqlite_path"),
        production=production,
        tls=tls,
        devices=MappingProxyType(devices),
    )


def load_secret(environment: Mapping[str, str] | None = None) -> bytes:
    env = os.environ if environment is None else environment
    secret_file = env.get("RFID_ADAPTER_SECRET_FILE")
    if secret_file is not None:
        if not secret_file:
            raise ConfigError("adapter secret file is not configured")
        try:
            secret = Path(secret_file).expanduser().read_bytes()
        except OSError as error:
            raise ConfigError("adapter secret file is unreadable") from error
        if secret.endswith(b"\r\n"):
            secret = secret[:-2]
        elif secret.endswith(b"\n"):
            secret = secret[:-1]
    else:
        raw_secret = env.get("RFID_ADAPTER_SECRET")
        if raw_secret is None:
            raise ConfigError("adapter secret is not configured")
        try:
            secret = raw_secret.encode("utf-8")
        except UnicodeError as error:
            raise ConfigError("adapter secret is invalid") from error
    if len(secret) < 32:
        raise ConfigError("adapter secret must be at least 32 bytes")
    if b"\n" in secret or b"\r" in secret or b"\x00" in secret:
        raise ConfigError("adapter secret has invalid bytes")
    return secret
