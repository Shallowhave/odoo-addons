# -*- coding: utf-8 -*-

from odoo import models, fields, api


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    contract_no = fields.Char(
        string='合同号',
        help='制造订单的合同号，会自动传递到库存移动行和库存数量记录'
    )

