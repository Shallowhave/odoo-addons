from odoo import models, fields, api, _
from odoo.tools import html2plaintext


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _get_quality_alert_note(self, move, line):
        """Get the latest quality alert description for this picking/lot/product."""
        self.ensure_one()

        if line.lot_id:
            domain = [
                ('lot_id', '=', line.lot_id.id),
                ('description', '!=', False),
            ]
        else:
            domain = [
                ('picking_id', '=', self.id),
                ('product_id', '=', move.product_id.id),
                ('lot_id', '=', False),
                ('description', '!=', False),
            ]

        alert = self.env['quality.alert'].search(domain, order='id desc', limit=1)
        if alert.description:
            note = html2plaintext(alert.description).strip()
            if note:
                return note
        return '-'

    def _get_quality_info(self):
        """Get quality note content from quality alerts for report printing."""
        quality_info = []
        for group in self._get_report_move_line_groups():
            line = group['lines'][0]
            move = group['move']
            quality_note = self._get_quality_alert_note(move, line)
            quality_info.append({
                'product': group['product'].name,
                'product_code': group['product'].default_code or '',
                'lot_name': group['lot'].name if group['lot'] else '-',
                'quality_note': quality_note,
                'quantity': group['quantity'],
                'uom': group['uom'].name,
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
