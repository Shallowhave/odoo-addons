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
        """重写完成制造订单方法，检查剩余组件
        
        逻辑：
        1. 如果剩余组件已经全部退回处理，即使产成品数量不足，也自动选择"无欠单"完成
        2. 如果还有剩余组件未处理，在"无欠单"时弹出处理向导
        """
        # 检查是否是从"无欠单"按钮调用的
        skip_backorder = self.env.context.get('skip_backorder', False)
        # 检查是否是从欠单向导调用的（Odoo在创建欠单时会设置 mo_ids_to_backorder）
        mo_ids_to_backorder = self.env.context.get('mo_ids_to_backorder', [])
        # 检查是否已经在处理剩余组件（防止递归）
        processing_return = self.env.context.get('processing_return', False)
        
        # 只有在"无欠单"且不是创建欠单且不在处理返回时才触发
        # mo_ids_to_backorder 存在表示是"创建欠单"操作
        should_check_remaining = skip_backorder and not mo_ids_to_backorder and not processing_return
        
        # 为每个记录处理
        records_to_process = []
        for record in self:
            # 检查剩余组件是否已经全部处理
            remaining_components = record._get_unprocessed_remaining_components()
            all_components_returned = not remaining_components
            
            _logger.info(
                f"[制造订单完成] {record.name}: "
                f"skip_backorder={skip_backorder}, "
                f"mo_ids_to_backorder={mo_ids_to_backorder}, "
                f"processing_return={processing_return}, "
                f"all_components_returned={all_components_returned}"
            )
            
            # 如果剩余组件已经全部退回处理，且当前不是从"无欠单"按钮调用
            # 自动选择"无欠单"完成，不弹出欠单提示
            if all_components_returned and not skip_backorder and not mo_ids_to_backorder and not processing_return:
                # 如果剩余组件已全部退回，无论产成品数量是否不足，都自动选择"无欠单"完成
                # 因为用户已经处理完了所有剩余组件，不需要再创建欠单
                _logger.info(
                    f"[制造订单完成] {record.name} 剩余组件已全部退回，"
                    f"自动选择无欠单完成（不弹出欠单提示）"
                )
                # 直接调用父类方法，传入 skip_backorder=True，避免递归
                # 使用 processing_return=True 防止再次检查剩余组件
                result = super(MrpProduction, record.with_context(
                    skip_backorder=True,
                    processing_return=True
                )).button_mark_done()
                # 如果返回的是动作（如弹窗），应该直接返回
                if isinstance(result, dict):
                    return result
                continue
            
            # 只有在真正的"无欠单"情况下才检查剩余组件
            if should_check_remaining:
                # 使用优化后的方法获取剩余组件（避免重复查询）
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
            
            # 其他记录正常处理
            records_to_process.append(record)
        
        # 如果有需要正常处理的记录，使用原始上下文调用父类方法
        if records_to_process:
            return super(MrpProduction, self.browse([r.id for r in records_to_process])).button_mark_done()
        
        # 如果没有需要处理的记录，返回空结果
        return True

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