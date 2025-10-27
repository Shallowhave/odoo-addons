# -*- coding: utf-8 -*-

from odoo import models, fields, api


class DeliveryLabelTemplate(models.Model):
    _name = 'delivery.label.template'
    _description = '发货标签模板'
    _order = 'name'

    name = fields.Char(
        string='模板名称',
        required=True
    )
    
    description = fields.Text(
        string='描述'
    )
    
    width = fields.Float(
        string='宽度(mm)',
        default=100.0,
        help='标签宽度，单位毫米'
    )
    
    height = fields.Float(
        string='高度(mm)',
        default=60.0,
        help='标签高度，单位毫米'
    )
    
    orientation = fields.Selection([
        ('portrait', '纵向'),
        ('landscape', '横向'),
    ], string='方向', default='portrait')
    
    show_company_logo = fields.Boolean(
        string='显示公司Logo',
        default=True
    )
    
    show_qr_code = fields.Boolean(
        string='显示二维码',
        default=True
    )
    
    show_barcode = fields.Boolean(
        string='显示条形码',
        default=True
    )
    
    show_tracking_number = fields.Boolean(
        string='显示跟踪号',
        default=True
    )
    
    show_weight = fields.Boolean(
        string='显示重量',
        default=True
    )
    
    show_volume = fields.Boolean(
        string='显示体积',
        default=False
    )
    
    show_delivery_address = fields.Boolean(
        string='显示收货地址',
        default=True
    )
    
    show_product_list = fields.Boolean(
        string='显示产品列表',
        default=True
    )
    
    font_size = fields.Selection([
        ('small', '小'),
        ('medium', '中'),
        ('large', '大'),
    ], string='字体大小', default='medium')
    
    active = fields.Boolean(
        string='激活',
        default=True
    )
    
    label_ids = fields.One2many(
        'delivery.label',
        'template_id',
        string='使用此模板的标签'
    )
    
    label_count = fields.Integer(
        string='标签数量',
        compute='_compute_label_count'
    )

    @api.depends('label_ids')
    def _compute_label_count(self):
        for template in self:
            template.label_count = len(template.label_ids)

    def action_view_labels(self):
        """查看使用此模板的标签"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'使用模板 {self.name} 的标签',
            'res_model': 'delivery.label',
            'view_mode': 'list,form',
            'domain': [('template_id', '=', self.id)],
            'context': {'default_template_id': self.id}
        }
