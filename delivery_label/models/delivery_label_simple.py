# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import base64
import io
from PIL import Image, ImageDraw, ImageFont
import qrcode


class DeliveryLabel(models.Model):
    _name = 'delivery.label'
    _description = '发货标签'
    _order = 'create_date desc'
    _rec_name = 'name'

    name = fields.Char(
        string='标签编号',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('New')
    )
    
    picking_id = fields.Many2one(
        'stock.picking',
        string='发货单',
        required=True,
        ondelete='cascade'
    )
    
    template_id = fields.Many2one(
        'delivery.label.template',
        string='标签模板',
        required=True
    )
    
    partner_id = fields.Many2one(
        'res.partner',
        string='客户',
        compute='_compute_partner_id',
        store=True
    )
    
    delivery_address = fields.Text(
        string='收货地址',
        compute='_compute_delivery_address',
        store=True
    )
    
    product_ids = fields.Many2many(
        'product.product',
        string='产品',
        compute='_compute_product_ids',
        store=True
    )
    
    weight = fields.Float(
        string='重量(kg)',
        compute='_compute_weight',
        store=True
    )
    
    volume = fields.Float(
        string='体积(m³)',
        compute='_compute_volume',
        store=True
    )
    
    tracking_number = fields.Char(
        string='跟踪号',
        compute='_compute_tracking_number',
        store=True
    )
    
    carrier_id = fields.Many2one(
        'delivery.carrier',
        string='承运商',
        compute='_compute_carrier_id',
        store=True
    )
    
    state = fields.Selection([
        ('draft', '草稿'),
        ('printed', '已打印'),
        ('shipped', '已发货'),
        ('delivered', '已送达'),
        ('cancelled', '已取消'),
    ], string='状态', default='draft', tracking=True)
    
    print_date = fields.Datetime(
        string='打印时间',
        readonly=True
    )
    
    print_user_id = fields.Many2one(
        'res.users',
        string='打印人',
        readonly=True
    )
    
    qr_code = fields.Binary(
        string='二维码',
        compute='_compute_qr_code',
        store=True
    )
    
    barcode = fields.Char(
        string='条形码',
        compute='_compute_barcode',
        store=True
    )
    
    label_image = fields.Binary(
        string='标签图片',
        compute='_compute_label_image',
        store=True
    )
    
    notes = fields.Text(
        string='备注'
    )

    @api.depends('picking_id.partner_id')
    def _compute_partner_id(self):
        for record in self:
            record.partner_id = record.picking_id.partner_id if record.picking_id else False

    @api.depends('picking_id.partner_id')
    def _compute_delivery_address(self):
        for record in self:
            if record.picking_id and record.picking_id.partner_id:
                partner = record.picking_id.partner_id
                address_parts = []
                if partner.street:
                    address_parts.append(partner.street)
                if partner.street2:
                    address_parts.append(partner.street2)
                if partner.city:
                    address_parts.append(partner.city)
                if partner.state_id:
                    address_parts.append(partner.state_id.name)
                if partner.zip:
                    address_parts.append(partner.zip)
                if partner.country_id:
                    address_parts.append(partner.country_id.name)
                record.delivery_address = '\n'.join(address_parts)
            else:
                record.delivery_address = ''

    @api.depends('picking_id.move_ids_without_package.product_id')
    def _compute_product_ids(self):
        for record in self:
            if record.picking_id:
                products = record.picking_id.move_ids_without_package.mapped('product_id')
                record.product_ids = [(6, 0, products.ids)]
            else:
                record.product_ids = [(5, 0, 0)]

    @api.depends('picking_id.move_ids_without_package')
    def _compute_volume(self):
        for record in self:
            if record.picking_id:
                # 计算所有移动行的体积总和
                total_volume = 0.0
                for move in record.picking_id.move_ids_without_package:
                    if move.product_id and hasattr(move.product_id, 'volume') and move.product_id.volume:
                        total_volume += move.product_id.volume * move.product_uom_qty
                record.volume = total_volume
            else:
                record.volume = 0.0

    @api.depends('picking_id.move_ids_without_package')
    def _compute_weight(self):
        for record in self:
            if record.picking_id:
                # 计算所有移动行的重量总和
                total_weight = 0.0
                for move in record.picking_id.move_ids_without_package:
                    if move.product_id and hasattr(move.product_id, 'weight') and move.product_id.weight:
                        total_weight += move.product_id.weight * move.product_uom_qty
                record.weight = total_weight
            else:
                record.weight = 0.0

    @api.depends('picking_id')
    def _compute_tracking_number(self):
        for record in self:
            if record.picking_id:
                # 尝试从不同字段获取跟踪号
                tracking_ref = ''
                if hasattr(record.picking_id, 'carrier_tracking_ref') and record.picking_id.carrier_tracking_ref:
                    tracking_ref = record.picking_id.carrier_tracking_ref
                elif hasattr(record.picking_id, 'tracking_reference') and record.picking_id.tracking_reference:
                    tracking_ref = record.picking_id.tracking_reference
                else:
                    tracking_ref = record.picking_id.name
                record.tracking_number = tracking_ref
            else:
                record.tracking_number = ''

    @api.depends('picking_id')
    def _compute_carrier_id(self):
        for record in self:
            if record.picking_id and hasattr(record.picking_id, 'carrier_id'):
                record.carrier_id = record.picking_id.carrier_id
            else:
                record.carrier_id = False

    @api.depends('name')
    def _compute_qr_code(self):
        for record in self:
            if record.name:
                try:
                    qr = qrcode.QRCode(
                        version=1,
                        error_correction=qrcode.constants.ERROR_CORRECT_L,
                        box_size=10,
                        border=4,
                    )
                    qr.add_data(record.name)
                    qr.make(fit=True)
                    
                    img = qr.make_image(fill_color="black", back_color="white")
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
                    record.qr_code = base64.b64encode(buffer.getvalue())
                except Exception:
                    record.qr_code = False
            else:
                record.qr_code = False

    @api.depends('name')
    def _compute_barcode(self):
        for record in self:
            record.barcode = record.name or ''

    @api.depends('template_id', 'name', 'partner_id', 'delivery_address', 'product_ids', 'weight', 'tracking_number')
    def _compute_label_image(self):
        for record in self:
            if record.template_id and record.name:
                try:
                    # 创建标签图片
                    img = self._generate_label_image(record)
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
                    record.label_image = base64.b64encode(buffer.getvalue())
                except Exception:
                    record.label_image = False
            else:
                record.label_image = False

    def _generate_label_image(self, record):
        """生成标签图片"""
        # 标签尺寸 (像素)
        width, height = 400, 300
        
        # 创建白色背景
        img = Image.new('RGB', (width, height), 'white')
        draw = ImageDraw.Draw(img)
        
        try:
            # 尝试使用系统字体
            font_large = ImageFont.truetype("arial.ttf", 16)
            font_medium = ImageFont.truetype("arial.ttf", 12)
            font_small = ImageFont.truetype("arial.ttf", 10)
        except:
            # 使用默认字体
            font_large = ImageFont.load_default()
            font_medium = ImageFont.load_default()
            font_small = ImageFont.load_default()
        
        y_position = 10
        
        # 标题
        draw.text((10, y_position), f"发货标签 - {record.name}", fill='black', font=font_large)
        y_position += 25
        
        # 客户信息
        if record.partner_id:
            draw.text((10, y_position), f"客户: {record.partner_id.name}", fill='black', font=font_medium)
            y_position += 20
        
        # 收货地址
        if record.delivery_address:
            address_lines = record.delivery_address.split('\n')
            for line in address_lines[:3]:  # 最多显示3行
                draw.text((10, y_position), line, fill='black', font=font_small)
                y_position += 15
        
        # 产品信息
        if record.product_ids:
            product_names = [p.name for p in record.product_ids[:2]]  # 最多显示2个产品
            draw.text((10, y_position), f"产品: {', '.join(product_names)}", fill='black', font=font_small)
            y_position += 15
        
        # 重量和跟踪号
        if record.weight:
            draw.text((10, y_position), f"重量: {record.weight}kg", fill='black', font=font_small)
            y_position += 15
        
        if record.tracking_number:
            draw.text((10, y_position), f"跟踪号: {record.tracking_number}", fill='black', font=font_small)
            y_position += 15
        
        # 二维码 (如果存在)
        if record.qr_code:
            try:
                qr_img = Image.open(io.BytesIO(base64.b64decode(record.qr_code)))
                qr_img = qr_img.resize((80, 80))
                img.paste(qr_img, (width - 90, height - 90))
            except:
                pass
        
        return img

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            vals['name'] = self.env['ir.sequence'].next_by_code('delivery.label') or _('New')
        return super().create(vals)

    def action_print_label(self):
        """打印标签"""
        self.ensure_one()
        self.write({
            'state': 'printed',
            'print_date': fields.Datetime.now(),
            'print_user_id': self.env.user.id
        })
        return self.env.ref('delivery_label.action_delivery_label_report').report_action(self)

    def action_mark_shipped(self):
        """标记为已发货"""
        self.write({'state': 'shipped'})

    def action_mark_delivered(self):
        """标记为已送达"""
        self.write({'state': 'delivered'})

    def action_cancel(self):
        """取消标签"""
        self.write({'state': 'cancelled'})

    def action_reset_to_draft(self):
        """重置为草稿"""
        self.write({'state': 'draft'})
