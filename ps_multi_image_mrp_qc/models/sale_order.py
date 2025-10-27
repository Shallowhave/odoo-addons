# -*- coding: utf-8 -*-

from odoo import models, fields


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    sum3_total_count = fields.Float(string='总条数', help='来自成品打包计件（Sum3）的总条数汇总')


