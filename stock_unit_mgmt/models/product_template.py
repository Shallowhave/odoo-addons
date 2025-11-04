# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError

from . import utils


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # ==================== 产品尺寸和材料属性字段 ====================
    product_width = fields.Integer(
        string="宽度 (mm)",
        help="产品宽度，单位：毫米"
    )
    
    product_thickness = fields.Integer(
        string="厚度 (μm)",
        help="产品厚度，单位：微米"
    )
    
    product_length = fields.Float(
        string="长度 (m)",
        help="产品长度，单位：米",
        digits=(12,2)
    )

    # 成品专用字段：材料密度（成品单独使用）
    finished_density = fields.Float(
        string="材料密度 (kg/cm³)",
        help="成品材料密度，单位：千克/立方厘米（如：PE=0.00092, PET=0.00138, PP=0.00090）",
        digits=(12, 6)
    )

    # 配液原料专用字段
    solution_solid_content = fields.Float(
        string="固含(%)",
        help="配液原料的固含量百分比",
        digits=(12, 2)
    )
    
    solution_viscosity = fields.Float(
        string="粘度值",
        help="配液原料的粘度值",
        digits=(12, 2)
    )
    
    # 成品膜专用字段：发货重量系数
    weight_per_sqm = fields.Float(
        string="发货重量系数 (g/㎡)",
        help="成品膜发货时的重量系数，单位：克/平方米。用于计算发货重量 = 面积 × 重量系数",
        digits=(12, 2)
    )

    # ==================== 计算字段 ====================
    product_area = fields.Float(
        string="面积 (㎡)",
        compute='_compute_product_area',
        store=True,
        help="产品面积，单位：平方米"
    )
    
    product_volume = fields.Float(
        string="体积 (cm³)",
        compute='_compute_product_volume',
        store=True,
        help="产品体积，单位：立方厘米"
    )
    
    weight_per_meter = fields.Float(
        string="单位重量 (kg/m²)",
        help="单位面积的重量，单位：千克/平方米",
        digits=(12, 4)
    )


    # ==================== 安全库存管理 ====================
    safty_qty = fields.Float(string='安全库存', default=0.0)
    is_safty = fields.Boolean(string='是否安全', default=True, compute='_compute_is_safty')
    safty_rule = fields.Selection([
        ('not_note1','不包括备注1'),
        ('not_note2','不包括备注2'),
        ('all','包括所有'),
        ('not_all','都不包括')
    ], string='安全库存规则', default='not_all')

    # ==================== 库存统计字段 ====================
    lot_weight = fields.Float(string='重量', default=0.0, compute='_compute_lot_weight')
    lot_qty = fields.Float(string='第二单位数量', default=0.0, compute='_compute_lot_weight')
    act_juan = fields.Integer(string='实际卷数', default=0, compute='_compute_lot_weight')
    o_note = fields.Char(string='备注卷数', default='', compute='_compute_o_note')

    # ==================== 通用单位配置系统 ====================
    enable_custom_units = fields.Boolean(
        string="启用自定义单位",
        default=False,
        help="启用后可以在库存移动时填写具体的单位信息"
    )
    
    # 附加单位配置（用于快速设置常用单位）
    default_unit_config = fields.Selection([
        ('kg', '公斤(kg)'),
        ('roll', '卷'),
        ('barrel', '桶'),
        ('box', '箱'),
        ('bag', '袋'),
        ('sqm', '平方米(㎡)'),
        ('custom', '自定义')
    ], string="附加单位模板",
       help="选择常用的附加单位模板，如：卷、桶、箱等。收货时可以从这些单位中选择")
    
    # 自定义单位名称（当选择"自定义"时使用）
    quick_unit_name = fields.Char(
        string="自定义单位名称",
        help="当附加单位模板选择'自定义'时填写，如：托、包、组等"
    )
    
    # 附加单位库存汇总
    total_lot_quantity = fields.Float(
        string="附加单位在手",
        compute='_compute_total_lot_quantity',
        help="所有批次的附加单位数量汇总",
        digits=(16, 2)  # 最多16位，小数点后2位
    )
    

    # ==================== 计算字段方法 ====================
    @api.depends('product_width', 'product_length')
    def _compute_product_area(self):
        """计算产品面积"""
        for record in self:
            if record.product_width and record.product_length:
                # 宽度(mm) * 长度(m) / 1000 = 面积(㎡)
                record.product_area = (record.product_width * record.product_length) / 1000
            else:
                record.product_area = 0.0

    @api.depends('product_width', 'product_length', 'product_thickness')
    def _compute_product_volume(self):
        """计算产品体积"""
        for record in self:
            if record.product_width and record.product_length and record.product_thickness:
                # 宽度(mm) * 长度(m转为mm) * 厚度(μm转为mm) = 体积(mm³) 再转为 cm³
                # 200mm * 10000mm * 0.002mm = 4000mm³ = 4cm³
                width_mm = record.product_width
                length_mm = record.product_length * 1000  # m转mm
                thickness_mm = record.product_thickness / 1000  # μm转mm
                volume_mm3 = width_mm * length_mm * thickness_mm
                record.product_volume = volume_mm3 / 1000  # mm³转cm³
            else:
                record.product_volume = 0.0

    @api.depends('product_variant_ids.stock_quant_ids', 'product_variant_ids.stock_quant_ids.o_note1', 
                 'product_variant_ids.stock_quant_ids.o_note2')
    def _compute_o_note(self):
        """计算备注卷数"""
        for product in self:
            # 优化：直接使用已加载的 stock_quant_ids，避免重复查询
            all_quants = product.product_variant_ids.mapped('stock_quant_ids')
            quants = all_quants.filtered(
                lambda q: q.location_id.usage == 'internal' and q.quantity > 0
            )
            # 收集所有备注1和备注2的内容
            all_notes = []
            for quant in quants:
                if quant.o_note1:
                    all_notes.append(quant.o_note1)
                if quant.o_note2:
                    all_notes.append(quant.o_note2)
            
            # 统计相同备注内容的数量
            note_counts = {}
            for note in all_notes:
                if note:  # 确保备注不为空
                    note_counts[note] = note_counts.get(note, 0) + 1
            
            # 生成统计结果字符串，格式：数量1, 数量2, ...
            if note_counts:
                product.o_note = ', '.join([str(count) for count in note_counts.values()])
            else:
                product.o_note = ''

    @api.depends('safty_qty', 'safty_rule', 'product_variant_ids.stock_quant_ids', 
                 'product_variant_ids.stock_quant_ids.inventory_quantity_auto_apply',
                 'product_variant_ids.stock_quant_ids.o_note1', 'product_variant_ids.stock_quant_ids.o_note2')
    def _compute_is_safty(self):
        """计算是否安全库存"""
        for product in self:
            if product.safty_qty <= 0:
                product.is_safty = True
                continue
                
            # 优化：直接使用已加载的 stock_quant_ids，避免重复查询
            all_quants = product.product_variant_ids.mapped('stock_quant_ids')
            quants = all_quants.filtered(lambda q: q.location_id.usage == 'internal')
            
            qty = 0
            if product.safty_rule == 'all':
                qty = sum(quants.mapped('inventory_quantity_auto_apply'))
            elif product.safty_rule == 'not_note1':
                qty = sum(quants.filtered(lambda x: not x.o_note1).mapped('inventory_quantity_auto_apply'))
            elif product.safty_rule == 'not_note2':
                qty = sum(quants.filtered(lambda x: not x.o_note2).mapped('inventory_quantity_auto_apply'))
            elif product.safty_rule == 'not_all':
                qty = sum(quants.filtered(lambda x: not x.o_note1 and not x.o_note2).mapped('inventory_quantity_auto_apply'))

            product.is_safty = qty >= product.safty_qty

    @api.depends('product_variant_ids.stock_quant_ids', 
                 'product_variant_ids.stock_quant_ids.lot_id', 
                 'product_variant_ids.stock_quant_ids.inventory_quantity_auto_apply',
                 'product_variant_ids.stock_quant_ids.quantity',
                 'product_variant_ids.stock_quant_ids.location_id.usage')
    def _compute_lot_weight(self):
        """计算库存重量和数量统计"""
        for product in self:
            # 优化：直接使用已加载的 stock_quant_ids，避免重复查询
            all_quants = product.product_variant_ids.mapped('stock_quant_ids')
            quants = all_quants.filtered(lambda q: q.location_id.usage == 'internal')
            if quants:
                # lot_weight 字段保留但可能在其他模块中定义，使用 hasattr 检查
                product.lot_weight = sum(q.lot_weight for q in quants if hasattr(q, 'lot_weight') and q.lot_weight) or 0.0
                # lot_qty 字段保留但不再使用（第二单位系统已删除）
                product.lot_qty = 0.0
            else:
                product.lot_weight = 0.0
                product.lot_qty = 0.0
            # 实际卷数：按照批号/序列号计算，每个批号/序列号都算作一卷，但需要判断在手数量大于0
            product.act_juan = len(quants.filtered(lambda x: x.lot_id and x.inventory_quantity_auto_apply > 0))

    # ==================== 单位配置方法 ====================
    def get_unit_config_for_stock_move(self):
        """获取库存移动时的单位配置"""
        self.ensure_one()
        if not self.enable_custom_units:
            return []
        
        configs = []
        
        # 快速配置：返回可用的附加单位（收货时手动输入数量）
        if self.default_unit_config and self.default_unit_config != 'custom':
            configs.append({
                'name': self.default_unit_config,
                'label': self._get_unit_display_name(self.default_unit_config)
            })
        elif self.default_unit_config == 'custom' and self.quick_unit_name:
            configs.append({
                'name': 'custom',
                'label': self.quick_unit_name
            })
        
        return configs

    def _get_unit_display_name(self, unit_code):
        """获取单位显示名称"""
        return utils.get_unit_display_name(unit_code)
    
    def action_quick_unit_setup(self):
        """打开快速单位设置向导"""
        self.ensure_one()
        return {
            'name': _('快速单位设置'),
            'type': 'ir.actions.act_window',
            'res_model': 'product.unit.setup.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_product_tmpl_id': self.id,
                'default_product_name': self.name,
            }
        }

    # ==================== 验证方法 ====================
    @api.constrains('product_width', 'product_length', 'product_thickness')
    def _check_product_dimensions(self):
        """验证产品尺寸"""
        for record in self:
            if record.product_width and record.product_width <= 0:
                raise UserError(_("产品宽度必须大于0"))
            if record.product_length and record.product_length <= 0:
                raise UserError(_("产品长度必须大于0"))
            if record.product_thickness and record.product_thickness <= 0:
                raise UserError(_("产品厚度必须大于0"))

    @api.constrains('finished_density')
    def _check_finished_density(self):
        """验证成品密度"""
        for record in self:
            if record.finished_density and record.finished_density <= 0:
                raise UserError(_("成品密度必须大于0"))

    @api.constrains('solution_solid_content')
    def _check_solution_solid_content(self):
        """验证固含量"""
        for record in self:
            if record.solution_solid_content and (record.solution_solid_content < 0 or record.solution_solid_content > 100):
                raise UserError(_("固含量必须在0-100之间"))

    @api.depends('enable_custom_units', 'product_variant_ids.stock_quant_ids', 
                 'product_variant_ids.stock_quant_ids.lot_quantity',
                 'product_variant_ids.stock_quant_ids.quantity',
                 'product_variant_ids.stock_quant_ids.location_id.usage')
    def _compute_total_lot_quantity(self):
        """计算所有批次的附加单位总数量"""
        for template in self:
            if not template.enable_custom_units:
                template.total_lot_quantity = 0.0
                continue
            
            # 优化：直接使用已加载的 stock_quant_ids，避免重复查询
            all_quants = template.product_variant_ids.mapped('stock_quant_ids')
            quants = all_quants.filtered(
                lambda q: q.location_id.usage == 'internal' and q.quantity > 0
            )
            
            total = sum(quants.mapped('lot_quantity') or [0.0])
            template.total_lot_quantity = total if total else 0.0

