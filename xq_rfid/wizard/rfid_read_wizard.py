# -*- coding: utf-8 -*-

import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError


_DEVICE_DOMAIN = [
    ("device_type", "=", "si120x1"),
    ("active", "=", True),
    ("validation_state", "=", "validated"),
]
_HEX_RE = re.compile(r"^[0-9A-F]+$")


class RfidReadWizard(models.TransientModel):
    _name = "rfid.read.wizard"
    _description = "RFID 读取向导"
    _check_company_auto = True

    device_id = fields.Many2one(
        "rfid.device.config",
        string="RFID 设备",
        required=True,
        check_company=True,
        domain=_DEVICE_DOMAIN,
    )
    epc_hex = fields.Char(
        string="EPC 标签",
        help="要读取的 RFID 标签 EPC（十六进制）",
    )
    mem_bank = fields.Selection(
        [
            ("0x01", "EPC 存储区"),
            ("0x02", "TID 存储区"),
            ("0x03", "用户存储区"),
        ],
        string="存储区",
        default="0x03",
        required=True,
    )
    word_ptr = fields.Integer(string="起始地址", default=0)
    word_count = fields.Integer(string="字数", default=20)
    read_result = fields.Text(string="读取结果", readonly=True)
    parsed_data = fields.Text(string="解析数据", readonly=True)
    read_status = fields.Selection(
        [
            ("pending", "待读取"),
            ("reading", "读取中"),
            ("success", "读取成功"),
            ("failed", "读取失败"),
        ],
        string="读取状态",
        default="pending",
        readonly=True,
    )
    read_time = fields.Datetime(string="读取时间", readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        default_device = self.env["rfid.device.config"].search(
            _DEVICE_DOMAIN + [("company_id", "=", self.env.company.id)],
            limit=1,
        )
        if default_device:
            res["device_id"] = default_device.id
        return res

    def _ensure_rfid_manager(self):
        if not self.env.user.has_group("xq_rfid.group_rfid_manager"):
            raise UserError(_("只有 RFID 管理员可以执行设备读取操作。"))

    def _validate_read_input(self):
        if self.device_id.company_id != self.env.company:
            raise UserError(_("请选择当前公司的 RFID 设备。"))
        epc_hex = (self.epc_hex or "").strip().upper()
        if not epc_hex:
            raise UserError(_("请输入 EPC 标签。"))
        if len(epc_hex) % 2 or not _HEX_RE.fullmatch(epc_hex):
            raise UserError(_("EPC 必须是偶数长度的十六进制字符串。"))
        if self.word_ptr < 0:
            raise UserError(_("起始地址不能小于 0。"))
        if not 1 <= self.word_count <= 128:
            raise UserError(_("读取字数必须在 1 到 128 之间。"))

    def action_read_rfid(self):
        self.ensure_one()
        self._ensure_rfid_manager()
        if not self.device_id:
            raise UserError(_("请选择 RFID 设备。"))
        self._validate_read_input()
        self.device_id._ensure_operational()
        self.device_id._raise_adapter_not_configured()

    def action_test_connection(self):
        self.ensure_one()
        self._ensure_rfid_manager()
        if not self.device_id:
            raise UserError(_("请选择 RFID 设备。"))
        return self.device_id.action_test_connection()
