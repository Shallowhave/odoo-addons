# -*- coding: utf-8 -*-
##############################################################################
#
# Grit - ifangtech.com
# Copyright (C) 2024 (https://ifangtech.com)
#
# Odoo → RFID Adapter HTTP client
# Sole authenticated entry point for all Adapter API calls.
#
##############################################################################

import hashlib
import hmac
import json
import logging
import secrets
import time

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Canonical HMAC request format — must exactly match the Adapter side
# (services/xq_rfid_adapter/src/xq_rfid_adapter/api.py).

_CONNECT_TIMEOUT = 2
_READ_TIMEOUT = 10
_TIMEOUT = (_CONNECT_TIMEOUT, _READ_TIMEOUT)

# Error code → retryable flag (matches Adapter domain).
_RETRYABLE_CODES = frozenset({"connection_error", "timeout"})


def _canonical_request(method, request_target, timestamp, nonce, body):
    """Build canonical request bytes — identical to Adapter's canonical_request."""
    digest = hashlib.sha256(body).hexdigest()
    return "\n".join(
        (method.upper(), request_target, timestamp, nonce, digest)
    ).encode("utf-8")


def _sign_request(secret, method, request_target, timestamp, nonce, body):
    """Compute HMAC-SHA256 signature — identical to Adapter's sign_request."""
    return hmac.new(
        secret,
        _canonical_request(method, request_target, timestamp, nonce, body),
        hashlib.sha256,
    ).hexdigest()


class RfidAdapterClient(models.AbstractModel):
    """Sole HTTP client for Odoo → RFID Adapter communication.

    Base URL, TLS, and shared secret are sourced exclusively from environment
    variables or admin-only ``ir.config_parameter`` keys.  Method signatures
    intentionally do NOT accept URL, IP, port, or path arguments — the Adapter
    endpoint is not under the caller's control.
    """

    _name = "rfid.adapter.client"
    _description = "RFID Adapter 客户端"

    # ------------------------------------------------------------------
    # Configuration helpers (no caller-controllable overrides)
    # ------------------------------------------------------------------

    def _get_base_url(self):
        """Return the Adapter base URL from system parameters or env."""
        import os
        base_url = os.environ.get("RFID_ADAPTER_URL")
        if not base_url:
            base_url = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("xq_rfid.adapter_url", default="")
            )
        if not base_url:
            raise UserError(_("RFID Adapter URL 未配置。"))
        # Strip trailing slash for consistent path joining.
        return base_url.rstrip("/")

    def _get_secret(self):
        """Return the shared HMAC secret as bytes."""
        import os
        secret_file = os.environ.get("RFID_ADAPTER_SECRET_FILE", "")
        if secret_file:
            try:
                with open(secret_file, "rb") as fh:
                    secret = fh.read().strip()
            except OSError as exc:
                raise UserError(_("无法读取 RFID Adapter 密钥文件。")) from exc
        else:
            secret_env = os.environ.get("RFID_ADAPTER_SECRET", "")
            if not secret_env:
                secret_env = (
                    self.env["ir.config_parameter"]
                    .sudo()
                    .get_param("xq_rfid.adapter_secret", default="")
                )
            if not secret_env:
                raise UserError(_("RFID Adapter 共享密钥未配置。"))
            secret = secret_env.encode("utf-8")
        if len(secret) < 16:
            raise UserError(_("RFID Adapter 共享密钥长度不足。"))
        return secret

    # ------------------------------------------------------------------
    # Low-level transport
    # ------------------------------------------------------------------

    def _request(self, method, path, body_dict=None):
        """Perform a signed HTTP request to the Adapter.

        Returns the parsed JSON envelope ``{"ok": ..., "result": ..., "error": ...}``.
        Raises ``UserError`` on any transport or protocol failure.
        """
        import requests as req_lib

        base_url = self._get_base_url()
        secret = self._get_secret()
        url = base_url + path
        body = (
            json.dumps(body_dict, separators=(",", ":"), ensure_ascii=True).encode(
                "utf-8"
            )
            if body_dict is not None
            else b""
        )
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        signature = _sign_request(secret, method, path, timestamp, nonce, body)

        headers = {
            "X-RFID-Timestamp": timestamp,
            "X-RFID-Nonce": nonce,
            "X-RFID-Signature": signature,
        }
        if body_dict is not None:
            headers["Content-Type"] = "application/json"

        try:
            response = req_lib.request(
                method,
                url,
                data=body if body_dict is not None else None,
                headers=headers,
                timeout=_TIMEOUT,
            )
        except req_lib.Timeout as exc:
            raise UserError(_("RFID Adapter 连接超时。")) from exc
        except req_lib.ConnectionError as exc:
            raise UserError(_("无法连接到 RFID Adapter。")) from exc
        except req_lib.RequestException as exc:
            raise UserError(_("RFID Adapter 请求失败。")) from exc

        try:
            envelope = response.json()
        except (ValueError, TypeError) as exc:
            raise UserError(_("RFID Adapter 返回了无效的 JSON 响应。")) from exc

        if not isinstance(envelope, dict):
            raise UserError(_("RFID Adapter 返回了无效的响应格式。"))

        if not envelope.get("ok"):
            error = envelope.get("error", {})
            code = error.get("code", "device_error") if isinstance(error, dict) else "device_error"
            message = error.get("message", "") if isinstance(error, dict) else ""
            safe_msg = _("RFID Adapter 错误: %s") % (message or code)
            if code in _RETRYABLE_CODES:
                raise UserError(_("RFID Adapter 暂时不可用（%s），请稍后重试。") % code)
            raise UserError(safe_msg)

        return envelope

    # ------------------------------------------------------------------
    # Public API (match AdapterService Protocol)
    # ------------------------------------------------------------------

    def test_connection(self, device):
        """Test connection to a specific device via the Adapter.

        Args:
            device: ``rfid.device.config`` record.

        Returns:
            dict with ``{"status": "connected"|"disconnected"}``.
        """
        device.ensure_one()
        device._ensure_rfid_manager()
        device._ensure_probe_ready()
        adapter_device_id = device.adapter_device_id
        if not adapter_device_id:
            raise UserError(_("设备缺少 Adapter 设备 ID。"))
        path = "/v1/devices/%s/test-connection" % adapter_device_id
        envelope = self._request("POST", path, {})
        return envelope.get("result", {})

    def get_device_info(self, device):
        """Retrieve device metadata and capabilities from the Adapter.

        Args:
            device: ``rfid.device.config`` record.

        Returns:
            dict with status, capabilities, versions, etc.
        """
        device.ensure_one()
        device._ensure_rfid_manager()
        device._ensure_probe_ready()
        adapter_device_id = device.adapter_device_id
        if not adapter_device_id:
            raise UserError(_("设备缺少 Adapter 设备 ID。"))
        path = "/v1/devices/%s" % adapter_device_id
        envelope = self._request("GET", path)
        return envelope.get("result", {})

    def submit_operation(self, operation_dict):
        """Submit an RFID write-and-verify operation.

        Args:
            operation_dict: dict with request_id, operation_type, device_id,
                            payload_hex, payload_version.

        Returns:
            Submitted operation result dict.
        """
        envelope = self._request("POST", "/v1/operations", operation_dict)
        return envelope.get("result", {})

    def get_operation(self, request_id):
        """Query operation status by request ID.

        Args:
            request_id: stable operation identifier string.

        Returns:
            Operation result dict.
        """
        if not request_id:
            raise UserError(_("操作 ID 不能为空。"))
        path = "/v1/operations/%s" % request_id
        envelope = self._request("GET", path)
        return envelope.get("result", {})
