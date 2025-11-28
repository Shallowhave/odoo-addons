# -*- coding: utf-8 -*-
from odoo import models, _


class QualityCheck(models.Model):
    _inherit = 'quality.check'

    def action_print_label(self):
        self.ensure_one()

        production = False
        if self.production_id:
            production = self.production_id
        elif self.workorder_id and getattr(self.workorder_id, 'production_id', False):
            production = self.workorder_id.production_id

        if not production:
            return False

        # 从质检点类型判断打印哪张标签
        test_type = False
        if getattr(self, 'test_type', False):
            test_type = self.test_type
        if not test_type and self.point_id and self.point_id.test_type_id:
            test_type = getattr(self.point_id.test_type_id, 'technical_name', False)

        ctx = dict(self.env.context or {})
        paper = self.env.ref('xq_mrp_label.paperformat_100x100', raise_if_not_found=False)
        if paper:
            ctx['force_paperformat_id'] = paper.id

        if test_type == 'product_label':
            xmlid = 'xq_mrp_label.action_report_mrp_label'
            action = self.env.ref(xmlid)
            return action.with_context(**ctx).report_action(production.ids)
        elif test_type == 'qc_label':
            xmlid = 'xq_mrp_label.action_report_mrp_qc_label'
            action = self.env.ref(xmlid)
            return action.with_context(**ctx).report_action(production.ids)
        elif test_type == 'byproduct_label':
            # 副产品标签：需要选择要打印的副产品
            return self._action_print_byproduct_label(production, ctx)
        else:
            return False

    def _action_print_byproduct_label(self, production, ctx):
        """打印副产品标签"""
        # 首先检查是否有副产品移动记录
        all_byproduct_moves = production.move_byproduct_ids
        
        if not all_byproduct_moves:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('提示'),
                    'message': _('该制造订单没有配置副产品，无法打印副产品标签！'),
                    'type': 'warning',
                    'sticky': False,
                }
            }
        
        # 获取制造订单的所有副产品移动记录（放宽状态过滤条件）
        # 允许更多状态：done, assigned, confirmed, waiting, partially_available
        # 只要数量大于0即可
        byproduct_moves = all_byproduct_moves.filtered(
            lambda m: m.state in ('done', 'assigned', 'confirmed', 'waiting', 'partially_available') and m.product_uom_qty > 0
        )
        
        # 如果过滤后没有符合条件的副产品，检查是否有其他状态的副产品
        if not byproduct_moves:
            # 检查是否有数量为0的副产品
            zero_qty_moves = all_byproduct_moves.filtered(
                lambda m: m.product_uom_qty <= 0
            )
            if zero_qty_moves:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('提示'),
                        'message': _('该制造订单的副产品数量为0，无法打印副产品标签！'),
                        'type': 'warning',
                        'sticky': False,
                    }
                }
            
            # 检查是否有其他状态的副产品
            other_state_moves = all_byproduct_moves.filtered(
                lambda m: m.state not in ('done', 'assigned', 'confirmed', 'waiting', 'partially_available')
            )
            if other_state_moves:
                states = ', '.join(set(other_state_moves.mapped('state')))
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('提示'),
                        'message': _('该制造订单的副产品状态为：%s，无法打印标签。请等待副产品移动完成后再试。') % states,
                        'type': 'warning',
                        'sticky': False,
                    }
                }
            
            # 如果都没有，说明确实没有可用的副产品
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('提示'),
                    'message': _('该制造订单没有可用的副产品，无法打印副产品标签！'),
                    'type': 'warning',
                    'sticky': False,
                }
            }
        
        # 如果只有一个副产品，直接打印
        if len(byproduct_moves) == 1:
            byproduct_move = byproduct_moves[0]
            # 在 context 中传递副产品移动记录的 ID，而不是记录对象
            ctx['byproduct_move_id'] = byproduct_move.id
            xmlid = 'xq_mrp_label.action_report_mrp_byproduct_label'
            action = self.env.ref(xmlid)
            return action.with_context(**ctx).report_action(production.ids)
        
        # 如果有多个副产品，显示向导让用户选择
        return {
            'type': 'ir.actions.act_window',
            'name': _('选择副产品'),
            'res_model': 'byproduct.label.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_production_id': production.id,
                'default_quality_check_id': self.id,
            }
        }


