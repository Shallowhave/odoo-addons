# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.tools import float_compare
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class MrpProduction(models.Model):
    _inherit = 'mrp.production'

    # 新增字段
    return_history_ids = fields.One2many(
        'mrp.production.return.history',
        'production_id',
        string='返回历史',
        help='记录剩余组件返回的历史'
    )
    has_remaining_components = fields.Boolean(
        string='有剩余组件',
        compute='_compute_has_remaining_components',
        help='是否有剩余组件需要处理'
    )
    remaining_components_count = fields.Integer(
        string='剩余组件数量',
        compute='_compute_remaining_components_count',
        help='当前剩余组件种类数量'
    )

    def _get_unprocessed_remaining_components(self):
        """获取未处理的剩余组件（复用方法，避免重复查询）"""
        self.ensure_one()
        
        # 获取剩余组件
        remaining_components = self.move_raw_ids.filtered(
            lambda m: m.state in ('done', 'assigned', 'partially_available') 
            and m.product_uom_qty > m.quantity
        )
        
        if not remaining_components:
            return self.env['stock.move']
        
        # 获取已处理的产品（只查询一次，通过 return_history_ids 关系）
        processed_products = self.return_history_ids.mapped('product_id')
        
        # 过滤掉已处理的组件
        if processed_products:
            remaining_components = remaining_components.filtered(
                lambda m: m.product_id not in processed_products
            )
        
        return remaining_components

    @api.depends('move_raw_ids', 'return_history_ids')
    def _compute_has_remaining_components(self):
        """计算是否有剩余组件"""
        for record in self:
            record.has_remaining_components = bool(
                record._get_unprocessed_remaining_components()
            )

    @api.depends('move_raw_ids', 'return_history_ids')
    def _compute_remaining_components_count(self):
        """计算剩余组件数量"""
        for record in self:
            record.remaining_components_count = len(
                record._get_unprocessed_remaining_components()
            )

    def button_mark_done(self):
        """重写完成制造订单方法，检查剩余组件"""
        # 检查是否是从"无欠单"按钮调用的
        skip_backorder = self.env.context.get('skip_backorder', False)
        # 检查是否是从欠单向导调用的（Odoo在创建欠单时会设置 mo_ids_to_backorder）
        mo_ids_to_backorder = self.env.context.get('mo_ids_to_backorder', [])
        # 检查是否已经在处理剩余组件（防止递归）
        processing_return = self.env.context.get('processing_return', False)
        
        # 只有在"无欠单"且不是创建欠单且不在处理返回时才触发
        # mo_ids_to_backorder 存在表示是"创建欠单"操作
        should_check_remaining = skip_backorder and not mo_ids_to_backorder and not processing_return
        
        for record in self:
            # 只有在真正的"无欠单"情况下才检查剩余组件
            if should_check_remaining:
                # 使用优化后的方法获取剩余组件（避免重复查询）
                remaining_components = record._get_unprocessed_remaining_components()
                
                if remaining_components:
                    # 记录关键信息（只在有剩余组件时）
                    _logger.info(
                        f"制造订单 {record.name} 有 {len(remaining_components)} 个剩余组件待处理：" +
                        ", ".join(remaining_components.mapped('product_id.name'))
                    )
                    
                    # 打开处理向导
                    return {
                        'type': 'ir.actions.act_window',
                        'name': f'处理剩余组件 - {record.name}',
                        'res_model': 'mrp.production.return.wizard',
                        'view_mode': 'form',
                        'target': 'new',
                        'context': {
                            'default_production_id': record.id,
                        }
                    }
        
        # 如果没有剩余组件或不是无欠单操作，调用原始方法
        return super().button_mark_done()

    def action_return_components(self):
        """处理剩余组件返回"""
        self.ensure_one()
        
        # 验证状态
        if self.state not in ('progress', 'to_close'):
            raise UserError('只有进行中或待关闭的制造订单才能处理剩余组件')
        
        # 检查是否有剩余组件需要处理
        if not self.has_remaining_components:
            # 如果没有剩余组件，直接完成制造单
            if self.state not in ('done', 'cancel'):
                self.button_mark_done()
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': '提示',
                    'message': '没有剩余组件需要处理，制造订单已完成。',
                    'type': 'success',
                }
            }
        
        # 打开剩余组件返回向导
        return {
            'type': 'ir.actions.act_window',
            'name': f'处理剩余组件 - {self.name}',
            'res_model': 'mrp.production.return.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_production_id': self.id,
            }
        }

    def action_view_return_history(self):
        """查看返回历史"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'返回历史 - {self.name}',
            'res_model': 'mrp.production.return.history',
            'view_mode': 'list,form',
            'domain': [('production_id', '=', self.id)],
            'context': {'default_production_id': self.id},
        }

    def action_batch_return_products(self):
        """批量处理剩余产品"""
        return {
            'type': 'ir.actions.act_window',
            'name': '批量处理剩余产品',
            'res_model': 'mrp.production.batch.return.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_production_ids': self.ids,
            }
        }