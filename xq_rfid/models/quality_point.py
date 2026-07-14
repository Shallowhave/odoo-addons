# -*- coding: utf-8 -*-
##############################################################################
#
# Grit - ifangtech.com
# Copyright (C) 2024 (https://ifangtech.com)
#
##############################################################################

from odoo import api, fields, models


_SELECTABLE_DEVICE_DOMAIN = """[
    ('device_type', '=', 'si120x1'),
    ('active', '=', True),
    ('validation_state', '=', 'validated'),
    ('company_id', '=', company_id),
]"""


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
        domain=_SELECTABLE_DEVICE_DOMAIN,
        help="选择用于 RFID 写入的已验证 SI120X1 设备",
    )

    @api.model
    def _find_default_rfid_device(self, company):
        return self.env["rfid.device.config"]._find_selectable(company)

    @api.model_create_multi
    def create(self, vals_list):
        """Select a validated SI120X1 for each point's target company."""
        defaults_by_company = {}
        default_test_type_id = self.env.context.get("default_test_type_id")
        default_company_id = self.env.context.get("default_company_id")
        for vals in vals_list:
            test_type = self.env["quality.point.test_type"].browse(
                vals.get("test_type_id", default_test_type_id)
            )
            if test_type.technical_name != "rfid_write" or vals.get("rfid_device_id"):
                continue
            company = self.env["res.company"].browse(
                vals.get("company_id", default_company_id) or self.env.company.id
            )
            if company.id not in defaults_by_company:
                defaults_by_company[company.id] = self._find_default_rfid_device(company)
            if defaults_by_company[company.id]:
                vals["rfid_device_id"] = defaults_by_company[company.id].id
        return super().create(vals_list)

    @api.onchange("test_type_id", "company_id")
    def _onchange_test_type_id(self):
        if self.rfid_device_id and self.rfid_device_id.company_id != self.company_id:
            self.rfid_device_id = False
        if (
            self.test_type_id
            and self.test_type_id.technical_name == "rfid_write"
            and not self.rfid_device_id
            and self.company_id
        ):
            self.rfid_device_id = self._find_default_rfid_device(self.company_id)
