# -*- coding: utf-8 -*-

from odoo import models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'
    
    # 预留：未来可添加销售订单行的扩展字段
