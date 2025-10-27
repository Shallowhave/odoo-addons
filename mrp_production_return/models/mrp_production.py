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

    @api.depends('move_raw_ids')
    def _compute_has_remaining_components(self):
        """计算是否有剩余组件"""
        for record in self:
            # 检查是否有未完全消耗的组件
            # 组件状态可能是 'done', 'assigned', 'partially_available'，且计划数量大于实际消耗数量
            remaining_components = record.move_raw_ids.filtered(
                lambda m: m.state in ('done', 'assigned', 'partially_available') and m.product_uom_qty > m.quantity
            )
            record.has_remaining_components = bool(remaining_components)

    @api.depends('move_raw_ids')
    def _compute_remaining_components_count(self):
        """计算剩余组件数量"""
        for record in self:
            # 计算未完全消耗的组件种类数量
            remaining_components = record.move_raw_ids.filtered(
                lambda m: m.state in ('done', 'assigned', 'partially_available') and m.product_uom_qty > m.quantity
            )
            record.remaining_components_count = len(remaining_components)

    def button_mark_done(self):
        """重写完成制造订单方法，检查剩余组件"""
        _logger.info(f"[DEBUG] button_mark_done 方法被调用")
        
        # 检查是否是从"无欠单"按钮调用的
        skip_backorder = self.env.context.get('skip_backorder', False)
        _logger.info(f"[DEBUG] skip_backorder 上下文: {skip_backorder}")
        
        for record in self:
            _logger.info(f"[DEBUG] 处理制造订单: {record.name}")
            
            # 只有在"无欠单"情况下才检查剩余组件
            if skip_backorder:
                # 调试信息：记录组件状态
                _logger.info(f"[剩余组件检测] 制造订单 {record.name} 的组件状态:")
                for move in record.move_raw_ids:
                    _logger.info(f"  组件: {move.product_id.name}, 状态: {move.state}, 计划数量: {move.product_uom_qty}, 实际数量: {move.quantity}, 剩余: {move.product_uom_qty - move.quantity}")
                
                # 检查是否有剩余组件需要处理
                # 包含更多可能的状态：done, assigned, partially_available
                remaining_components = record.move_raw_ids.filtered(
                    lambda m: m.state in ('done', 'assigned', 'partially_available') and m.product_uom_qty > m.quantity
                )
                
                # 也检查其他可能的状态
                all_components = record.move_raw_ids.filtered(
                    lambda m: m.product_uom_qty > m.quantity
                )
                _logger.info(f"[剩余组件检测] 所有有剩余的组件: {len(all_components)}")
                for comp in all_components:
                    _logger.info(f"  有剩余组件: {comp.product_id.name}, 状态: {comp.state}, 计划: {comp.product_uom_qty}, 实际: {comp.quantity}")
                
                _logger.info(f"[剩余组件检测] 找到 {len(remaining_components)} 个剩余组件")
                
                if remaining_components:
                    # 如果有剩余组件，打开处理向导
                    _logger.info(f"[剩余组件检测] 打开处理向导")
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
                else:
                    _logger.info(f"[剩余组件检测] 没有剩余组件，直接完成制造订单")
            else:
                _logger.info(f"[DEBUG] 非无欠单操作，直接调用原始方法")
        
        # 如果没有剩余组件或不是无欠单操作，调用原始方法
        _logger.info(f"[DEBUG] 调用原始 button_mark_done 方法")
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
            'view_mode': 'tree,form',
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