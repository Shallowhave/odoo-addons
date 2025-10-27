from odoo import models, fields, api, _


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _get_lot_serial_info(self):
        """获取批次/序列号信息"""
        lot_info = []
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
                            
                        try:
                            length = getattr(product_tmpl, 'product_length', None)
                            length = length if length else '-'
                        except:
                            length = '-'
                        
                        lot_info.append({
                            'product': move.product_id.name,
                            'product_code': move.product_id.default_code or '',
                            'lot_name': line.lot_id.name,
                            'quantity': line.quantity,
                            'uom': move.product_id.uom_id.name,
                            'thickness': thickness,
                            'width': width,
                            'length': length,
                        })
        return lot_info

    def action_print_delivery_report(self):
        """打印交货单报告"""
        return self.env.ref('delivery_report.action_delivery_report').report_action(self)


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
