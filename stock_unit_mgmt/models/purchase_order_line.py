# -*- coding: utf-8 -*-

from odoo import models


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'
    
    # 预留：未来可添加采购订单行的扩展字段
