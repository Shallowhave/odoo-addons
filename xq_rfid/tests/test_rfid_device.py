# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "xq_rfid")
class TestRfidDevice(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.device = cls.env["rfid.device.config"].sudo().create({
            "name": "SI120X1 test",
            "device_type": "si120x1",
            "adapter_device_id": "reader-1",
            "company_id": cls.env.company.id,
            "active": True,
        })
        cls.env["ir.config_parameter"].sudo().set_param(
            "xq_rfid.adapter_url", "http://127.0.0.1:8000"
        )
        cls.env["ir.config_parameter"].sudo().set_param(
            "xq_rfid.adapter_secret", "0123456789abcdef0123456789abcdef"
        )

    def test_missing_adapter_id_prevents_creation_of_si120x1(self):
        with self.assertRaisesRegex(UserError, "必须配置 Adapter 设备 ID"):
            self.env["rfid.device.config"].sudo().create({
                "name": "Invalid device",
                "device_type": "si120x1",
                "company_id": self.env.company.id,
            })

    def test_connection_test_validates_capabilities_and_sets_validated(self):
        # We need to patch the two client calls that happen during action_test_connection
        def mock_test_connection(client, device):
            return {"status": "connected"}

        def mock_get_device_info(client, device):
            return {
                "status": "connected",
                "capabilities": {
                    "supports_epc": True,
                    "supports_tid": True,
                    "supports_user_read": True,
                    "supports_user_write": True,
                },
                "antenna_count": 1,
                "region": "CN",
                "firmware_version": "1.0.0",
                "hardware_version": "2.0.0",
                "module_version": "3.0.0",
            }

        # Override protocol_family so it's not "unconfirmed"
        self.device.write({"protocol_family": "moduleapi_http"})

        with patch("odoo.addons.xq_rfid.models.rfid_adapter_client.RfidAdapterClient.test_connection", new=mock_test_connection), \
             patch("odoo.addons.xq_rfid.models.rfid_adapter_client.RfidAdapterClient.get_device_info", new=mock_get_device_info):
            self.device.action_test_connection()

        self.assertEqual(self.device.validation_state, "validated")
        self.assertEqual(self.device.connection_status, "connected")
        self.assertEqual(self.device.firmware_version, "1.0.0")
        self.assertEqual(self.device.hardware_version, "2.0.0")
        self.assertEqual(self.device.module_version, "3.0.0")
        self.assertEqual(self.device.antenna_count, 1)
        self.assertEqual(self.device.region, "CN")
        self.assertTrue(self.device.supports_epc)
        self.assertTrue(self.device.supports_tid)
        self.assertTrue(self.device.supports_user_read)
        self.assertTrue(self.device.supports_user_write)

    def test_missing_capabilities_prevent_validation(self):
        def mock_test_connection(client, device):
            return {"status": "connected"}

        def mock_get_device_info(client, device):
            return {
                "status": "connected",
                "capabilities": {
                    "supports_epc": True,
                    "supports_tid": False,
                    "supports_user_read": False,  # Missing requirement
                    "supports_user_write": True,
                },
                "antenna_count": 1,
                "region": "CN",
            }

        self.device.write({"protocol_family": "moduleapi_http"})

        with patch("odoo.addons.xq_rfid.models.rfid_adapter_client.RfidAdapterClient.test_connection", new=mock_test_connection), \
             patch("odoo.addons.xq_rfid.models.rfid_adapter_client.RfidAdapterClient.get_device_info", new=mock_get_device_info):
            with self.assertRaisesRegex(UserError, "设备能力不满足"):
                self.device.action_test_connection()

        self.assertEqual(self.device.validation_state, "error")

    def test_unconfirmed_protocol_prevents_validation(self):
        def mock_test_connection(client, device):
            return {"status": "connected"}

        def mock_get_device_info(client, device):
            return {
                "status": "connected",
                "capabilities": {
                    "supports_epc": True,
                    "supports_tid": True,
                    "supports_user_read": True,
                    "supports_user_write": True,
                },
                "antenna_count": 1,
                "region": "CN",
            }

        # Keep protocol_family as "unconfirmed" (the default)
        with patch("odoo.addons.xq_rfid.models.rfid_adapter_client.RfidAdapterClient.test_connection", new=mock_test_connection), \
             patch("odoo.addons.xq_rfid.models.rfid_adapter_client.RfidAdapterClient.get_device_info", new=mock_get_device_info):
            with self.assertRaisesRegex(UserError, "设备能力不满足写后验证要求，或协议族尚未确认"):
                self.device.action_test_connection()

        self.assertEqual(self.device.validation_state, "error")

    def test_disconnected_status_prevents_validation(self):
        def mock_test_connection(client, device):
            return {"status": "disconnected"}

        self.device.write({"protocol_family": "moduleapi_http"})

        with patch("odoo.addons.xq_rfid.models.rfid_adapter_client.RfidAdapterClient.test_connection", new=mock_test_connection):
            with self.assertRaisesRegex(UserError, "Adapter 未连接到硬件"):
                self.device.action_test_connection()

        self.assertEqual(self.device.validation_state, "error")
        self.assertEqual(self.device.connection_status, "disconnected")
