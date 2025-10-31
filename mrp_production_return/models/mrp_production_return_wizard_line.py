# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import ValidationError


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
    
    @api.constrains('return_qty', 'remaining_qty')
    def _check_return_qty(self):
        """验证返回数量"""
        for record in self:
            # 检查负数
            if record.return_qty < 0:
                raise ValidationError(
                    f'组件 {record.product_id.name} 的返回数量不能为负数！\n'
                    f'当前输入：{record.return_qty}'
                )
            
            # 检查是否超过剩余数量（允许小的浮点误差）
            if record.return_qty > record.remaining_qty + 0.0001:
                raise ValidationError(
                    f'组件 {record.product_id.name} 的返回数量不能超过剩余数量！\n'
                    f'剩余数量：{record.remaining_qty} {record.product_uom_id.name}\n'
                    f'您输入的返回数量：{record.return_qty} {record.product_uom_id.name}\n'
                    f'请修改为不超过 {record.remaining_qty} 的值。'
                )
    
    @api.onchange('return_qty')
    def _onchange_return_qty(self):
        """返回数量变更时的实时提示"""
        if self.return_qty and self.remaining_qty:
            if self.return_qty < 0:
                return {
                    'warning': {
                        'title': '数量错误',
                        'message': '返回数量不能为负数！'
                    }
                }
            if self.return_qty > self.remaining_qty + 0.0001:
                return {
                    'warning': {
                        'title': '数量超限',
                        'message': (
                            f'返回数量 {self.return_qty} 超过剩余数量 {self.remaining_qty}！\n'
                            f'最大可返回：{self.remaining_qty} {self.product_uom_id.name}'
                        )
                    }
                }
    
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
