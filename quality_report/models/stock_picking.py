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

    def action_print_quality_report(self):
        """打印品质报告"""
        return self.env.ref('quality_report.action_quality_report').report_action(self)
