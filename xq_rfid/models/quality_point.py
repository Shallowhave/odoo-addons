# -*- coding: utf-8 -*-
##############################################################################
#
# Grit - ifangtech.com
# Copyright (C) 2024 (https://ifangtech.com)
#
##############################################################################

from odoo import fields, models


class QualityPoint(models.Model):
    _inherit = 'quality.point'
    
    # RFID 设备配置（可选）
    rfid_device_required = fields.Boolean(
        string='需要 RFID 设备',
        help='启用后，生成 RFID 时将调用硬件设备接口进行写入操作'
    )

