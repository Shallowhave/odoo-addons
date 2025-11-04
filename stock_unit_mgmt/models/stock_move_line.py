# -*- coding: utf-8 -*-

from odoo import models, fields, api
from re import findall as regex_findall

from . import utils


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    # 单位数量字段
    lot_quantity = fields.Float(
        string='单位数量', 
        help='实际收到的单位数量',
        digits=(16, 2)  # 最多16位，小数点后2位
    )
    
    # 单位名称字段（动态选择）
    lot_unit_name = fields.Selection(
        selection='_get_lot_unit_name_selection',
        string='单位名称', 
        help='计量单位名称（如：桶、卷、件、箱等），根据产品配置显示可用选项'
    )
    
    # 自定义单位名称字段
    lot_unit_name_custom = fields.Char(
        string='自定义单位名称', 
        help='当选择"自定义"时填写具体的单位名称'
    )
    
    # 动态标签字段
    lot_weight_label = fields.Char(
        string='单位标签', 
        compute='_compute_lot_weight_label', 
        store=False
    )
    
    # 动态单位字段
    custom_unit_values = fields.Text(
        string='自定义单位值', 
        help='JSON格式存储的自定义单位值'
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
        
        if product_tmpl.default_unit_config == 'custom':
            return [('custom', '自定义')]
        
        # 根据产品配置返回对应的单位选项
        config_map = {
            'kg': [('kg', '公斤(kg)')],
            'roll': [('roll', '卷')],
            'barrel': [('barrel', '桶')],
            'box': [('box', '箱')],
            'bag': [('bag', '袋')],
            'sqm': [('sqm', '平方米(㎡)')],
        }
        
        return config_map.get(product_tmpl.default_unit_config, all_options)

    @api.depends('lot_unit_name', 'lot_unit_name_custom')
    def _compute_lot_weight_label(self):
        """根据选择的单位名称计算单位标签"""
        for record in self:
            if record.lot_unit_name:
                if record.lot_unit_name == 'custom':
                    record.lot_weight_label = record.lot_unit_name_custom or 'kg'
                else:
                    record.lot_weight_label = record.lot_unit_name
            else:
                record.lot_weight_label = 'kg'

    @api.onchange('product_id')
    def _onchange_product_id_custom_units(self):
        """当选择产品时，自动带入单位名称，但不修改原生计量单位字段"""
        result = super()._onchange_product_id() if hasattr(super(), '_onchange_product_id') else {}
        
        if self.product_id:
            product_tmpl = self.product_id.product_tmpl_id
            if hasattr(product_tmpl, 'enable_custom_units') and product_tmpl.enable_custom_units:
                if hasattr(product_tmpl, 'default_unit_config') and product_tmpl.default_unit_config:
                    if product_tmpl.default_unit_config == 'custom':
                        self.lot_unit_name = 'custom'
                        # 自定义单位名称从产品配置中获取
                        if hasattr(product_tmpl, 'quick_unit_name') and product_tmpl.quick_unit_name:
                            self.lot_unit_name_custom = product_tmpl.quick_unit_name
                    else:
                        self.lot_unit_name = product_tmpl.default_unit_config
                    
                    if not self.lot_quantity:
                        self.lot_quantity = 1
                
                elif hasattr(product_tmpl, 'custom_unit_name') and product_tmpl.custom_unit_name:
                    if product_tmpl.custom_unit_name == 'custom':
                        self.lot_unit_name = 'custom'
                        self.lot_unit_name_custom = product_tmpl.custom_unit_name_text or ''
                    else:
                        self.lot_unit_name = product_tmpl.custom_unit_name
                    
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
        
        if product_tmpl.default_unit_config != 'custom':
            if self.lot_unit_name != product_tmpl.default_unit_config:
                self.lot_unit_name = product_tmpl.default_unit_config
                return {
                    'warning': {
                        'title': '单位已自动调整',
                        'message': f'该产品已配置单位"{self._get_unit_display_name(product_tmpl.default_unit_config)}"，已自动调整为配置的单位。'
                    }
                }
        elif product_tmpl.default_unit_config == 'custom':
            custom_unit_name = product_tmpl.quick_unit_name if hasattr(product_tmpl, 'quick_unit_name') else None
            if self.lot_unit_name == 'custom' and not self.lot_unit_name_custom and custom_unit_name:
                self.lot_unit_name_custom = custom_unit_name
            elif self.lot_unit_name != 'custom':
                self.lot_unit_name = 'custom'
                if custom_unit_name:
                    self.lot_unit_name_custom = custom_unit_name
                return {
                    'warning': {
                        'title': '单位已自动调整',
                        'message': f'该产品已配置自定义单位"{custom_unit_name or "自定义"}"，已自动调整为自定义单位。'
                    }
                }
        
        return {}

    def _get_unit_display_name(self, unit_code):
        """获取单位显示名称"""
        return utils.get_unit_display_name(unit_code)

    @api.onchange('lot_name')
    def _onchange_lot_name(self):
        """当输入批次名称时，自动带入单位名称和默认值"""
        if self.lot_name and self.product_id:
            if hasattr(self.product_id.product_tmpl_id, 'custom_unit_name') and self.product_id.product_tmpl_id.custom_unit_name:
                if self.product_id.product_tmpl_id.custom_unit_name == 'custom':
                    self.lot_unit_name = self.product_id.product_tmpl_id.custom_unit_name_text or ''
                else:
                    self.lot_unit_name = self.product_id.product_tmpl_id.custom_unit_name
            
            if hasattr(self.product_id.product_tmpl_id, 'custom_unit_value') and self.product_id.product_tmpl_id.custom_unit_value:
                self.lot_quantity = int(self.product_id.product_tmpl_id.custom_unit_value)
            
            if not self.lot_quantity:
                self.lot_quantity = 1
