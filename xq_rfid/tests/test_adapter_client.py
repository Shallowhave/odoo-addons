# -*- coding: utf-8 -*-
from unittest.mock import patch
import requests

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "xq_rfid")
class TestAdapterClient(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "xq_rfid.adapter_url", "http://127.0.0.1:8000"
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "xq_rfid.adapter_secret", "0123456789abcdef0123456789abcdef"
        )
        cls.client = cls.env["rfid.adapter.client"]

        cls.device = cls.env["rfid.device.config"].sudo().create({
            "name": "SI120X1 test",
            "device_type": "si120x1",
            "adapter_device_id": "reader-1",
            "company_id": cls.env.company.id,
            "active": True,
        })

    def test_client_builds_url_from_system_configuration_only(self):
        with self.assertRaises(TypeError):
            # Pass arbitrary keyword argument to verify it doesn't accept base_url etc.
            self.client.get_operation("r1", base_url="http://attacker/")

    def test_timeout_maps_to_retryable_error(self):
        with patch("requests.request", side_effect=requests.Timeout):
            with self.assertRaisesRegex(UserError, "超时"):
                self.client.get_operation("r1")

    def test_connection_error_maps_to_user_error(self):
        with patch("requests.request", side_effect=requests.ConnectionError):
            with self.assertRaisesRegex(UserError, "无法连接"):
                self.client.get_operation("r1")

    def test_invalid_json_maps_to_user_error(self):
        class MockResponse:
            def json(self):
                raise ValueError("Bad JSON")

        with patch("requests.request", return_value=MockResponse()):
            with self.assertRaisesRegex(UserError, "无效的 JSON"):
                self.client.get_operation("r1")

    def test_adapter_error_maps_to_user_error(self):
        class MockResponse:
            def json(self):
                return {
                    "ok": False,
                    "error": {
                        "code": "device_error",
                        "message": "internal device error"
                    }
                }

        with patch("requests.request", return_value=MockResponse()):
            with self.assertRaisesRegex(UserError, "internal device error"):
                self.client.get_operation("r1")

    def test_retryable_adapter_error_maps_to_retry_message(self):
        class MockResponse:
            def json(self):
                return {
                    "ok": False,
                    "error": {
                        "code": "timeout",
                        "message": "device operation timed out",
                        "retryable": True
                    }
                }

        with patch("requests.request", return_value=MockResponse()):
            with self.assertRaisesRegex(UserError, "暂时不可用.*timeout"):
                self.client.get_operation("r1")

    def test_successful_request_returns_result_dict(self):
        class MockResponse:
            def json(self):
                return {
                    "ok": True,
                    "result": {"status": "connected"}
                }

        with patch("requests.request", return_value=MockResponse()):
            result = self.client.test_connection(self.device)
            self.assertEqual(result, {"status": "connected"})
