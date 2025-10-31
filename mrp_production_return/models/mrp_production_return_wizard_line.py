# -*- coding: utf-8 -*-
from odoo import models, fields, api


class MrpProductionReturnWizardLine(models.TransientModel):
    _name = 'mrp.production.return.wizard.line'
    _description = '制造订单剩余组件返回向导行'

    wizard_id = fields.Many2one(
        'mrp.production.return.wizard',
        string='向导',
        required=True,
        ondelete='cascade'
    )
    move_id = fields.Many2one(
        'stock.move',
        string='库存移动',
        required=True
        # 注意：不在模型中设置 readonly，只在视图中设置
        # 这样 default_get 创建记录时才能正常传递 move_id
    )
    product_id = fields.Many2one(
        'product.product',
        string='组件',
        related='move_id.product_id',
        readonly=True
    )
    product_uom_id = fields.Many2one(
        'uom.uom',
        string='计量单位',
        related='move_id.product_uom',
        readonly=True
    )
    expected_qty = fields.Float(
        string='计划数量',
        related='move_id.product_uom_qty',
        readonly=True
    )
    consumed_qty = fields.Float(
        string='已消耗数量',
        related='move_id.quantity',
        readonly=True
    )
    remaining_qty = fields.Float(
        string='剩余数量',
        compute='_compute_remaining_qty',
        readonly=True
    )
    return_qty = fields.Float(
        string='返回数量',
        required=True,
        help='要返回的组件数量'
    )

    @api.depends('expected_qty', 'consumed_qty')
    def _compute_remaining_qty(self):
        """计算剩余数量"""
        for record in self:
            record.remaining_qty = record.expected_qty - record.consumed_qty

    @api.model
    def default_get(self, fields_list):
        """设置默认值"""
        res = super().default_get(fields_list)
        if 'return_qty' in fields_list:
            res['return_qty'] = res.get('remaining_qty', 0)
        return res
    
    @api.model_create_multi
    def create(self, vals_list):
        """重写创建方法，过滤掉没有move_id的记录"""
        # 只保留有 move_id 的记录（防止 Odoo 自动保存机制创建无效记录）
        valid_vals_list = [vals for vals in vals_list if vals.get('move_id')]
        
        if not valid_vals_list:
            # 如果没有有效记录，返回空记录集（不报错）
            return self.browse()
        
        # 创建有效记录
        return super().create(valid_vals_list)
