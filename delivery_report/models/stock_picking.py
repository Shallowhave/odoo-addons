from odoo import models, fields, api, _
from odoo.tools.misc import formatLang


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _get_report_move_line_groups(self):
        """Group tracked report lines without changing stock history."""
        self.ensure_one()
        groups = {}

        for move in self.move_ids_without_package:
            for line in move.move_line_ids:
                uom = line.product_uom_id or move.product_id.uom_id
                if line.lot_id:
                    key = (move.product_id.id, line.lot_id.id, uom.id)
                else:
                    # Untracked lines have no physical identity to aggregate by.
                    key = ('line', line.id)

                if key not in groups:
                    groups[key] = {
                        'move': move,
                        'product': move.product_id,
                        'lot': line.lot_id,
                        'uom': uom,
                        'lines': self.env['stock.move.line'],
                        'quantity': 0.0,
                        'package_names': [],
                    }

                group = groups[key]
                group['lines'] |= line
                group['quantity'] += float(line.quantity or 0.0)

                package = line.result_package_id or line.package_id
                if package and package.name not in group['package_names']:
                    group['package_names'].append(package.name)

        return list(groups.values())

    @api.model
    def _get_delivery_line_length(self, line):
        """Return the physical length represented by one move line."""
        quantity = float(line.quantity or 0.0)
        product_tmpl = line.product_id.product_tmpl_id
        width_mm = float(getattr(product_tmpl, 'product_width', 0.0) or 0.0)
        uom_name = (
            line.product_uom_id.name or line.product_id.uom_id.name or ''
        ).lower()
        is_area_uom = any(
            token in uom_name for token in ('平米', '平方米', 'sqm', 'm²')
        )
        is_length_uom = (
            '米' in uom_name or uom_name in ('m', 'meter', 'meters')
        ) and not is_area_uom

        if is_length_uom:
            return quantity
        if is_area_uom:
            return quantity / (width_mm / 1000.0) if width_mm else None

        product_length = float(
            getattr(product_tmpl, 'product_length', 0.0) or 0.0
        )
        return product_length * quantity if product_length else None

    @api.model
    def _format_delivery_report_number(self, value):
        return formatLang(self.env, value, digits=2, grouping=False)

    def _get_delivery_order_reference(self):
        self.ensure_one()
        if getattr(self, 'is_freeform_quant_delivery', False):
            return self.name
        return self.origin or self.name

    def _get_delivery_report_customer(self):
        self.ensure_one()
        freeform_customer = getattr(self, 'freeform_customer_id', False)
        return (
            freeform_customer
            or self.partner_id.commercial_partner_id
            or self.partner_id
        )

    def _get_lot_serial_info(self):
        """获取批次/序列号信息"""
        lot_info = []
        total_length = 0.0
        total_quantity = 0.0

        for group in self._get_report_move_line_groups():
            if not group['lot']:
                continue

            product = group['product']
            product_tmpl = product.product_tmpl_id
            line_lengths = [
                self._get_delivery_line_length(line)
                for line in group['lines']
            ]
            measured_lengths = [
                length for length in line_lengths if length is not None
            ]
            length_value = sum(measured_lengths)
            has_length = bool(measured_lengths)

            total_length += length_value
            total_quantity += group['quantity']

            lot_info.append({
                'product': product.name,
                'product_code': product.default_code or '',
                'lot_name': group['lot'].name,
                'quantity': group['quantity'],
                'uom': group['uom'].name,
                'thickness': getattr(product_tmpl, 'product_thickness', False) or '-',
                'width': getattr(product_tmpl, 'product_width', False) or '-',
                'length': length_value if has_length else '-',
                'length_display': (
                    self._format_delivery_report_number(length_value)
                    if has_length else '-'
                ),
                'package_name': ', '.join(group['package_names']) or '-',
            })

        # 将汇总信息添加到返回结果中
        return {
            'lot_info': lot_info,
            'total_length': total_length,
            'total_quantity': total_quantity,
        }

    can_print_delivery_report = fields.Boolean(
        string='可打印交货单',
        compute='_compute_can_print_delivery_report',
        store=False,
        help='根据作业类型配置判断是否可以打印交货单'
    )
    
    @api.depends('picking_type_id')
    def _compute_can_print_delivery_report(self):
        """计算是否可以打印交货单报告"""
        for picking in self:
            if not picking.picking_type_id:
                picking.can_print_delivery_report = False
                continue
            # 安全检查字段是否存在
            if hasattr(picking.picking_type_id, 'enable_delivery_report'):
                picking.can_print_delivery_report = picking.picking_type_id.enable_delivery_report
            else:
                picking.can_print_delivery_report = False
    
    def action_print_delivery_report(self):
        """打印交货单报告"""
        # 检查是否有权限打印（从列表视图调用时，self 可能包含多个记录）
        for picking in self:
            if not picking.picking_type_id:
                continue
            # 检查是否启用了交货单打印
            if hasattr(picking.picking_type_id, 'enable_delivery_report'):
                if not picking.picking_type_id.enable_delivery_report:
                    # 如果未启用，跳过此记录（不显示错误，因为可能是批量操作）
                    continue
        # 过滤出有权限的记录
        allowed_pickings = self.filtered(lambda p: p.picking_type_id and 
                                         hasattr(p.picking_type_id, 'enable_delivery_report') and 
                                         p.picking_type_id.enable_delivery_report)
        if not allowed_pickings:
            # 如果没有允许的记录，返回警告
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('警告'),
                    'message': _('当前选择的交货单未启用交货单打印功能。'),
                    'type': 'warning',
                    'sticky': False,
                }
            }
        # 只对允许的记录执行打印
        return self.env.ref('delivery_report.action_delivery_report').report_action(allowed_pickings)


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    def _get_lot_details(self):
        """获取批次详细信息"""
        if not self.lot_id:
            return {}
        
        return {
            'lot_name': self.lot_id.name,
            'product_name': self.product_id.name,
            'product_code': self.product_id.default_code or '',
            'quantity': self.quantity,
            'uom': self.product_id.uom_id.name,
            # Odoo 18 中 stock.lot 模型可能没有这些日期字段
            'expiration_date': False,
            'removal_date': False,
            'alert_date': False,
        }
