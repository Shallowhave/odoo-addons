# -*- coding: utf-8 -*-

from odoo import models, fields, api
import math


class StockMove(models.Model):
    _inherit = 'stock.move'

    # 覆盖 product_uom_qty 字段，设置3位小数精度（仅影响显示）
    # 注意：这会影响所有 stock.move，但主要是为了在制造订单组件列表中显示3位小数
    product_uom_qty = fields.Float(
        digits=(16, 3),  # 3位小数精度
    )

    @api.model_create_multi
    def create(self, vals_list):
        """覆盖 create 方法，对制造订单的组件移动使用向下取整"""
        # 在创建前修正数量
        for vals in vals_list:
            if 'product_uom_qty' in vals and vals.get('product_uom_qty'):
                # 检查是否是制造订单的组件移动
                if vals.get('raw_material_production_id'):
                    original_qty = vals['product_uom_qty']
                    
                    # 获取 UOM 的 rounding 值
                    uom = None
                    if 'product_uom' in vals and vals['product_uom']:
                        uom = self.env['uom.uom'].browse(vals['product_uom'])
                    elif 'product_id' in vals and vals['product_id']:
                        product = self.env['product.product'].browse(vals['product_id'])
                        if product.exists() and product.uom_id:
                            uom = product.uom_id
                    
                    if uom and uom.exists() and uom.rounding and uom.rounding > 0:
                        # 使用向下取整
                        rounded_qty = math.floor(original_qty / uom.rounding) * uom.rounding
                        if abs(rounded_qty - original_qty) > 1e-10:
                            vals['product_uom_qty'] = rounded_qty
        
        moves = super(StockMove, self).create(vals_list)
        
        # 创建后再次检查并修正（以防创建时没有正确获取 UOM）
        for move in moves:
            if move.raw_material_production_id and move.product_uom_qty:
                uom = move.product_uom
                if uom and uom.rounding and uom.rounding > 0:
                    original_qty = move.product_uom_qty
                    rounded_qty = math.floor(original_qty / uom.rounding) * uom.rounding
                    if abs(rounded_qty - original_qty) > 1e-10:
                        move.sudo().write({'product_uom_qty': rounded_qty})
                        move.invalidate_recordset(['product_uom_qty'])
        
        return moves

    def write(self, vals):
        """覆盖 write 方法，对制造订单的组件移动使用向下取整"""
        # 如果设置了 product_uom_qty，且是制造订单的组件移动，使用向下取整
        if 'product_uom_qty' in vals and vals.get('product_uom_qty'):
            for move in self:
                # 只处理制造订单的组件移动（原材料移动）
                if move.raw_material_production_id:
                    original_qty = vals['product_uom_qty']
                    # 获取 UOM 的 rounding 值
                    uom = move.product_uom or (move.product_id and move.product_id.uom_id)
                    if not uom and 'product_uom' in vals:
                        uom = self.env['uom.uom'].browse(vals['product_uom'])
                    
                    if uom and uom.rounding and uom.rounding > 0:
                        # 使用向下取整
                        rounded_qty = math.floor(original_qty / uom.rounding) * uom.rounding
                        if abs(rounded_qty - original_qty) > 1e-10:
                            vals['product_uom_qty'] = rounded_qty
        
        return super(StockMove, self).write(vals)

    # 计算字段：总单位数量
    lot_quantity = fields.Float(string='总单位数量', compute='_compute_lot_quantity', digits=(16, 2))
    
    # 计算字段：单位名称
    lot_unit_name = fields.Char(string='单位名称', compute='_compute_lot_unit_name')
    
    # 计算字段：总发货重量
    total_delivery_weight = fields.Float(
        string='总发货重量 (kg)',
        compute='_compute_total_delivery_weight',
        digits=(16, 2),
        help='所有移动行的发货重量汇总，单位：千克'
    )

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

    @api.depends('move_line_ids.delivery_weight')
    def _compute_total_delivery_weight(self):
        """计算总发货重量"""
        for move in self:
            move.total_delivery_weight = sum(move.move_line_ids.mapped('delivery_weight') or [0.0])

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
            # **关键修复**：添加 contract_no 到无效化字段列表，确保合同号也会重新计算
            quants_to_recompute.invalidate_recordset(['lot_quantity', 'lot_unit_name', 'lot_unit_name_custom', 'contract_no'])
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
