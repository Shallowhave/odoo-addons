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
    solution_weight = fields.Float(
        string="重量 (公斤)",
        help="配液原料的重量，单位：公斤",
        digits=(12, 3)
    )
    
    solution_solid_content = fields.Float(
        string="固含 (%)",
        help="配液原料的固含量，单位：百分比",
        digits=(5, 2)
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
