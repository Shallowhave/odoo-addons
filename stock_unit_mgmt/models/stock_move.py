# -*- coding: utf-8 -*-

from odoo import models, fields, api


class StockMove(models.Model):
    _inherit = 'stock.move'

    # 计算字段：总单位数量
    lot_quantity = fields.Float(string='总单位数量', compute='_compute_lot_quantity', digits=(16, 2))
    
    # 计算字段：单位名称
    lot_unit_name = fields.Char(string='单位名称', compute='_compute_lot_unit_name')

    @api.depends('move_line_ids.lot_quantity')
    def _compute_lot_quantity(self):
        """计算总单位数量"""
        for move in self:
            move.lot_quantity = sum(move.move_line_ids.mapped('lot_quantity') or [0.0])

    @api.depends('move_line_ids.lot_unit_name')
    def _compute_lot_unit_name(self):
        """计算单位名称"""
        for move in self:
            unit_names = move.move_line_ids.mapped('lot_unit_name')
            move.lot_unit_name = next((name for name in unit_names if name), '')

    def _action_done(self, cancel_backorder=False):
        """完成库存移动时，将单位信息传递到库存数量记录"""
        result = super()._action_done(cancel_backorder)
        
        # 收集需要更新计算的 stock_quant 记录
        quants_to_recompute = self.env['stock.quant']
        
        for move in self:
            for move_line in move.move_line_ids:
                # 只要有批次号就触发重新计算，即使 lot_quantity 为空
                # 因为 lot_quantity 可能在扫码时填写，需要重新计算
                if move_line.lot_id:
                    # 查找相关的 stock_quant 记录
                    # 优先匹配目标位置（入库），如果没有找到再尝试源位置（出库）
                    domain = [
                        ('product_id', '=', move_line.product_id.id),
                        ('lot_id', '=', move_line.lot_id.id),
                    ]
                    
                    # owner_id 处理：如果移动行有 owner_id，则匹配；否则匹配 owner_id 为空的记录
                    if move_line.owner_id:
                        domain.append(('owner_id', '=', move_line.owner_id.id))
                    else:
                        domain.append(('owner_id', '=', False))
                    
                    # 优先查找目标位置的 quant（入库）
                    quants_found = False
                    if move_line.location_dest_id:
                        quants = self.env['stock.quant'].search(domain + [
                            ('location_id', '=', move_line.location_dest_id.id)
                        ])
                        if quants:
                            quants_to_recompute |= quants
                            quants_found = True
                    
                    # 如果没有找到，尝试源位置的 quant（出库或内部移动）
                    if not quants_found and move_line.location_id:
                        quants = self.env['stock.quant'].search(domain + [
                            ('location_id', '=', move_line.location_id.id)
                        ])
                        if quants:
                            quants_to_recompute |= quants
                            quants_found = True
                    
                    # 如果还是没找到，尝试不限制位置（可能位置不匹配）
                    if not quants_found:
                        quants = self.env['stock.quant'].search(domain)
                        if quants:
                            quants_to_recompute |= quants
        
        # 触发所有相关的 stock_quant 重新计算
        if quants_to_recompute:
            # 批量触发计算字段重新计算（优化性能）
            quants_to_recompute.invalidate_recordset(['lot_quantity', 'lot_unit_name', 'lot_unit_name_custom'])
            quants_to_recompute._compute_lot_unit_info()
        
        return result

    @api.model
    def split_lots(self, lots):
        """分割批次号，支持单位数量"""
        breaking_char = '\n'
        separation_char = '\t'
        options = False

        if not lots:
            return []

        split_lines = lots.split(breaking_char)
        split_lines = list(filter(None, split_lines))
        move_lines_vals = []
        for lot_text in split_lines:
            move_line_vals = {
                'lot_name': lot_text,
                'quantity': 1,
            }
            lot_text_parts = lot_text.replace(';', separation_char).split(separation_char)
            options = options or self._get_formating_options(lot_text_parts[1:] if len(lot_text_parts) > 1 else [])
            for extra_string in (lot_text_parts[1] if len(lot_text_parts) > 1 else []):
                field_data = self._convert_string_into_field_data(extra_string, options)
                if field_data:
                    lot_text = lot_text_parts[0]
                    lot_quantity = int(lot_text_parts[-1]) if lot_text_parts[-1].isdigit() else 1
                    if field_data == "ignore":
                        move_line_vals.update(lot_name=lot_text, lot_quantity=lot_quantity)
                    else:
                        move_line_vals.update(**field_data, lot_name=lot_text, lot_quantity=lot_quantity)
                else:
                    move_line_vals['lot_name'] = lot_text
                    break
            move_lines_vals.append(move_line_vals)
        return move_lines_vals
