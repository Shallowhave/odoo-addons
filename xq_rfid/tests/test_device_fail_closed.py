# -*- coding: utf-8 -*-

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDeviceFailClosed(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.device = cls.env["rfid.device.config"].create({
            "name": "SI120X1 test",
            "device_type": "si120x1",
            "company_id": cls.env.company.id,
            "active": True,
        })

    def test_unvalidated_device_is_not_operational(self):
        with self.assertRaisesRegex(UserError, "尚未验证"):
            self.device._ensure_operational()

    def test_abstract_service_never_reports_write_success(self):
        result = self.env["rfid.device.service"].write_rfid_tag({"token": "test"})
        self.assertFalse(result["success"])

    def test_non_manager_cannot_run_hardware_action(self):
        user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "RFID Non Manager",
            "login": "rfid-non-manager",
            "groups_id": [(6, 0, [self.env.ref("xq_rfid.group_rfid_user").id])],
        })
        with self.assertRaisesRegex(UserError, "管理员"):
            self.device.with_user(user).action_test_connection()

    def test_validated_device_fails_when_adapter_is_not_configured(self):
        self.device.validation_state = "validated"
        with self.assertRaisesRegex(UserError, "Adapter 尚未配置"):
            self.device.action_test_connection()

    def test_read_wizard_rejects_invalid_bounds_before_adapter(self):
        self.device.validation_state = "validated"
        wizard = self.env["rfid.read.wizard"].create({
            "device_id": self.device.id,
            "epc_hex": "3008",
            "word_count": 0,
        })
        with self.assertRaisesRegex(UserError, "1 到 128"):
            wizard.action_read_rfid()

    def test_read_wizard_rejects_invalid_epc_before_adapter(self):
        self.device.validation_state = "validated"
        wizard = self.env["rfid.read.wizard"].create({
            "device_id": self.device.id,
            "epc_hex": "XYZ",
        })
        with self.assertRaisesRegex(UserError, "十六进制"):
            wizard.action_read_rfid()

    def test_read_wizard_has_no_reserve_bank(self):
        selection = dict(
            self.env["rfid.read.wizard"]._fields["mem_bank"]._description_selection(
                self.env
            )
        )
        self.assertNotIn("0x00", selection)
