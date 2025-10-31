# -*- coding: utf-8 -*-

from odoo import models, fields, api


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    # 单位名称字段
    lot_unit_name = fields.Selection([
        ('kg', '公斤(kg)'),
        ('roll', '卷'),
        ('barrel', '桶'),
        ('box', '箱'),
        ('bag', '袋'),
        ('sqm', '平方米(㎡)'),
        ('piece', '件'),
        ('custom', '自定义')
    ], string='单位名称', help='计量单位名称（如：桶、卷、件、箱等）', compute='_compute_lot_unit_info', store=True)
    
    # 自定义单位名称字段
    lot_unit_name_custom = fields.Char(
        string='自定义单位名称', 
        help='当选择"自定义"时填写具体的单位名称',
        compute='_compute_lot_unit_info', store=True
    )
    
    # 单位数量字段
    lot_quantity = fields.Float(
        string='单位数量', 
        help='实际收到的单位数量',
        compute='_compute_lot_unit_info', 
        store=True,
        digits=(16, 2)  # 最多16位，小数点后2位
    )
    
    # 格式化的单位显示
    lot_unit_display = fields.Char(
        string='附加单位',
        compute='_compute_lot_unit_display',
        help='格式化显示的附加单位信息，如："5 卷"'
    )
    
    # 备注字段
    o_note1 = fields.Char(string='备注1')
    o_note2 = fields.Char(string='备注2')
    contract_no = fields.Char(string='合同号')

    @api.depends('lot_id', 'product_id', 'quantity', 'location_id')
    def _compute_lot_unit_info(self):
        """从批次记录中获取单位信息，累加所有入库，减去所有出库"""
        for quant in self:
            if not quant.lot_id or not quant.product_id:
                quant.lot_unit_name = False
                quant.lot_unit_name_custom = False
                quant.lot_quantity = 0.0
                continue
            
            try:
                # 优化：一次性查找所有相关的移动行，然后分别筛选
                all_move_lines = self.env['stock.move.line'].search([
                    ('lot_id', '=', quant.lot_id.id),
                    ('product_id', '=', quant.product_id.id),
                    ('state', '=', 'done')
                ])
                
                # 筛选入库和出库移动行
                incoming_move_lines = all_move_lines.filtered(
                    lambda ml: ml.location_dest_id.id == quant.location_id.id and 
                    ml.lot_quantity and ml.lot_quantity > 0
                )
                
                outgoing_move_lines = all_move_lines.filtered(
                    lambda ml: ml.location_id.id == quant.location_id.id and 
                    ml.lot_quantity and ml.lot_quantity > 0
                )
                
                # 累加入库的单位数量
                total_incoming = sum(incoming_move_lines.mapped('lot_quantity') or [0.0])
                
                # 累加出库的单位数量
                total_outgoing = sum(outgoing_move_lines.mapped('lot_quantity') or [0.0])
                
                # 计算当前剩余的单位数量
                current_lot_quantity = total_incoming - total_outgoing
                
                # 如果还有库存但单位数量为0或负数，说明可能出库时没有填写单位数量
                # 在这种情况下，按比例计算
                if quant.quantity > 0 and current_lot_quantity <= 0 and total_incoming > 0:
                    # 找到总的入库数量
                    total_incoming_qty = sum(incoming_move_lines.mapped('qty_done') or [0.0]) or \
                                        sum(incoming_move_lines.mapped('quantity') or [0.0])
                    if total_incoming_qty > 0:
                        # 按比例计算：当前库存数量 / 总入库数量 * 总入库单位数量
                        current_lot_quantity = (quant.quantity / total_incoming_qty) * total_incoming
                
                # 取最新的移动行来获取单位名称（优先入库，其次出库）
                latest_move_line = all_move_lines.filtered(
                    lambda ml: ml.lot_unit_name
                ).sorted(key='id', reverse=True)[:1]
                
                if latest_move_line:
                    quant.lot_unit_name = latest_move_line.lot_unit_name
                    quant.lot_unit_name_custom = latest_move_line.lot_unit_name_custom
                    quant.lot_quantity = max(0.0, current_lot_quantity)  # 确保不为负数
                else:
                    # 如果找不到移动行，尝试从产品配置获取
                    product_tmpl = quant.product_id.product_tmpl_id
                    if hasattr(product_tmpl, 'get_unit_config_for_stock_move'):
                        try:
                            unit_configs = product_tmpl.get_unit_config_for_stock_move()
                            if unit_configs:
                                config = unit_configs[0]
                                quant.lot_unit_name = config['name']
                                quant.lot_quantity = 0.0
                            else:
                                quant.lot_unit_name = False
                                quant.lot_unit_name_custom = False
                                quant.lot_quantity = 0.0
                        except Exception:
                            quant.lot_unit_name = False
                            quant.lot_unit_name_custom = False
                            quant.lot_quantity = 0.0
                    else:
                        quant.lot_unit_name = False
                        quant.lot_unit_name_custom = False
                        quant.lot_quantity = 0.0
            except Exception:
                # 错误处理：确保即使出错也不会导致系统崩溃
                quant.lot_unit_name = False
                quant.lot_unit_name_custom = False
                quant.lot_quantity = 0.0
    
    @api.depends('lot_quantity', 'lot_unit_name', 'lot_unit_name_custom')
    def _compute_lot_unit_display(self):
        """计算格式化的单位显示"""
        for quant in self:
            if quant.lot_quantity and quant.lot_unit_name:
                unit_map = {
                    'kg': '公斤',
                    'roll': '卷',
                    'barrel': '桶',
                    'box': '箱',
                    'bag': '袋',
                    'sqm': '㎡',
                    'piece': '件',
                }
                if quant.lot_unit_name == 'custom':
                    unit_name = quant.lot_unit_name_custom or '单位'
                else:
                    unit_name = unit_map.get(quant.lot_unit_name, quant.lot_unit_name)
                quant.lot_unit_display = f"{quant.lot_quantity} {unit_name}"
            else:
                quant.lot_unit_display = ""
