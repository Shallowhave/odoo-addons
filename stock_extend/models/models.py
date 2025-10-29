# -*- coding: utf-8 -*-

from odoo import models, fields, api
from re import findall as regex_findall

class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    # lot_weight 字段已移除，使用 lot_quantity 替代
    lot_quantity = fields.Integer(string='单位件数', help='实际收到的单位数量')
    lot_unit_name = fields.Selection(
        selection='_get_lot_unit_name_selection',
        string='单位名称', 
        help='计量单位名称（如：桶、卷、件、箱等），根据产品配置显示可用选项'
    )
    
    @api.model
    def _get_lot_unit_name_selection(self):
        """根据产品配置动态获取单位选择列表"""
        # 所有可用的单位选项
        all_options = [
            ('kg', '公斤(kg)'),
            ('roll', '卷'),
            ('barrel', '桶'),
            ('box', '箱'),
            ('bag', '袋'),
            ('sqm', '平方米(㎡)'),
            ('piece', '件'),
            ('custom', '自定义')
        ]
        
        # 尝试从当前记录获取产品
        product_id = None
        if hasattr(self, 'product_id') and self.product_id:
            product_id = self.product_id.id
        elif hasattr(self, '_origin') and self._origin and self._origin.product_id:
            product_id = self._origin.product_id.id
        else:
            # 从上下文获取产品ID（对于新建记录）
            product_id = self.env.context.get('default_product_id')
        
        if not product_id:
            return all_options
        
        # 获取产品
        product = self.env['product.product'].browse(product_id)
        if not product.exists():
            return all_options
        
        # 获取产品配置的单位
        product_tmpl = product.product_tmpl_id
        if not hasattr(product_tmpl, 'default_unit_config') or not product_tmpl.default_unit_config:
            return all_options
        
        # 如果配置了自定义单位，需要检查是否有自定义单位名称
        if product_tmpl.default_unit_config == 'custom':
            if product_tmpl.quick_unit_name:
                # 返回自定义单位选项
                return [('custom', '自定义')]
            else:
                # 没有填写自定义单位名称，返回所有选项
                return all_options
        
        # 返回配置的单位选项
        config_map = {
            'kg': [('kg', '公斤(kg)')],
            'roll': [('roll', '卷')],
            'barrel': [('barrel', '桶')],
            'box': [('box', '箱')],
            'bag': [('bag', '袋')],
            'sqm': [('sqm', '平方米(㎡)')],
        }
        
        return config_map.get(product_tmpl.default_unit_config, all_options)
    
    
    # 自定义单位名称字段
    lot_unit_name_custom = fields.Char(string='自定义单位名称', help='当选择"自定义"时填写具体的单位名称')
    
    # 动态标签字段
    lot_weight_label = fields.Char(string='单位标签', compute='_compute_lot_weight_label', store=False)
    
    # 动态单位字段
    custom_unit_values = fields.Text(string='自定义单位值', help='JSON格式存储的自定义单位值')
    
    @api.depends('lot_unit_name', 'lot_unit_name_custom')
    def _compute_lot_weight_label(self):
        """根据选择的单位名称计算单位标签"""
        for record in self:
            if record.lot_unit_name:
                if record.lot_unit_name == 'custom':
                    # 如果选择自定义，使用自定义文本
                    record.lot_weight_label = record.lot_unit_name_custom or 'kg'
                else:
                    # 使用选择的值
                    record.lot_weight_label = record.lot_unit_name
            else:
                record.lot_weight_label = 'kg'
    
    @api.onchange('product_id')
    def _onchange_product_id_custom_units(self):
        """当选择产品时，自动带入单位名称，但不修改原生计量单位字段"""
        # 调用父类的方法
        result = super()._onchange_product_id() if hasattr(super(), '_onchange_product_id') else {}
        
        if self.product_id:
            # 使用附加单位配置
            product_tmpl = self.product_id.product_tmpl_id
            if hasattr(product_tmpl, 'enable_custom_units') and product_tmpl.enable_custom_units:
                if hasattr(product_tmpl, 'default_unit_config') and product_tmpl.default_unit_config:
                    # 根据配置自动设置单位名称
                    if product_tmpl.default_unit_config == 'custom':
                        # 自定义单位
                        if product_tmpl.quick_unit_name:
                            self.lot_unit_name = 'custom'
                            self.lot_unit_name_custom = product_tmpl.quick_unit_name
                    else:
                        # 使用配置的单位
                        self.lot_unit_name = product_tmpl.default_unit_config
                    
                    # 设置默认单位数量为1
                    if not self.lot_quantity:
                        self.lot_quantity = 1
            
            # 回退到旧的方法（兼容性）
            elif hasattr(product_tmpl, 'custom_unit_name') and product_tmpl.custom_unit_name:
                if product_tmpl.custom_unit_name == 'custom':
                    # 如果选择的是自定义，使用自定义文本
                    self.lot_unit_name = 'custom'
                    self.lot_unit_name_custom = product_tmpl.custom_unit_name_text or ''
                else:
                    # 使用预设的单位名称
                    self.lot_unit_name = product_tmpl.custom_unit_name
            
            # 设置默认单位数量为1
            if not self.lot_quantity:
                self.lot_quantity = 1
        
        return result
    
    @api.onchange('lot_unit_name', 'product_id')
    def _onchange_lot_unit_name(self):
        """当手动修改单位时，验证是否与产品配置匹配"""
        if not self.product_id or not self.lot_unit_name:
            return {}
        
        product_tmpl = self.product_id.product_tmpl_id
        if not hasattr(product_tmpl, 'enable_custom_units') or not product_tmpl.enable_custom_units:
            return {}
        
        if not hasattr(product_tmpl, 'default_unit_config') or not product_tmpl.default_unit_config:
            return {}
        
        # 如果产品已配置单位，但用户选择的单位与配置不匹配，自动纠正
        if product_tmpl.default_unit_config != 'custom':
            if self.lot_unit_name != product_tmpl.default_unit_config:
                # 自动纠正为产品配置的单位
                self.lot_unit_name = product_tmpl.default_unit_config
                return {
                    'warning': {
                        'title': '单位已自动调整',
                        'message': f'该产品已配置单位"{self._get_unit_display_name(product_tmpl.default_unit_config)}"，已自动调整为配置的单位。'
                    }
                }
        elif product_tmpl.default_unit_config == 'custom':
            # 自定义单位需要填写自定义名称
            if self.lot_unit_name == 'custom' and not self.lot_unit_name_custom and product_tmpl.quick_unit_name:
                self.lot_unit_name_custom = product_tmpl.quick_unit_name
            elif self.lot_unit_name != 'custom':
                # 如果产品配置了自定义单位，但用户选择了其他单位，纠正为自定义
                self.lot_unit_name = 'custom'
                if product_tmpl.quick_unit_name:
                    self.lot_unit_name_custom = product_tmpl.quick_unit_name
                return {
                    'warning': {
                        'title': '单位已自动调整',
                        'message': f'该产品已配置自定义单位"{product_tmpl.quick_unit_name or "自定义"}"，已自动调整为自定义单位。'
                    }
                }
        
        return {}
    
    def _get_unit_display_name(self, unit_code):
        """获取单位显示名称"""
        unit_map = {
            'kg': '公斤(kg)',
            'roll': '卷',
            'barrel': '桶',
            'box': '箱',
            'bag': '袋',
            'sqm': '平方米(㎡)',
            'piece': '件',
            'custom': '自定义'
        }
        return unit_map.get(unit_code, unit_code)

    @api.onchange('lot_name')
    def _onchange_lot_name(self):
        """当输入批次名称时，自动带入单位名称和默认值"""
        if self.lot_name and self.product_id:
            # 从产品模板获取单位名称
            if hasattr(self.product_id.product_tmpl_id, 'custom_unit_name') and self.product_id.product_tmpl_id.custom_unit_name:
                if self.product_id.product_tmpl_id.custom_unit_name == 'custom':
                    # 如果选择的是自定义，使用自定义文本
                    self.lot_unit_name = self.product_id.product_tmpl_id.custom_unit_name_text or ''
                else:
                    # 使用预设的单位名称
                    self.lot_unit_name = self.product_id.product_tmpl_id.custom_unit_name
            
            # 从产品模板获取默认单位数量
            if hasattr(self.product_id.product_tmpl_id, 'custom_unit_value') and self.product_id.product_tmpl_id.custom_unit_value:
                self.lot_quantity = int(self.product_id.product_tmpl_id.custom_unit_value)
            
            # 设置默认单位数量为1
            if not self.lot_quantity:
                self.lot_quantity = 1

    # 注释掉自动计算逻辑，让"单位数量"和"数量"字段独立填写
    # @api.onchange('lot_quantity')
    # def _onchange_custom_units(self):
    #     """当手动输入单位数量时，根据自定义规则更新数量"""
    #     # 不再自动计算，让用户独立填写"单位数量"和"数量"字段
    #     pass


