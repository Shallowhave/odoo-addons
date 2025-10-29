# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError


# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ProductTemplate(models.Model):
    _inherit = 'product.template'
    shipping_weight_coefficient = fields.Float(string='发货重量系数', default=0.0)
    # 产品类型：区分原膜、成品膜、配液原料和默认
    product_type = fields.Selection([
        ('default', '默认'),
        ('raw_material', '原膜'),
        ('finished_product', '成品膜'),
        ('solution_material', '配液原料')
    ], string="产品类型", default='default', required=True,
       help="选择产品类型")

    # 产品尺寸和材料属性字段
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
        string="材料密度 (g/cm³)",
        help="成品材料密度，单位：克/立方厘米（如：PE=0.92, PET=1.38, PP=0.90）",
        digits=(12, 3)
    )

    # 配液原料专用字段
    solution_solid_content = fields.Float(
        string="固含 (%)",
        help="配液原料的固含量，单位：百分比",
        digits=(5, 2)
    )
    
    solution_viscosity = fields.Float(
        string="粘度值",
        help="配液原料的粘度值",
        digits=(12, 3)
    )

    # 通用单位配置系统
    enable_custom_units = fields.Boolean(
        string="启用自定义单位",
        default=False,
        help="启用后可以在库存移动时填写具体的单位信息"
    )
    
    # 默认单位配置（用于快速设置）
    default_unit_config = fields.Selection([
        ('kg', '公斤(kg)'),
        ('roll', '卷'),
        ('barrel', '桶'),
        ('box', '箱'),
        ('bag', '袋'),
        ('sqm', '平方米(㎡)'),
        ('custom', '自定义')
    ], string="默认单位配置",
       help="选择常用的单位配置模板")
    
    # 快速配置字段（根据默认配置自动显示）
    quick_unit_name = fields.Char(
        string="单位名称",
        help="如：卷、桶、箱、袋等"
    )
    
    quick_unit_value = fields.Float(
        string="单位数值",
        digits=(12, 3),
        help="如：每卷重量、每桶重量等"
    )
    
    quick_unit_type = fields.Selection([
        ('weight', '重量(kg)'),
        ('area', '面积(㎡)'),
        ('volume', '体积(m³)'),
        ('length', '长度(m)'),
        ('quantity', '数量(件)'),
        ('custom', '自定义')
    ], string="单位类型",
       help="选择单位类型")
    
    # 采购单位相关字段（保留兼容性）
    purchase_unit_type = fields.Selection([
        ('roll', '按卷采购'),
        ('kg', '按公斤采购'),
        ('sqm', '按平方采购'),
        ('custom', '自定义采购单位')
    ], string="采购单位类型", 
       help="选择产品的采购单位类型")
    
    # 原膜按卷采购：每卷重量
    roll_weight_per_unit = fields.Float(
        string="每卷重量 (kg)",
        help="原膜按卷采购时，每卷的重量",
        digits=(12, 3)
    )
    
    # 配液原料按kg采购：每桶重量
    barrel_weight_per_unit = fields.Float(
        string="每桶重量 (kg)",
        help="配液原料按kg采购时，每桶的重量",
        digits=(12, 3)
    )
    
    # 半成品原膜按平方采购：每卷面积
    roll_area_per_unit = fields.Float(
        string="每卷面积 (㎡)",
        help="半成品原膜按平方采购时，每卷的面积",
        digits=(12, 3)
    )
    
    # 自定义采购单位
    custom_purchase_unit_name = fields.Char(
        string="自定义采购单位名称",
        help="自定义采购单位的名称，如：箱、包、袋等"
    )
    
    custom_purchase_unit_value = fields.Float(
        string="自定义采购单位值",
        help="自定义采购单位的数值",
        digits=(12, 3)
    )

    # 成品计算字段：每米重量
    weight_per_meter = fields.Float(
        string="每米重量 (kg/m)",
        compute='_compute_weight_per_meter',
        store=True,
        readonly=False,
        help="成品每米的重量，单位：千克/米",
        digits=(12, 3)
    )

    # 计算字段：体积
    product_volume = fields.Integer(
        string="体积 (cm³)",
        compute='_compute_product_volume',
        store=True,
        help="根据长宽高计算的产品体积"
    )

    # 计算字段：面积
    product_area = fields.Float(
        string="面积 (㎡)",
        compute='_compute_product_area',
        store=True,
        readonly=False,
        help="根据长度和宽度计算的产品面积",
        digits=(12, 3)
    )

    # 单位比例显示字段
    unit_ratio_display = fields.Char(
        string="单位比例",
        compute='_compute_unit_ratio_display',
        store=False,
        help="显示产品的单位转换比例，如：1箱=24瓶"
    )

    # 基础单位信息
    base_unit_name = fields.Char(
        string="基础单位",
        compute='_compute_unit_info',
        store=False,
        help="产品的基础计量单位名称"
    )

    # 包装单位信息
    package_unit_name = fields.Char(
        string="包装单位",
        compute='_compute_unit_info',
        store=False,
        help="产品的包装计量单位名称"
    )

    # 转换比例
    conversion_ratio = fields.Float(
        string="转换比例",
        compute='_compute_unit_info',
        store=False,
        help="一个包装单位包含多少个基础单位"
    )

    @api.depends('product_width', 'product_thickness', 'product_length', 'product_type')
    def _compute_product_volume(self):
        """计算产品体积 (cm³) - 仅适用于原料"""
        for product in self:
            # 成品不计算体积
            if product.product_type == 'finished_product':
                product.product_volume = 0
                continue

            if product.product_width and product.product_thickness and product.product_length:
                # 单位转换：长度(m) × 宽度(mm) × 厚度(μm) → cm³
                length_cm = product.product_length * 100  # m → cm
                width_cm = product.product_width * 0.1    # mm → cm
                thickness_cm = product.product_thickness * 0.0001  # μm → cm
                volume_cm3 = length_cm * width_cm * thickness_cm
                product.product_volume = int(volume_cm3)
            else:
                product.product_volume = 0

    @api.depends('product_width', 'product_thickness', 'finished_density', 'product_type')
    def _compute_weight_per_meter(self):
        """计算成品每米重量 (kg/m)"""
        for product in self:
            if product.product_type != 'finished_product':
                product.weight_per_meter = 0.0
                continue

            if product.product_width and product.product_thickness and product.finished_density:
                length_cm = 100
                width_cm = product.product_width * 0.1
                thickness_cm = product.product_thickness * 0.0001
                volume_cm3_per_meter = length_cm * width_cm * thickness_cm
                weight_g = volume_cm3_per_meter * product.finished_density
                weight_kg = weight_g / 1000
                product.weight_per_meter = round(weight_kg, 3)
            else:
                product.weight_per_meter = 0.0

    @api.depends('product_length', 'product_width', 'product_type')
    def _compute_product_area(self):
        """计算产品面积 (㎡)"""
        for product in self:
            if product.product_type == 'finished_product':
                # 成品膜：每米面积 = 宽度
                if product.product_width and product.product_width > 0:
                    width_m = product.product_width / 1000.0
                    product.product_area = round(1.0 * width_m, 3)
                else:
                    product.product_area = 0.0
            elif product.product_type == 'raw_material':
                # 原膜：面积 = 长度 × 宽度
                if product.product_length and product.product_width:
                    length_m = product.product_length
                    width_m = product.product_width / 1000.0
                    product.product_area = round(length_m * width_m, 3)
                else:
                    product.product_area = 0.0
            elif product.product_type == 'default':
                # 默认类型：不计算自定义面积，使用标准 Odoo 字段
                product.product_area = 0.0
            else:
                # 配液原料等其他类型不计算面积
                product.product_area = 0.0

    @api.onchange('product_width', 'product_length', 'product_type')
    def _onchange_product_area(self):
        if self.product_type == 'finished_product':
            if self.product_width and self.product_width > 0:
                self.product_area = round(self.product_width / 1000.0, 3)
            else:
                self.product_area = 0.0
        elif self.product_type == 'raw_material':
            if self.product_length and self.product_length > 0 and self.product_width and self.product_width > 0:
                self.product_area = round(self.product_length * (self.product_width / 1000.0), 3)
            else:
                self.product_area = 0.0
        elif self.product_type == 'default':
            # 默认类型：不计算自定义面积
            self.product_area = 0.0

    @api.onchange('product_width', 'product_thickness', 'finished_density', 'product_type')
    def _onchange_finished_weight(self):
        if self.product_type == 'finished_product':
            if self.product_width and self.product_thickness and self.finished_density:
                length_cm = 100
                width_cm = self.product_width * 0.1
                thickness_cm = self.product_thickness * 0.0001
                volume_cm3_per_meter = length_cm * width_cm * thickness_cm
                weight_g = volume_cm3_per_meter * self.finished_density
                weight_kg = weight_g / 1000
                self.weight_per_meter = round(weight_kg, 3)
            else:
                self.weight_per_meter = 0.0

    @api.onchange('product_length', 'product_width', 'product_thickness')
    def _onchange_product_dimensions(self):
        if self.product_type == 'raw_material':
            if self.product_length and self.product_width and self.product_thickness:
                self.update_dynamic_uom_factors()

    def write(self, vals):
        result = super().write(vals)
        dimension_fields = {'product_length', 'product_width', 'product_thickness', 'finished_density', 'product_type'}
        if any(field in vals for field in dimension_fields):
            for record in self:
                if record.product_type == 'raw_material':
                    if all([record.product_length, record.product_width, record.product_thickness]):
                        record.update_dynamic_uom_factors()
        return result

    @api.depends('uom_id', 'uom_po_id')
    def _compute_unit_ratio_display(self):
        for product in self:
            ratio_info = product._get_unit_ratio_info()
            product.unit_ratio_display = ratio_info['display_text'] if ratio_info else False

    @api.depends('uom_id', 'uom_po_id')
    def _compute_unit_info(self):
        for product in self:
            ratio_info = product._get_unit_ratio_info()
            if ratio_info:
                product.base_unit_name = ratio_info['base_unit_name']
                product.package_unit_name = ratio_info['package_unit_name']
                product.conversion_ratio = ratio_info['conversion_ratio']
            else:
                product.base_unit_name = False
                product.package_unit_name = False
                product.conversion_ratio = 0.0

    def _get_unit_ratio_info(self):
        self.ensure_one()
        if not self.uom_id or not self.uom_po_id:
            return False
        if self.uom_id.category_id != self.uom_po_id.category_id:
            return False
        if self.uom_id.factor == 1.0 and self.uom_po_id.factor < 1.0:
            base_uom = self.uom_id
            package_uom = self.uom_po_id
        elif self.uom_po_id.factor == 1.0 and self.uom_id.factor < 1.0:
            base_uom = self.uom_po_id
            package_uom = self.uom_id
        else:
            if self.uom_id.factor >= self.uom_po_id.factor:
                base_uom = self.uom_id
                package_uom = self.uom_po_id
            else:
                base_uom = self.uom_po_id
                package_uom = self.uom_id
        conversion_ratio = 1.0 / package_uom.factor if package_uom.factor > 0 else 1.0
        if conversion_ratio == int(conversion_ratio):
            display_text = f"1{package_uom.name}={int(conversion_ratio)}{base_uom.name}"
        else:
            display_text = f"1{package_uom.name}={conversion_ratio:.2f}{base_uom.name}"
        return {
            'base_unit_name': base_uom.name,
            'package_unit_name': package_uom.name,
            'conversion_ratio': conversion_ratio,
            'display_text': display_text
        }

    def _compute_dynamic_uom_factors(self):
        self.ensure_one()
        factors = {}
        if self.product_width:
            sqm_per_meter = self.product_width / 1000
            factors['sqm_factor'] = sqm_per_meter
        if self.product_length:
            roll_per_meter = 1.0 / self.product_length
            factors['roll_factor'] = roll_per_meter
        # 已移除：基于原料材料密度的吨计算（material_density 已删除）
        return factors

    def update_dynamic_uom_factors(self):
        self.ensure_one()
        factors = self._compute_dynamic_uom_factors()
        if not factors:
            return
        product_name = self.name or f"产品{self.id}"
        category_name = f"{product_name}-计量单位"
        product_category = self.env['uom.category'].search([('name', '=', category_name)], limit=1)
        if not product_category:
            return
        if 'sqm_factor' in factors:
            sqm_uom = self.env['uom.uom'].search([('name', '=', "平米"), ('category_id', '=', product_category.id)], limit=1)
            if sqm_uom:
                sqm_uom.write({'factor': factors['sqm_factor']})
        if 'roll_factor' in factors:
            roll_uom = self.env['uom.uom'].search([('name', '=', "卷"), ('category_id', '=', product_category.id)], limit=1)
            if roll_uom:
                roll_uom.write({'factor': factors['roll_factor']})
        return factors

    def action_quick_unit_setup(self):
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

    def action_batch_unit_setup(self):
        return {
            'name': _('批量单位设置'),
            'type': 'ir.actions.act_window',
            'res_model': 'product.batch.unit.setup.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_product_tmpl_ids': [(6, 0, self.ids)],
            }
        }

    def _check_unit_setup_eligibility(self):
        self.ensure_one()
        if self.uom_id and self.uom_po_id:
            if self.uom_id.category_id.name and '-' in self.uom_id.category_id.name:
                return False, "已通过计算设置单位"
        missing_fields = []
        if self.product_type == 'finished_product':
            if not self.product_width:
                missing_fields.append("产品宽度")
            if not self.product_thickness:
                missing_fields.append("产品厚度")
            if not self.finished_density:
                missing_fields.append("材料密度")
        else:
            if not self.product_length:
                missing_fields.append("产品长度")
            if not self.product_width:
                missing_fields.append("产品宽度")
            if not self.product_thickness:
                missing_fields.append("产品厚度")
        if missing_fields:
            return False, f"缺少基础信息: {', '.join(missing_fields)}"
        return True, "符合条件"

    def get_purchase_unit_info(self):
        """
        根据产品类型和采购单位类型，返回采购单位信息
        
        :return: dict {
            'unit_name': '单位名称',
            'unit_value': '单位数值',
            'unit_type': '单位类型',
            'description': '描述'
        }
        """
        self.ensure_one()
        
        # 根据产品类型自动设置采购单位类型
        if not self.purchase_unit_type:
            if self.product_type == 'raw_material':
                self.purchase_unit_type = 'roll'
            elif self.product_type == 'solution_material':
                self.purchase_unit_type = 'kg'
            elif self.product_type == 'finished_product':
                self.purchase_unit_type = 'sqm'
        
        unit_info = {
            'unit_name': '',
            'unit_value': 0.0,
            'unit_type': self.purchase_unit_type,
            'description': ''
        }
        
        if self.purchase_unit_type == 'roll':
            # 原膜按卷采购
            if self.product_type == 'raw_material' and self.roll_weight_per_unit:
                unit_info.update({
                    'unit_name': '卷',
                    'unit_value': self.roll_weight_per_unit,
                    'description': f'每卷重量: {self.roll_weight_per_unit}kg'
                })
            else:
                # 如果没有设置每卷重量，尝试从尺寸计算
                if self.product_length and self.product_width and self.product_thickness:
                    # 假设密度为1.0 g/cm³（可以根据实际情况调整）
                    density = 1.0  # g/cm³
                    length_cm = self.product_length * 100  # m → cm
                    width_cm = self.product_width * 0.1    # mm → cm
                    thickness_cm = self.product_thickness * 0.0001  # μm → cm
                    volume_cm3 = length_cm * width_cm * thickness_cm
                    weight_kg = (volume_cm3 * density) / 1000  # g → kg
                    
                    unit_info.update({
                        'unit_name': '卷',
                        'unit_value': round(weight_kg, 3),
                        'description': f'每卷重量(计算): {round(weight_kg, 3)}kg'
                    })
                    
        elif self.purchase_unit_type == 'kg':
            # 配液原料按kg采购
            if self.product_type == 'solution_material' and self.barrel_weight_per_unit:
                unit_info.update({
                    'unit_name': '桶',
                    'unit_value': self.barrel_weight_per_unit,
                    'description': f'每桶重量: {self.barrel_weight_per_unit}kg'
                })
            else:
                unit_info.update({
                    'unit_name': 'kg',
                    'unit_value': 1.0,
                    'description': '按公斤采购'
                })
                
        elif self.purchase_unit_type == 'sqm':
            # 半成品原膜按平方采购
            if self.product_type == 'finished_product' and self.roll_area_per_unit:
                unit_info.update({
                    'unit_name': '卷',
                    'unit_value': self.roll_area_per_unit,
                    'description': f'每卷面积: {self.roll_area_per_unit}㎡'
                })
            else:
                # 如果没有设置每卷面积，尝试从宽度计算
                if self.product_width:
                    width_m = self.product_width / 1000.0  # mm → m
                    unit_info.update({
                        'unit_name': '卷',
                        'unit_value': round(width_m, 3),
                        'description': f'每卷面积(计算): {round(width_m, 3)}㎡'
                    })
                    
        elif self.purchase_unit_type == 'custom':
            # 自定义采购单位
            if self.custom_purchase_unit_name and self.custom_purchase_unit_value:
                unit_info.update({
                    'unit_name': self.custom_purchase_unit_name,
                    'unit_value': self.custom_purchase_unit_value,
                    'description': f'自定义单位: {self.custom_purchase_unit_value}{self.custom_purchase_unit_name}'
                })
        
        return unit_info

    @api.onchange('product_type')
    def _onchange_product_type_purchase_unit(self):
        """当产品类型改变时，自动设置采购单位类型"""
        if self.product_type == 'raw_material':
            self.purchase_unit_type = 'roll'
        elif self.product_type == 'solution_material':
            self.purchase_unit_type = 'kg'
        elif self.product_type == 'finished_product':
            self.purchase_unit_type = 'sqm'
        else:
            self.purchase_unit_type = 'custom'

    @api.onchange('default_unit_config')
    def _onchange_default_unit_config(self):
        """当选择默认单位配置时，自动设置快速配置字段"""
        if self.default_unit_config == 'kg':
            self.quick_unit_name = 'kg'
            self.quick_unit_type = 'weight'
            self.quick_unit_value = 0.0
        elif self.default_unit_config == 'roll':
            self.quick_unit_name = '卷'
            self.quick_unit_type = 'weight'
            self.quick_unit_value = 0.0
        elif self.default_unit_config == 'barrel':
            self.quick_unit_name = '桶'
            self.quick_unit_type = 'weight'
            self.quick_unit_value = 0.0
        elif self.default_unit_config == 'box':
            self.quick_unit_name = '箱'
            self.quick_unit_type = 'quantity'
            self.quick_unit_value = 0.0
        elif self.default_unit_config == 'bag':
            self.quick_unit_name = '袋'
            self.quick_unit_type = 'weight'
            self.quick_unit_value = 0.0
        elif self.default_unit_config == 'sqm':
            self.quick_unit_name = '㎡'
            self.quick_unit_type = 'area'
            self.quick_unit_value = 0.0
        elif self.default_unit_config == 'custom':
            self.quick_unit_name = ''
            self.quick_unit_type = 'custom'
            self.quick_unit_value = 0.0

    @api.onchange('product_length', 'product_width', 'product_thickness', 'product_type')
    def _onchange_dimensions_calculate_units(self):
        """当产品尺寸改变时，自动计算采购单位值"""
        if self.product_type == 'raw_material' and self.purchase_unit_type == 'roll':
            # 自动计算每卷重量
            if self.product_length and self.product_width and self.product_thickness:
                # 假设密度为1.0 g/cm³（可以根据实际情况调整）
                density = 1.0  # g/cm³
                length_cm = self.product_length * 100  # m → cm
                width_cm = self.product_width * 0.1    # mm → cm
                thickness_cm = self.product_thickness * 0.0001  # μm → cm
                volume_cm3 = length_cm * width_cm * thickness_cm
                weight_kg = (volume_cm3 * density) / 1000  # g → kg
                self.roll_weight_per_unit = round(weight_kg, 3)
                
        elif self.product_type == 'finished_product' and self.purchase_unit_type == 'sqm':
            # 自动计算每卷面积
            if self.product_width:
                width_m = self.product_width / 1000.0  # mm → m
                self.roll_area_per_unit = round(width_m, 3)

    def get_unit_config_for_stock_move(self):
        """获取用于库存移动的单位配置信息"""
        self.ensure_one()
        
        if not self.enable_custom_units:
            return []
        
        # 使用快速配置
        if self.quick_unit_name:
            return [{
                'name': self.quick_unit_name,
                'type': self.quick_unit_type,
                'default_value': 0.0,  # 不提供默认数值，让用户手动填写
                'description': f'{self.quick_unit_name} - {self.quick_unit_type}'
            }]
        
        return []


class ProductProduct(models.Model):
    _inherit = 'product.product'
    
    def action_quick_unit_setup(self):
        self.ensure_one()
        return {
            'name': _('快速单位设置'),
            'type': 'ir.actions.act_window',
            'res_model': 'product.unit.setup.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_product_tmpl_id': self.product_tmpl_id.id,
                'default_product_name': self.name,
            }
        }
        return
