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
    product_type = fields.Selection(
        string='产品类型',
        related='product_tmpl_id.product_type',
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
    # material_density field removed
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

    @api.depends('product_tmpl_id', 'product_type', 'product_length', 'product_width', 'product_thickness', 'finished_density')
    def _compute_unit_ratios(self):
        """根据产品尺寸自动计算单位比例"""
        for wizard in self:
            # 成品：以米为基础单位
            if wizard.product_type == 'finished_product':
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
                if wizard.product_length and wizard.product_width and wizard.product_thickness:
                    # 计算1米的重量，然后乘以长度得到1卷的重量
                    length_cm = 100  # 1m = 100cm
                    width_cm = wizard.product_width * 0.1    # mm → cm
                    thickness_cm = wizard.product_thickness * 0.0001  # μm → cm
                    volume_cm3_per_meter = length_cm * width_cm * thickness_cm
                    # 已移除：基于 material_density 的吨计算；保留占位逻辑为 0
                    wizard.ton_per_roll = 0.0
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
            if self.product_type == 'finished_product':
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
        
        # 更新产品的计量单位设置
        values = {
            'uom_id': base_uom.id,  # 产品单位：米
            'uom_po_id': purchase_uom.id,  # 采购单位：平米
        }
        
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
        
        # 更新产品的计量单位设置
        values = {
            'uom_id': base_uom.id,  # 产品单位始终是卷
            'uom_po_id': purchase_uom.id,  # 采购单位
        }
        
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

    def action_preview_setup(self):
        """预览单位设置"""
        self.ensure_one()
        
        preview_message = _(
            "单位设置预览：\n\n"
            "基础单位: 卷 (参考单位)\n"
            "采购单位: %s\n\n"
            "将创建的单位:\n"
        ) % (
            dict(self._fields['purchase_unit'].selection)[self.purchase_unit]
        )
        
        # 添加计算结果
        if self.product_length > 0:
            preview_message += f"• 米 (smaller, factor = {self.product_length:.0f})\n"
        if self.product_length > 0 and self.product_width > 0:
            length_m = self.product_length
            width_m = self.product_width / 1000
            sqm_per_roll = length_m * width_m
            preview_message += f"• 平米 (smaller, factor = {sqm_per_roll:.2f})\n"
        if self.ton_per_roll > 0:
            roll_per_ton = 1.0 / self.ton_per_roll
            preview_message += f"• 吨 (smaller, factor = {self.ton_per_roll:.6f}，{self.ton_per_roll:.6f}吨 = 1卷，1吨 = {roll_per_ton:.2f}卷)\n"
        
        # 显示预览对话框
        return {
            'name': _('单位设置预览'),
            'type': 'ir.actions.act_window',
            'res_model': 'ir.ui.view',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'message': preview_message,
            },
        }

    def action_cancel(self):
        """取消设置"""
        return {'type': 'ir.actions.act_window_close'}


class ProductBatchUnitSetupWizard(models.TransientModel):
    _name = 'product.batch.unit.setup.wizard'
    _description = 'Product Batch Unit Setup Wizard'

    product_tmpl_ids = fields.Many2many(
        'product.template',
        string='产品列表',
        required=True
    )
    
    # 采购单位选择
    purchase_unit = fields.Selection([
        ('meter', '米'),
        ('sqm', '平米'),
        ('roll', '卷'),
        ('ton', '吨')
    ], string='采购单位', default='ton', help='选择用于采购的单位')
    
    # 统计信息
    total_products = fields.Integer(
        string='总产品数',
        compute='_compute_statistics',
        store=False
    )
    eligible_products = fields.Integer(
        string='符合条件产品数',
        compute='_compute_statistics',
        store=False
    )
    skipped_products = fields.Integer(
        string='跳过产品数',
        compute='_compute_statistics',
        store=False
    )
    
    # 产品详情
    product_details = fields.Text(
        string='产品详情',
        compute='_compute_statistics',
        store=False
    )

    @api.depends('product_tmpl_ids')
    def _compute_statistics(self):
        """计算统计信息"""
        for wizard in self:
            total = len(wizard.product_tmpl_ids)
            eligible_count = 0
            skipped_count = 0
            details = []
            
            for product in wizard.product_tmpl_ids:
                is_eligible, reason = product._check_unit_setup_eligibility()
                if is_eligible:
                    eligible_count += 1
                    details.append(f"✓ {product.name} - {reason}")
                else:
                    skipped_count += 1
                    details.append(f"✗ {product.name} - {reason}")
            
            wizard.total_products = total
            wizard.eligible_products = eligible_count
            wizard.skipped_products = skipped_count
            wizard.product_details = "\n".join(details)

    def action_setup_units(self):
        """执行批量单位设置"""
        self.ensure_one()
        
        if self.eligible_products == 0:
            raise UserError(_("没有符合条件的产品可以设置单位"))
        
        success_count = 0
        error_count = 0
        error_messages = []
        
        for product in self.product_tmpl_ids:
            is_eligible, reason = product._check_unit_setup_eligibility()
            if not is_eligible:
                continue  # 跳过不符合条件的产品
            
            try:
                # 使用现有的单位设置逻辑
                wizard = self.env['product.unit.setup.wizard'].create({
                    'product_tmpl_id': product.id,
                    'purchase_unit': self.purchase_unit,
                })
                wizard.action_setup_units()
                success_count += 1
            except Exception as e:
                error_count += 1
                error_messages.append(f"{product.name}: {str(e)}")
        
        # 构建结果消息
        result_message = f"批量单位设置完成！\n\n"
        result_message += f"成功设置: {success_count} 个产品\n"
        result_message += f"跳过: {self.skipped_products} 个产品\n"
        
        if error_count > 0:
            result_message += f"失败: {error_count} 个产品\n"
            result_message += "\n错误详情:\n" + "\n".join(error_messages)
        
        # 显示结果通知
        self.env['bus.bus']._sendone(
            self.env.user.partner_id,
            'simple_notification',
            {
                'title': _('批量设置完成'),
                'message': result_message,
                'type': 'success' if error_count == 0 else 'warning',
                'sticky': True,
            }
        )
        
        return {
            'type': 'ir.actions.act_window_close',
            'effect': {
                'fadeout': 'slow',
                'message': result_message,
                'type': 'rainbow_man' if error_count == 0 else 'warning',
            }
        }

    def action_cancel(self):
        """取消设置"""
        return {'type': 'ir.actions.act_window_close'}