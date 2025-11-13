# -*- coding: utf-8 -*-
from odoo import models, fields, api


class StockPickingType(models.Model):
    _inherit = 'stock.picking.type'

    enable_delivery_report = fields.Boolean(
        string='启用交货单打印',
        default=False,
        help='启用后，在交货单表单和列表视图中显示"打印交货单"按钮'
    )

