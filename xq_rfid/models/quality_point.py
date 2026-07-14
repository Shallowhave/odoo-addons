# -*- coding: utf-8 -*-
##############################################################################
#
# Grit - ifangtech.com
# Copyright (C) 2024 (https://ifangtech.com)
#
##############################################################################

from odoo import api, fields, models


_OPERATIONAL_DEVICE_DOMAIN = [
    ("device_type", "=", "si120x1"),
    ("active", "=", True),
    ("validation_state", "=", "validated"),
]


class QualityPoint(models.Model):
    _inherit = "quality.point"
    _check_company_auto = True

    test_type = fields.Char(
        string="测试类型",
        related="test_type_id.technical_name",
        readonly=True,
        store=False,
    )
    rfid_device_required = fields.Boolean(
        string="需要 RFID 设备",
        help="启用后，生成 RFID 时将调用硬件设备接口进行写入操作",
    )
    rfid_device_id = fields.Many2one(
        "rfid.device.config",
        string="RFID 设备",
        check_company=True,
        domain=_OPERATIONAL_DEVICE_DOMAIN,
        help="选择用于 RFID 写入的已验证 SI120X1 设备",
    )

    def _find_default_rfid_device(self):
        return self.env["rfid.device.config"].search(
            _OPERATIONAL_DEVICE_DOMAIN + [("company_id", "=", self.env.company.id)],
            limit=1,
        )

    @api.model_create_multi
    def create(self, vals_list):
        """Select a validated SI120X1 in the current company when needed."""
        records = super().create(vals_list)
        default_device = self._find_default_rfid_device()
        for record in records:
            if (
                record.test_type_id
                and record.test_type_id.technical_name == "rfid_write"
                and not record.rfid_device_id
                and default_device
            ):
                record.rfid_device_id = default_device
        return records

    @api.onchange("test_type_id")
    def _onchange_test_type_id(self):
        if (
            self.test_type_id
            and self.test_type_id.technical_name == "rfid_write"
            and not self.rfid_device_id
        ):
            self.rfid_device_id = self._find_default_rfid_device()
