from odoo import models, fields, api, _


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _get_quality_info(self):
        """获取品质信息"""
        quality_info = []
        for move in self.move_ids_without_package:
            if move.move_line_ids:
                for line in move.move_line_ids:
                    if line.lot_id:
                        # 获取品质备注（从产品制造中的质量描述）
                        quality_note = '-'
                        try:
                            # 尝试从产品模板获取质量描述
                            if hasattr(move.product_id.product_tmpl_id, 'description') and move.product_id.product_tmpl_id.description:
                                quality_note = move.product_id.product_tmpl_id.description
                            # 如果产品模板没有描述，尝试从产品本身获取
                            elif hasattr(move.product_id, 'description') and move.product_id.description:
                                quality_note = move.product_id.description
                        except AttributeError:
                            pass  # 字段不存在
                        
                        quality_info.append({
                            'product': move.product_id.name,
                            'product_code': move.product_id.default_code or '',
                            'lot_name': line.lot_id.name,
                            'quality_note': quality_note,
                            'quantity': line.quantity,
                            'uom': move.product_id.uom_id.name,
                        })
        return quality_info

    can_print_quality_report = fields.Boolean(
        string='可打印品质报告',
        compute='_compute_can_print_quality_report',
        store=False,
        help='根据作业类型配置判断是否可以打印品质报告'
    )
    
    @api.depends('picking_type_id')
    def _compute_can_print_quality_report(self):
        """计算是否可以打印品质报告"""
        for picking in self:
            if not picking.picking_type_id:
                picking.can_print_quality_report = False
                continue
            # 安全检查字段是否存在
            if hasattr(picking.picking_type_id, 'enable_quality_report'):
                picking.can_print_quality_report = picking.picking_type_id.enable_quality_report
            else:
                picking.can_print_quality_report = False
    
    def action_print_quality_report(self):
        """打印品质报告"""
        # 检查是否有权限打印（从列表视图调用时，self 可能包含多个记录）
        for picking in self:
            if not picking.picking_type_id:
                continue
            # 检查是否启用了品质报告打印
            if hasattr(picking.picking_type_id, 'enable_quality_report'):
                if not picking.picking_type_id.enable_quality_report:
                    # 如果未启用，跳过此记录（不显示错误，因为可能是批量操作）
                    continue
        # 过滤出有权限的记录
        allowed_pickings = self.filtered(lambda p: p.picking_type_id and 
                                         hasattr(p.picking_type_id, 'enable_quality_report') and 
                                         p.picking_type_id.enable_quality_report)
        if not allowed_pickings:
            # 如果没有允许的记录，返回警告
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('警告'),
                    'message': _('当前选择的交货单未启用品质报告打印功能。'),
                    'type': 'warning',
                    'sticky': False,
                }
            }
        # 只对允许的记录执行打印
        return self.env.ref('quality_report.action_quality_report').report_action(allowed_pickings)
