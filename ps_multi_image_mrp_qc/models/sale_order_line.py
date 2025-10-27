# -*- coding: utf-8 -*-

from odoo import models, fields


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # 实际生产数量字段
    actual_production_qty = fields.Float(
        string='实际生产数量', 
        help='从质量检查同步的实际生产数量',
        default=0.0
    )
    
    # 保留原有字段以兼容
    sum3_total_count = fields.Float(string='产品总条数', help='该销售行对应产品的成品打包计件总条数')


