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
from odoo.exceptions import AccessError, UserError


def _validate_device_type(device_type):
    if device_type == "legacy_disabled":
        raise UserError(_("不能创建或设置已停用的旧设备类型。"))


class RfidDeviceService(models.AbstractModel):
    """Compatibility interface that fails closed until an Adapter is provided."""

    _name = "rfid.device.service"
    _description = "RFID 设备服务接口"

    def _ensure_rfid_manager(self):
        if not self.env.user.has_group("xq_rfid.group_rfid_manager"):
            raise UserError(_("只有 RFID 管理员可以执行设备硬件操作。"))

    def write_rfid_tag(self, data):
        del data
        self._ensure_rfid_manager()
        return {"success": False, "error": _("未配置可用的 RFID Adapter 驱动。")}

    def read_rfid_tag(self):
        self._ensure_rfid_manager()
        return {"success": False, "error": _("未配置可用的 RFID Adapter 驱动。")}

    def verify_rfid_tag(self, rfid_number):
        del rfid_number
        self._ensure_rfid_manager()
        return {
            "success": False,
            "valid": False,
            "error": _("未配置可用的 RFID Adapter 驱动。"),
        }

    def erase_rfid_tag(self):
        self._ensure_rfid_manager()
        return {"success": False, "error": _("未配置可用的 RFID Adapter 驱动。")}

    def get_device_status(self):
        self._ensure_rfid_manager()
        return {
            "connected": False,
            "error": _("未配置可用的 RFID Adapter 驱动。"),
        }


class RfidDeviceConfig(models.Model):
    _name = "rfid.device.config"
    _description = "RFID 设备配置"
    _order = "sequence, id"
    _check_company_auto = True

    _sql_constraints = [
        (
            "adapter_device_id_company_uniq",
            "unique(company_id, adapter_device_id)",
            "每个公司的 Adapter 设备 ID 必须唯一。",
        )
    ]

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

    # ------------------------------------------------------------------
    # Capability fields for SI120X1 / Adapter
    # ------------------------------------------------------------------
    adapter_device_id = fields.Char(string="Adapter 设备 ID", index=True)
    protocol_family = fields.Selection(
        [
            ("unconfirmed", "未确认"),
            ("moduleapi_http", "ModuleAPI HTTP"),
            ("moduleapi_sdk", "ModuleAPI SDK"),
            ("ex10_raw", "EX10 原始协议"),
        ],
        string="协议族",
        default="unconfirmed",
        readonly=True,
    )
    transport_type = fields.Selection(
        [
            ("http", "HTTP"),
            ("tcp_transparent", "TCP 透传"),
            ("serial", "串口"),
            ("sdk_tcp", "SDK TCP"),
            ("sdk_serial", "SDK 串口"),
        ],
        string="传输层",
        readonly=True,
    )
    firmware_version = fields.Char(string="固件版本", readonly=True)
    hardware_version = fields.Char(string="硬件版本", readonly=True)
    module_version = fields.Char(string="模块版本", readonly=True)
    antenna_count = fields.Integer(string="天线数量", readonly=True)
    region = fields.Char(string="频段区域", readonly=True)
    supports_epc = fields.Boolean(string="支持 EPC", readonly=True)
    supports_tid = fields.Boolean(string="支持 TID", readonly=True)
    supports_user_read = fields.Boolean(string="支持读 User 区", readonly=True)
    supports_user_write = fields.Boolean(string="支持写 User 区", readonly=True)
    last_connection_test_at = fields.Datetime(string="最后连接测试", readonly=True)
    last_successful_operation_at = fields.Datetime(string="最后成功操作", readonly=True)
    last_device_code = fields.Char(string="最后设备状态码", readonly=True)

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

    @api.model
    def _validate_allowed_company(self, company_id):
        if not company_id:
            return
        company = self.env["res.company"].browse(company_id).exists()
        if not company or company not in self.env.companies:
            raise AccessError(_("无权将 RFID 设备分配到该公司。"))

    @api.model_create_multi
    def create(self, vals_list):
        default_device_type = self.env.context.get("default_device_type")
        default_company_id = self.env.context.get("default_company_id")
        for vals in vals_list:
            _validate_device_type(vals.get("device_type", default_device_type))
            self._validate_allowed_company(
                vals.get("company_id", default_company_id or self.env.company.id)
            )
            if vals.get("device_type", default_device_type) == "si120x1" and not vals.get("adapter_device_id"):
                raise UserError(_("SI120X1 类型的设备必须配置 Adapter 设备 ID。"))
        return super().create(vals_list)

    def write(self, vals):
        _validate_device_type(vals.get("device_type"))
        self._validate_allowed_company(vals.get("company_id"))
        if "device_type" in vals or "adapter_device_id" in vals:
            for device in self:
                dev_type = vals.get("device_type", device.device_type)
                adapter_id = vals.get("adapter_device_id", device.adapter_device_id)
                if dev_type == "si120x1" and not adapter_id:
                    raise UserError(_("SI120X1 类型的设备必须配置 Adapter 设备 ID。"))
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

        client = self.env["rfid.adapter.client"]

        # 1. 测连通性
        try:
            conn_result = client.test_connection(self)
        except Exception as exc:
            self.write({
                "validation_state": "error",
                "connection_status": "error",
                "error_message": str(exc),
                "last_connection_test_at": fields.Datetime.now(),
            })
            raise

        if conn_result.get("status") != "connected":
            self.write({
                "validation_state": "error",
                "connection_status": "disconnected",
                "error_message": _("Adapter 未连接到硬件。"),
                "last_connection_test_at": fields.Datetime.now(),
            })
            raise UserError(_("Adapter 未连接到硬件。"))

        # 2. 查设备能力
        try:
            dev_info = client.get_device_info(self)
        except Exception as exc:
            self.write({
                "validation_state": "error",
                "connection_status": "error",
                "error_message": str(exc),
                "last_connection_test_at": fields.Datetime.now(),
            })
            raise

        caps = dev_info.get("capabilities", {})

        # We must confirm the device matches our requirements
        is_si120x1 = self.device_type == "si120x1"
        is_protocol_confirmed = self.protocol_family != "unconfirmed"
        supports_reqs = caps.get("supports_epc") and caps.get("supports_user_read") and caps.get("supports_user_write")

        valid = is_si120x1 and is_protocol_confirmed and supports_reqs

        self.write({
            "connection_status": "connected",
            "last_connected": fields.Datetime.now(),
            "last_connection_test_at": fields.Datetime.now(),
            "error_message": False,
            "firmware_version": dev_info.get("firmware_version"),
            "hardware_version": dev_info.get("hardware_version"),
            "module_version": dev_info.get("module_version"),
            "antenna_count": dev_info.get("antenna_count"),
            "region": dev_info.get("region"),
            "supports_epc": caps.get("supports_epc"),
            "supports_tid": caps.get("supports_tid"),
            "supports_user_read": caps.get("supports_user_read"),
            "supports_user_write": caps.get("supports_user_write"),
            "validation_state": "validated" if valid else "error",
        })

        if not valid:
            raise UserError(_("设备能力不满足写后验证要求，或协议族尚未确认。"))

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
