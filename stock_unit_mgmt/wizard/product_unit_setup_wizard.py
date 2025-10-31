# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError


class ProductUnitSetupWizard(models.TransientModel):
    _name = 'product.unit.setup.wizard'
    _description = 'Product Unit Setup Wizard'

    product_tmpl_id = fields.Many2one(
        'product.template',
        string='产品',
        required=True
    )
    product_name = fields.Char(
        string='产品名称',
        related='product_tmpl_id.name',
        readonly=True
    )
    
    # 产品尺寸信息（只读显示）
    product_length = fields.Float(
        string='产品长度 (m)',
        related='product_tmpl_id.product_length',
        readonly=True
    )
    product_width = fields.Integer(
        string='产品宽度 (mm)',
        related='product_tmpl_id.product_width',
        readonly=True
    )
    product_thickness = fields.Integer(
        string='产品厚度 (μm)',
        related='product_tmpl_id.product_thickness',
        readonly=True
    )
    finished_density = fields.Float(
        string='材料密度 (g/cm³)',
        related='product_tmpl_id.finished_density',
        readonly=True,
        digits=(12, 3)
    )
    product_area = fields.Float(
        string='产品面积 (㎡)',
        related='product_tmpl_id.product_area',
        readonly=True,
        digits=(12, 3)
    )
    weight_per_meter = fields.Float(
        string='每米重量 (kg/m)',
        related='product_tmpl_id.weight_per_meter',
        readonly=True
    )
    
    # 自动判断产品类型（基于属性）
    is_finished_product = fields.Boolean(
        string='是否成品',
        compute='_compute_product_type',
        help='根据产品属性自动判断：有宽度和密度但没有长度时，判断为成品'
    )
    
    # 原料：自动计算的比例 (以卷为基础单位)
    sqm_per_roll = fields.Float(
        string='平米/卷',
        compute='_compute_unit_ratios',
        store=False,
        digits=(12, 2),
        help='一卷包含多少平米'
    )
    meter_per_roll = fields.Float(
        string='米/卷',
        compute='_compute_unit_ratios',
        store=False,
        digits=(12, 0),
        help='一卷包含多少米'
    )
    ton_per_roll = fields.Float(
        string='吨/卷',
        compute='_compute_unit_ratios',
        store=False,
        digits=(12, 6),
        help='一卷的重量（吨）'
    )
    
    # 成品：自动计算的比例 (以米为基础单位)
    sqm_per_meter = fields.Float(
        string='平米/米',
        compute='_compute_unit_ratios',
        store=False,
        digits=(12, 3),
        help='一米包含多少平米'
    )
    
    # 采购单位选择
    purchase_unit = fields.Selection([
        ('meter', '米'),
        ('sqm', '平米'),
        ('roll', '卷'),
        ('ton', '吨')
    ], string='采购单位', default='ton', help='选择用于采购的单位')
    
    # 附加单位配置
    enable_custom_units = fields.Boolean(
        string='启用附加单位',
        default=True,
        help='启用后可以在库存移动时填写附加单位信息（如：卷、桶等）'
    )
    
    default_unit_config = fields.Selection([
        ('kg', '公斤(kg)'),
        ('roll', '卷'),
        ('barrel', '桶'),
        ('box', '箱'),
        ('bag', '袋'),
        ('sqm', '平方米(㎡)'),
        ('custom', '自定义')
    ], string='附加单位模板',
       help='选择常用的附加单位模板，收货时可手动输入数量')
    
    quick_unit_name = fields.Char(
        string='自定义单位名称',
        help='当附加单位模板选择"自定义"时填写，如：托、包、组等'
    )

    @api.onchange('product_tmpl_id')
    def _onchange_product_tmpl_id(self):
        """当选择产品时，自动加载现有配置并推荐附加单位"""
        if self.product_tmpl_id:
            # 加载现有的附加单位配置
            self.enable_custom_units = self.product_tmpl_id.enable_custom_units
            self.default_unit_config = self.product_tmpl_id.default_unit_config
            self.quick_unit_name = self.product_tmpl_id.quick_unit_name
            
            # 重新计算产品类型
            self._compute_product_type()
            
            # 根据产品类型自动推荐附加单位
            if not self.default_unit_config:
                if self.is_finished_product:
                    # 成品推荐使用平方米
                    self.default_unit_config = 'sqm'
                elif self.product_length and self.product_length > 0:
                    # 原料推荐使用卷
                    self.default_unit_config = 'roll'
                else:
                    # 其他情况推荐使用公斤
                    self.default_unit_config = 'kg'
    
    @api.depends('product_length', 'product_width', 'finished_density', 'product_tmpl_id')
    def _compute_product_type(self):
        """根据产品属性自动判断产品类型"""
        for wizard in self:
            # 如果没有长度，但有宽度和密度，判断为成品
            # 否则判断为原料（以卷为基础）
            wizard.is_finished_product = (
                not wizard.product_length or wizard.product_length == 0
            ) and wizard.product_width and wizard.finished_density
    
    @api.onchange('default_unit_config')
    def _onchange_default_unit_config(self):
        """当附加单位模板改变时，清空自定义单位名称"""
        if self.default_unit_config != 'custom':
            self.quick_unit_name = False

    @api.depends('product_tmpl_id', 'product_length', 'product_width', 'product_thickness', 'finished_density', 'is_finished_product')
    def _compute_unit_ratios(self):
        """根据产品尺寸自动计算单位比例"""
        for wizard in self:
            # 成品：以米为基础单位
            if wizard.is_finished_product:
                wizard.meter_per_roll = 0.0
                wizard.sqm_per_roll = 0.0
                wizard.ton_per_roll = 0.0
                
                # 计算每米的面积 (㎡/m)
                if wizard.product_width:
                    # 1米 × 宽度(mm) / 1000 = 面积(㎡)
                    width_m = wizard.product_width / 1000  # mm → m
                    wizard.sqm_per_meter = round(width_m, 3)
                else:
                    wizard.sqm_per_meter = 0.0
                    
            # 原料：以卷为基础单位
            else:
                wizard.sqm_per_meter = 0.0
                
                # 计算一卷的长度（米）
                if wizard.product_length:
                    wizard.meter_per_roll = wizard.product_length
                else:
                    wizard.meter_per_roll = 0.0
                
                # 计算一卷的面积 (㎡) - 需要长度和宽度
                if wizard.product_length and wizard.product_width:
                    # 长度(m) × 宽度(mm) / 1000 = 面积(㎡)
                    length_m = wizard.product_length
                    width_m = wizard.product_width / 1000  # mm → m
                    wizard.sqm_per_roll = round(length_m * width_m, 2)
                else:
                    wizard.sqm_per_roll = 0.0
                    
                # 计算一卷的重量 (吨) - 需要长度、宽度、厚度和密度
                if wizard.product_length and wizard.product_width and wizard.product_thickness and wizard.finished_density:
                    # 计算体积：长度(m) × 宽度(mm→m) × 厚度(μm→m)
                    length_m = wizard.product_length
                    width_m = wizard.product_width / 1000.0  # mm → m
                    thickness_m = wizard.product_thickness / 1000000.0  # μm → m
                    volume_m3 = length_m * width_m * thickness_m
                    
                    # 密度 g/cm³ = 1000 kg/m³，转换为吨/m³
                    density_kg_m3 = wizard.finished_density * 1000
                    weight_kg = volume_m3 * density_kg_m3
                    wizard.ton_per_roll = round(weight_kg / 1000.0, 6)  # kg → 吨
                else:
                    wizard.ton_per_roll = 0.0

    def _create_or_update_uom(self, category, name, factor, uom_type, rounding):
        """创建或更新计量单位"""
        existing_uom = self.env['uom.uom'].search([
            ('name', '=', name),
            ('category_id', '=', category.id)
        ], limit=1)
        
        if existing_uom:
            # 更新现有单位
            existing_uom.write({'factor': factor})
            return existing_uom
        else:
            # 创建新单位
            return self.env['uom.uom'].create({
                'name': name,
                'category_id': category.id,
                'factor': factor,
                'uom_type': uom_type,
                'rounding': rounding,
            })

    def action_setup_units(self):
        """执行单位设置"""
        self.ensure_one()
        
        try:
            # 为产品创建专用的计量单位类别
            product_name = self.product_tmpl_id.name or f"产品{self.product_tmpl_id.id}"
            category_name = f"{product_name}-计量单位"
            
            # 检查是否已存在该产品的计量单位类别
            existing_category = self.env['uom.category'].search([
                ('name', '=', category_name)
            ], limit=1)
            
            if existing_category:
                category = existing_category
            else:
                # 创建新的计量单位类别
                category = self.env['uom.category'].create({
                    'name': category_name
                })
            
            # 根据产品类型设置不同的单位系统
            if self.is_finished_product:
                # 成品：基础单位为米，次要单位为平米
                return self._setup_finished_product_units(category, category_name)
            else:
                # 原料：基础单位为卷
                return self._setup_raw_material_units(category, category_name)
                
        except Exception as e:
            raise UserError(_("设置单位时出错: %s") % str(e))
    
    def _setup_finished_product_units(self, category, category_name):
        """设置成品单位系统：基础单位为米，次要单位为平米"""
        # 为产品创建专用的基础单位：米
        base_uom_name = "米"
        existing_base_uom = self.env['uom.uom'].search([
            ('name', '=', base_uom_name),
            ('category_id', '=', category.id)
        ], limit=1)
        
        if existing_base_uom:
            base_uom = existing_base_uom
        else:
            # 创建基础单位：米
            base_uom = self.env['uom.uom'].create({
                'name': base_uom_name,
                'category_id': category.id,
                'factor': 1.0,
                'uom_type': 'reference',
                'rounding': 0.01,
            })
        
        # 创建所有计量单位 (以米为基础)
        created_uoms = {'meter': base_uom}
        
        # 创建平米单位 (以米为基础，smaller - 因为多个平米组成一米)
        if self.product_width > 0:
            # 计算1米的面积
            width_m = self.product_width / 1000  # mm转m
            sqm_per_meter = width_m  # 1米的面积
            sqm_uom = self._create_or_update_uom(category, "平米", sqm_per_meter, 'smaller', 0.001)
            created_uoms['sqm'] = sqm_uom
        
        # 采购单位默认为平米
        purchase_uom = created_uoms.get('sqm', base_uom)
        
        # 更新产品的计量单位设置和附加单位配置
        values = {
            'uom_id': base_uom.id,  # 产品单位：米
            'uom_po_id': purchase_uom.id,  # 采购单位：平米
            'enable_custom_units': self.enable_custom_units,
        }
        
        # 添加附加单位配置
        if self.enable_custom_units:
            if self.default_unit_config == 'custom' and not self.quick_unit_name:
                raise UserError(_('选择自定义单位时，必须填写自定义单位名称'))
            values.update({
                'default_unit_config': self.default_unit_config,
                'quick_unit_name': self.quick_unit_name if self.default_unit_config == 'custom' else False,
            })
        
        self.product_tmpl_id.write(values)
        
        # 构建详细的成功消息
        message_parts = [
            f"单位设置完成！\n",
            f"计量单位类别: {category_name}",
            f"基础单位: {base_uom.name} (参考单位)",
            f"采购单位: {purchase_uom.name}\n",
            "转换关系 (以米为基础):"
        ]
        
        # 添加所有可用的转换关系
        if 'sqm' in created_uoms:
            sqm_ratio = created_uoms['sqm'].factor
            message_parts.append(f"• {sqm_ratio:.3f}平米 = 1米 (factor={sqm_ratio:.3f})")
        
        # 添加附加单位配置信息
        if self.enable_custom_units:
            unit_label = self._get_unit_display_name() if self.default_unit_config != 'custom' else self.quick_unit_name
            message_parts.append(f"\n附加单位: {unit_label}")
        
        message = "\n".join(message_parts)
        
        # 先显示成功通知
        self.env['bus.bus']._sendone(
            self.env.user.partner_id,
            'simple_notification',
            {
                'title': _('成功'),
                'message': message,
                'type': 'success',
                'sticky': False,
            }
        )
        
        # 返回关闭向导的动作
        return {
            'type': 'ir.actions.act_window_close',
            'effect': {
                'fadeout': 'slow',
                'message': message,
                'type': 'rainbow_man',
            }
        }
    
    def _setup_raw_material_units(self, category, category_name):
        """设置原料单位系统：基础单位为卷"""
        # 为产品创建专用的基础单位：卷
        base_uom_name = "卷"
        existing_base_uom = self.env['uom.uom'].search([
            ('name', '=', base_uom_name),
            ('category_id', '=', category.id)
        ], limit=1)
        
        if existing_base_uom:
            base_uom = existing_base_uom
        else:
            # 创建基础单位：卷
            base_uom = self.env['uom.uom'].create({
                'name': base_uom_name,
                'category_id': category.id,
                'factor': 1.0,
                'uom_type': 'reference',
                'rounding': 0.01,
            })
        
        # 创建所有计量单位 (以卷为基础)
        created_uoms = {'roll': base_uom}
        
        # 1. 创建米单位 (以卷为基础，smaller - 因为很多米组成一卷)
        if self.product_length > 0:
            # 米比卷小，factor应该是多少米等于1卷
            # 例如：产品长度1550米，所以1550米=1卷，factor=1550
            meter_factor = self.product_length  # 多少米等于1卷
            meter_uom = self._create_or_update_uom(category, "米", meter_factor, 'smaller', 0.01)
            created_uoms['meter'] = meter_uom
        
        # 2. 创建平米单位 (以卷为基础，smaller - 因为很多平米组成一卷)
        if self.product_length > 0 and self.product_width > 0:
            # 计算1卷的面积
            length_m = self.product_length
            width_m = self.product_width / 1000  # mm转m
            sqm_per_roll = length_m * width_m  # 1卷的面积
            sqm_uom = self._create_or_update_uom(category, "平米", sqm_per_roll, 'smaller', 0.01)
            created_uoms['sqm'] = sqm_uom
            
        # 3. 创建吨单位 (以卷为基础，smaller - 和米、平米一样的逻辑)
        if self.ton_per_roll > 0:
            # ton_per_roll 表示1卷的重量（吨）
            # factor就是重量值，含义：ton_per_roll吨 = 1卷
            ton_uom = self._create_or_update_uom(category, "吨", self.ton_per_roll, 'smaller', 0.001)
            created_uoms['ton'] = ton_uom
        
        # 根据选择的采购单位确定采购单位
        if self.purchase_unit in created_uoms:
            purchase_uom = created_uoms[self.purchase_unit]
        else:
            purchase_uom = base_uom
        
        # 更新产品的计量单位设置和附加单位配置
        values = {
            'uom_id': base_uom.id,  # 产品单位始终是卷
            'uom_po_id': purchase_uom.id,  # 采购单位
            'enable_custom_units': self.enable_custom_units,
        }
        
        # 添加附加单位配置
        if self.enable_custom_units:
            if self.default_unit_config == 'custom' and not self.quick_unit_name:
                raise UserError(_('选择自定义单位时，必须填写自定义单位名称'))
            values.update({
                'default_unit_config': self.default_unit_config,
                'quick_unit_name': self.quick_unit_name if self.default_unit_config == 'custom' else False,
            })
        
        self.product_tmpl_id.write(values)
        
        # 构建详细的成功消息
        message_parts = [
            f"单位设置完成！\n",
            f"计量单位类别: {category_name}",
            f"基础单位: {base_uom.name} (参考单位)",
            f"采购单位: {purchase_uom.name}\n",
            "转换关系 (以卷为基础):"
        ]
        
        # 添加所有可用的转换关系
        if 'meter' in created_uoms:
            meter_ratio = created_uoms['meter'].factor
            message_parts.append(f"• {meter_ratio:.0f}米 = 1卷 (factor={meter_ratio:.0f})")
            
        if 'sqm' in created_uoms:
            sqm_ratio = created_uoms['sqm'].factor
            message_parts.append(f"• {sqm_ratio:.2f}平米 = 1卷 (factor={sqm_ratio:.2f})")
            
        if 'ton' in created_uoms:
            ton_ratio = created_uoms['ton'].factor  # ton_per_roll，1卷的重量（吨）
            roll_per_ton = 1.0 / ton_ratio  # 1吨包含多少卷
            message_parts.append(f"• {ton_ratio:.6f}吨 = 1卷 (1吨 = {roll_per_ton:.2f}卷, factor={ton_ratio:.6f})")
        
        # 添加附加单位配置信息
        if self.enable_custom_units:
            unit_label = self._get_unit_display_name() if self.default_unit_config != 'custom' else self.quick_unit_name
            message_parts.append(f"\n附加单位: {unit_label}")
        
        message = "\n".join(message_parts)
        
        # 先显示成功通知
        self.env['bus.bus']._sendone(
            self.env.user.partner_id,
            'simple_notification',
            {
                'title': _('成功'),
                'message': message,
                'type': 'success',
                'sticky': False,
            }
        )
        
        # 返回关闭向导的动作
        return {
            'type': 'ir.actions.act_window_close',
            'effect': {
                'fadeout': 'slow',
                'message': message,
                'type': 'rainbow_man',
            }
        }

    def _get_unit_display_name(self):
        """获取单位显示名称"""
        unit_map = {
            'kg': '公斤(kg)',
            'roll': '卷',
            'barrel': '桶',
            'box': '箱',
            'bag': '袋',
            'sqm': '平方米(㎡)',
            'custom': '自定义'
        }
        return unit_map.get(self.default_unit_config, self.default_unit_config)

    def action_cancel(self):
        """取消设置"""
        return {'type': 'ir.actions.act_window_close'}

