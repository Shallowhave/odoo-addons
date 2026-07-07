# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.tools import float_round


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    contract_no = fields.Char(
        string='合同号',
        help='制造订单的合同号，会自动传递到库存移动行和库存数量记录'
    )

    def _prepare_move_raw(self, bom_line, line_data):
        """覆盖方法，对组件数量使用向下取整而不是四舍五入"""
        res = super(MrpProduction, self)._prepare_move_raw(bom_line, line_data)
        
        # 如果返回了 product_uom_qty，使用 Odoo 的 UoM 精度工具向下取整
        if res and 'product_uom_qty' in res and res.get('product_uom_qty'):
            original_qty = res['product_uom_qty']

            # 获取 UOM 的 rounding 值
            uom = None
            if 'product_uom' in res and res['product_uom']:
                uom = self.env['uom.uom'].browse(res['product_uom']).exists()
            elif 'product_id' in res and res['product_id']:
                product = self.env['product.product'].browse(res['product_id']).exists()
                if product and product.uom_id:
                    uom = product.uom_id

            if uom and uom.rounding and uom.rounding > 0:
                rounded_qty = float_round(
                    original_qty,
                    precision_rounding=uom.rounding,
                    rounding_method='DOWN',
                )
                if rounded_qty != original_qty:
                    res['product_uom_qty'] = rounded_qty
        
        return res

