from odoo import models, fields, api, _


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _get_lot_serial_info(self):
        """获取批次/序列号信息"""
        lot_info = []
        total_length = 0.0
        total_quantity = 0.0
        
        for move in self.move_ids_without_package:
            if move.move_line_ids:
                for line in move.move_line_ids:
                    if line.lot_id:
                        # 安全获取产品模板的自定义属性
                        product_tmpl = move.product_id.product_tmpl_id
                        
                        # 使用 try-except 安全获取自定义字段
                        try:
                            thickness = getattr(product_tmpl, 'product_thickness', None)
                            thickness = thickness if thickness else '-'
                        except:
                            thickness = '-'
                            
                        try:
                            width = getattr(product_tmpl, 'product_width', None)
                            width = width if width else '-'
                        except:
                            width = '-'
                            
                        # 按本次交付移动行计算长度，避免使用整批库存 quant 长度。
                        length = '-'
                        length_value = 0.0
                        try:
                            delivered_qty = float(line.quantity or 0.0)
                            width_value = getattr(product_tmpl, 'product_width', 0.0) or 0.0
                            uom_name = (line.product_uom_id.name or move.product_id.uom_id.name or '').lower()
                            is_area_uom = any(token in uom_name for token in ('平米', '平方米', 'sqm', 'm²'))
                            is_length_uom = ('米' in uom_name or uom_name in ('m', 'meter', 'meters')) and not is_area_uom
                            if is_length_uom:
                                length_value = delivered_qty
                            elif is_area_uom and width_value:
                                length_value = delivered_qty / (width_value / 1000.0)
                            elif getattr(product_tmpl, 'product_length', False):
                                length_value = float(product_tmpl.product_length) * delivered_qty
                            if length_value:
                                length = length_value
                        except Exception:
                            length = '-'
                        
                        # 获取包裹信息
                        package_name = '-'
                        if line.result_package_id:
                            package_name = line.result_package_id.name
                        elif line.package_id:
                            package_name = line.package_id.name
                        
                        # 累加汇总值
                        total_length += length_value
                        total_quantity += float(line.quantity) if line.quantity else 0.0
                        
                        lot_info.append({
                            'product': move.product_id.name,
                            'product_code': move.product_id.default_code or '',
                            'lot_name': line.lot_id.name,
                            'quantity': line.quantity,
                            'uom': move.product_id.uom_id.name,
                            'thickness': thickness,
                            'width': width,
                            'length': length,
                            'package_name': package_name,
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
