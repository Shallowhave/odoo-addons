# -*- coding: utf-8 -*-
from odoo import models, fields, api


class StockPickingType(models.Model):
    _inherit = 'stock.picking.type'

    enable_quality_report = fields.Boolean(
        string='启用品质报告打印',
        default=False,
        help='启用后，在交货单表单和列表视图中显示"打印品质报告"按钮'
    )

