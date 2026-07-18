# -*- coding: utf-8 -*-
from unittest.mock import patch
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "xq_rfid")
class TestRfidReadWizard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        
        cls.manager = cls.env["res.users"].create({
            "name": "RFID Manager",
            "login": "rfid_wizard_manager",
            "groups_id": [(6, 0, [cls.env.ref("xq_rfid.group_rfid_manager").id])],
        })
        
        cls.user = cls.env["res.users"].create({
            "name": "RFID User",
            "login": "rfid_wizard_user",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        
        cls.device = cls.env["rfid.device.config"].sudo().create({
            "name": "SI120X1 test",
            "device_type": "si120x1",
            "adapter_device_id": "reader-1",
            "company_id": cls.company.id,
            "active": True,
        })
        
        cls.device.write({
            "validation_state": "validated",
            "connection_status": "connected",
        })

    def _create_wizard(self, user=None, **kwargs):
        env = self.env(user=user or self.manager)
        vals = {
            "device_id": self.device.id,
            "epc_hex": "1234ABCD",
            "mem_bank": "0x03",
            "word_ptr": 0,
            "word_count": 4,
        }
        vals.update(kwargs)
        return env["rfid.read.wizard"].create(vals)

    def test_manager_can_read(self):
        wizard = self._create_wizard()
        
        def mock_submit(client, device, req_id, op_type, payload):
            self.assertEqual(device.id, self.device.id)
            self.assertEqual(op_type, "read_memory")
            self.assertEqual(payload["target"], "1234ABCD")
            self.assertEqual(payload["bank"], "user")
            self.assertEqual(payload["offset"], 0)
            self.assertEqual(payload["count"], 4)
            return {"status": "queued"}
            
        with patch("odoo.addons.xq_rfid.models.rfid_adapter_client.RfidAdapterClient.submit_operation", new=mock_submit):
            wizard.action_read_rfid()
            self.assertEqual(wizard.read_status, "reading")
            
    def test_user_cannot_read(self):
        wizard = self._create_wizard(user=self.user)
        with self.assertRaisesRegex(UserError, "管理员可以执行"):
            wizard.action_read_rfid()
            
    def test_word_count_limits(self):
        wizard1 = self._create_wizard(word_count=0)
        with self.assertRaisesRegex(UserError, "读取字数必须在 1 到 128 之间"):
            wizard1.action_read_rfid()
            
        wizard2 = self._create_wizard(word_count=129)
        with self.assertRaisesRegex(UserError, "读取字数必须在 1 到 128 之间"):
            wizard2.action_read_rfid()
            
    def test_word_ptr_limits(self):
        wizard = self._create_wizard(word_ptr=-1)
        with self.assertRaisesRegex(UserError, "起始地址不能小于 0"):
            wizard.action_read_rfid()
            
    def test_epc_validation(self):
        wizard1 = self._create_wizard(epc_hex="")
        with self.assertRaisesRegex(UserError, "请输入 EPC"):
            wizard1.action_read_rfid()
            
        wizard2 = self._create_wizard(epc_hex="123")  # Odd length
        with self.assertRaisesRegex(UserError, "EPC 必须是偶数长度的十六进制字符串"):
            wizard2.action_read_rfid()
            
        wizard3 = self._create_wizard(epc_hex="123G")  # Invalid hex
        with self.assertRaisesRegex(UserError, "EPC 必须是偶数长度的十六进制字符串"):
            wizard3.action_read_rfid()
            
    def test_reserve_bank_not_in_selection(self):
        bank_selection = dict(self.env['rfid.read.wizard']._fields['mem_bank'].selection)
        self.assertIn("0x01", bank_selection) # EPC
        self.assertIn("0x02", bank_selection) # TID
        self.assertIn("0x03", bank_selection) # User
        self.assertNotIn("0x00", bank_selection) # Reserve
