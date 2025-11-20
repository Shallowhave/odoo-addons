# -*- coding: utf-8 -*-
from odoo import models, fields


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    mrp_lot_prefix = fields.Char(
        string='批次号前缀',
        help='生产时自动生成批次号的前缀。如果留空，将使用全局配置的前缀。',
        size=10,
    )