class StockMove(models.Model):
    _inherit = 'stock.move'
    lot_quantity = fields.Integer(string='总单位数量', compute='_compute_lot_quantity')
    lot_unit_name = fields.Char(string='单位名称', compute='_compute_lot_unit_name')
    
    def _compute_lot_quantity(self):
        for move in self:
            move.lot_quantity = sum(move.move_line_ids.mapped('lot_quantity'))
    
    def _compute_lot_unit_name(self):
        for move in self:
            unit_names = move.move_line_ids.mapped('lot_unit_name')
            # 取第一个非空的单位名称
            move.lot_unit_name = next((name for name in unit_names if name), '')
    
    def _action_done(self, cancel_backorder=False):
        """重写完成动作，将手动输入的数据传递到库存记录"""
        result = super()._action_done(cancel_backorder)
        
        # 将手动输入的数据传递到stock.quant
        for move in self:
            for move_line in move.move_line_ids:
                if move_line.lot_quantity:
                    # 查找对应的库存记录
                    quants = self.env['stock.quant'].search([
                        ('product_id', '=', move_line.product_id.id),
                        ('lot_id', '=', move_line.lot_id.id),
                        ('location_id', '=', move_line.location_dest_id.id),
                        ('owner_id', '=', move_line.owner_id.id),
                    ])
                    
                    for quant in quants:
                        # 更新库存记录的单位信息
                        update_vals = {
                            'lot_unit_name': move_line.lot_unit_name,
                            'lot_unit_name_custom': move_line.lot_unit_name_custom,
                            'lot_quantity': move_line.lot_quantity,
                        }
                        quant.write(update_vals)
        
        return result

    @api.model
    def split_lots(self, lots):
        breaking_char = '\n'
        separation_char = '\t'
        options = False

        if not lots:
            return []  # Skip if the `lot_name` doesn't contain multiple values.

        # Checks the lines and prepares the move lines' values.
        split_lines = lots.split(breaking_char)
        split_lines = list(filter(None, split_lines))
        move_lines_vals = []
        for lot_text in split_lines:
            move_line_vals = {
                'lot_name': lot_text,
                'quantity': 1,
            }
            # Semicolons are also used for separation but for convenience we
            # replace them to work only with tabs.
            lot_text_parts = lot_text.replace(';', separation_char).split(separation_char)
            options = options or self._get_formating_options(lot_text_parts[1:] if len(lot_text_parts) > 1 else [])
            for extra_string in (lot_text_parts[1] if len(lot_text_parts) > 1 else []):
                field_data = self._convert_string_into_field_data(extra_string, options)
                if field_data:
                    lot_text = lot_text_parts[0]
                    lot_quantity = int(lot_text_parts[-1]) if lot_text_parts[-1].isdigit() else 1
                    if field_data == "ignore":
                        # Got an unusable data for this move, updates only the lot_name part.
                        move_line_vals.update(lot_name=lot_text, lot_quantity=lot_quantity)
                    else:
                        move_line_vals.update(**field_data, lot_name=lot_text, lot_quantity=lot_quantity)
                else:
                    # At least this part of the string is erronous and can't be converted,
                    # don't try to guess and simply use the full string as the lot name.
                    move_line_vals['lot_name'] = lot_text
                    break
            move_lines_vals.append(move_line_vals)
        return move_lines_vals
    

