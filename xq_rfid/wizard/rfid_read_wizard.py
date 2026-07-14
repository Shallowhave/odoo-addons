# -*- coding: utf-8 -*-

import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError


_SELECTABLE_DEVICE_DOMAIN = """[
    ('device_type', '=', 'si120x1'),
    ('active', '=', True),
    ('validation_state', '=', 'validated'),
    ('company_id', '=', company_id),
]"""
_HEX_RE = re.compile(r"^[0-9A-F]+$")
_BANK_NAMES = {"0x01": "epc", "0x02": "tid", "0x03": "user"}


class RfidReadWizard(models.TransientModel):
    _name = "rfid.read.wizard"
    _description = "RFID 读取向导"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        readonly=True,
    )
    device_id = fields.Many2one(
        "rfid.device.config",
        string="RFID 设备",
        required=True,
        check_company=True,
        domain=_SELECTABLE_DEVICE_DOMAIN,
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
        res["company_id"] = self.env.company.id
        device_model = self.env["rfid.device.config"]
        device = device_model.browse(res.get("device_id")).exists()
        selectable_domain = device_model._selectable_domain(self.env.company)
        if not device or not device.filtered_domain(selectable_domain):
            device = device_model._find_selectable(self.env.company)
        res["device_id"] = device.id or False
        return res

    def _ensure_rfid_manager(self):
        if not self.env.user.has_group("xq_rfid.group_rfid_manager"):
            raise UserError(_("只有 RFID 管理员可以执行设备读取操作。"))

    def _validate_read_input(self):
        if self.company_id != self.env.company:
            raise UserError(_("请选择当前公司的 RFID 设备。"))
        if self.device_id.company_id != self.company_id:
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
        return epc_hex

    def action_read_rfid(self):
        self.ensure_one()
        self._ensure_rfid_manager()
        if not self.device_id:
            raise UserError(_("请选择 RFID 设备。"))
        epc_hex = self._validate_read_input()
        return self.device_id.read_memory(
            epc_hex,
            _BANK_NAMES[self.mem_bank],
            self.word_ptr,
            self.word_count,
        )

    def action_test_connection(self):
        self.ensure_one()
        self._ensure_rfid_manager()
        if not self.device_id:
            raise UserError(_("请选择 RFID 设备。"))
        if self.company_id != self.env.company or self.device_id.company_id != self.company_id:
            raise UserError(_("请选择当前公司的 RFID 设备。"))
        return self.device_id.action_test_connection()
