# -*- coding: utf-8 -*-
##############################################################################
#
# Grit - ifangtech.com
# Copyright (C) 2024 (https://ifangtech.com)
#
# RFID 硬件设备接口
# 为 RFID 读写器预留的抽象接口层
#
##############################################################################

from odoo import _, api, fields, models
from odoo.exceptions import UserError


def _validate_device_type(device_type):
    if device_type == "legacy_disabled":
        raise UserError(_("不能创建或设置已停用的旧设备类型。"))


class RfidDeviceService(models.AbstractModel):
    """Compatibility interface that fails closed until an Adapter is provided."""

    _name = "rfid.device.service"
    _description = "RFID 设备服务接口"

    def write_rfid_tag(self, data):
        del data
        return {"success": False, "error": _("未配置可用的 RFID Adapter 驱动。")}

    def read_rfid_tag(self):
        return {"success": False, "error": _("未配置可用的 RFID Adapter 驱动。")}

    def verify_rfid_tag(self, rfid_number):
        del rfid_number
        return {
            "success": False,
            "valid": False,
            "error": _("未配置可用的 RFID Adapter 驱动。"),
        }

    def erase_rfid_tag(self):
        return {"success": False, "error": _("未配置可用的 RFID Adapter 驱动。")}

    def get_device_status(self):
        return {
            "connected": False,
            "error": _("未配置可用的 RFID Adapter 驱动。"),
        }


class RfidDeviceConfig(models.Model):
    _name = "rfid.device.config"
    _description = "RFID 设备配置"
    _order = "sequence, id"
    _check_company_auto = True

    name = fields.Char(string="设备名称", required=True)
    sequence = fields.Integer(string="序号", default=10)
    active = fields.Boolean(string="启用", default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    device_type = fields.Selection(
        [
            ("simulation", "模拟设备"),
            ("legacy_disabled", "旧设备（需要重新配置）"),
            ("si120x1", "SI120X1"),
            ("custom", "自定义设备"),
        ],
        default="simulation",
        required=True,
    )
    migration_required = fields.Boolean(
        readonly=True,
        compute="_compute_migration_required",
        store=True,
    )
    validation_state = fields.Selection(
        [
            ("unvalidated", "未验证"),
            ("validated", "已验证"),
            ("error", "验证错误"),
        ],
        string="验证状态",
        default="unvalidated",
        required=True,
        readonly=True,
    )

    # Generic connection metadata. Hardware methods never accept connection
    # endpoints from RPC arguments; Task 10 will map records to the Adapter.
    connection_string = fields.Char(string="连接字符串")
    ip_address = fields.Char(string="IP 地址")
    port = fields.Char(string="端口")
    baudrate = fields.Integer(string="波特率", default=9600)
    timeout = fields.Integer(string="超时时间（秒）", default=5)
    auto_connect = fields.Boolean(string="自动连接", default=True)
    retry_times = fields.Integer(string="重试次数", default=3)

    last_connected = fields.Datetime(string="最后连接时间", readonly=True)
    connection_status = fields.Selection(
        [
            ("disconnected", "未连接"),
            ("connected", "已连接"),
            ("error", "连接错误"),
        ],
        string="连接状态",
        default="disconnected",
        readonly=True,
    )
    error_message = fields.Text(string="错误信息", readonly=True)
    write_count = fields.Integer(string="写入次数", default=0, readonly=True)
    read_count = fields.Integer(string="读取次数", default=0, readonly=True)
    notes = fields.Text(string="备注")

    @api.model_create_multi
    def create(self, vals_list):
        default_device_type = self.env.context.get("default_device_type")
        for vals in vals_list:
            _validate_device_type(vals.get("device_type", default_device_type))
        return super().create(vals_list)

    def write(self, vals):
        _validate_device_type(vals.get("device_type"))
        return super().write(vals)

    @api.depends("device_type")
    def _compute_migration_required(self):
        for device in self:
            device.migration_required = device.device_type == "legacy_disabled"

    def _ensure_rfid_manager(self):
        if not self.env.user.has_group("xq_rfid.group_rfid_manager"):
            raise UserError(_("只有 RFID 管理员可以执行设备配置和硬件操作。"))

    @api.model
    def _selectable_domain(self, company=None):
        company = company or self.env.company
        if company not in self.env.companies:
            raise UserError(_("无权访问该公司的 RFID 设备。"))
        return [
            ("device_type", "=", "si120x1"),
            ("active", "=", True),
            ("validation_state", "=", "validated"),
            ("company_id", "=", company.id),
        ]

    @api.model
    def _find_selectable(self, company=None):
        return self.search(self._selectable_domain(company), limit=1)

    def _ensure_probe_ready(self):
        self.ensure_one()
        if not self.active:
            raise UserError(_("RFID 设备已停用。"))
        if self.migration_required:
            raise UserError(_("旧 RFID 设备必须重新配置。"))
        if self.device_type != "si120x1":
            raise UserError(_("该设备不是 SI120X1。"))
        if self.company_id not in self.env.companies:
            raise UserError(_("无权访问该公司的 RFID 设备。"))
        return True

    def _ensure_operational(self):
        self._ensure_probe_ready()
        if self.validation_state != "validated":
            raise UserError(_("SI120X1 设备尚未验证。"))
        return True

    def _raise_adapter_not_configured(self):
        raise UserError(_("RFID Adapter 尚未配置。"))

    def write_and_verify(self, payload):
        del payload
        self._ensure_rfid_manager()
        self._ensure_operational()
        self._raise_adapter_not_configured()

    def read_memory(self, epc_hex, memory_bank, word_offset, word_count):
        del epc_hex, memory_bank, word_offset, word_count
        self._ensure_rfid_manager()
        self._ensure_operational()
        self._raise_adapter_not_configured()

    def action_test_connection(self):
        self.ensure_one()
        self._ensure_rfid_manager()
        self._ensure_probe_ready()
        self._raise_adapter_not_configured()

    def action_write_test_tag(self):
        self.ensure_one()
        self._ensure_rfid_manager()
        self.write_and_verify({"test": True})

    def action_read_test_tag(self):
        self.ensure_one()
        self._ensure_rfid_manager()
        self.read_memory("00", "user", 0, 1)

    def action_view_write_logs(self):
        self.ensure_one()
        self._ensure_rfid_manager()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("写入统计"),
                "message": _("设备 %s 累计写入次数: %d") % (self.name, self.write_count),
                "type": "info",
            },
        }

    def action_view_read_logs(self):
        self.ensure_one()
        self._ensure_rfid_manager()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("读取统计"),
                "message": _("设备 %s 累计读取次数: %d") % (self.name, self.read_count),
                "type": "info",
            },
        }