class StockQuant(models.Model):
    _inherit = 'stock.quant'
    
    # 单位信息字段
    lot_unit_name = fields.Selection([
        ('kg', '公斤(kg)'),
        ('roll', '卷'),
        ('barrel', '桶'),
        ('box', '箱'),
        ('bag', '袋'),
        ('sqm', '平方米(㎡)'),
        ('piece', '件'),
        ('custom', '自定义')
    ], string='单位名称', help='计量单位名称（如：桶、卷、件、箱等）')
    
    lot_unit_name_custom = fields.Char(string='自定义单位名称', help='当选择"自定义"时填写具体的单位名称')
    
    # 注意：lot_weight字段已移除，只定义单位相关字段
    lot_quantity = fields.Integer(string='数量', help='实际收到的单位数量')
    
    @api.depends('lot_id', 'product_id')
    def _compute_lot_unit_info(self):
        """从批次记录中获取单位信息"""
        for quant in self:
            if quant.lot_id and quant.product_id:
                # 查找最近的库存移动行来获取单位信息
                move_line = self.env['stock.move.line'].search([
                    ('lot_id', '=', quant.lot_id.id),
                    ('state', '=', 'done'),
                    ('lot_unit_name', '!=', False)
                ], limit=1, order='id desc')
                
                if move_line:
                    quant.lot_unit_name = move_line.lot_unit_name
                    quant.lot_unit_name_custom = move_line.lot_unit_name_custom
                    quant.lot_quantity = move_line.lot_quantity
                else:
                    # 如果没有找到移动行，尝试从产品配置获取默认单位
                    if hasattr(quant.product_id.product_tmpl_id, 'get_unit_config_for_stock_move'):
                        try:
                            unit_configs = quant.product_id.product_tmpl_id.get_unit_config_for_stock_move()
                            if unit_configs:
                                config = unit_configs[0]
                                quant.lot_unit_name = config['name']
                                quant.lot_quantity = 0
                            else:
                                quant.lot_unit_name = False
                                quant.lot_unit_name_custom = False
                                quant.lot_quantity = 0
                        except Exception:
                            quant.lot_unit_name = False
                            quant.lot_unit_name_custom = False
                            quant.lot_quantity = 0
                    else:
                        # 如果没有找到，设置为默认值
                        quant.lot_unit_name = False
                        quant.lot_unit_name_custom = False
                        quant.lot_quantity = 0


