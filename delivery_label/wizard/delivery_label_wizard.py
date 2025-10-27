# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class DeliveryLabelWizard(models.TransientModel):
    _name = 'delivery.label.wizard'
    _description = '发货标签向导'

    picking_id = fields.Many2one(
        'stock.picking',
        string='发货单',
        required=True
    )
    
    partner_id = fields.Many2one(
        'res.partner',
        string='客户',
        related='picking_id.partner_id',
        readonly=True
    )
    
    template_id = fields.Many2one(
        'delivery.label.template',
        string='标签模板',
        required=True,
        domain=[('active', '=', True)]
    )
    
    quantity = fields.Integer(
        string='标签数量',
        default=1,
        required=True
    )
    
    notes = fields.Text(
        string='备注'
    )

    @api.model
    def default_get(self, fields_list):
        """设置默认值"""
        res = super().default_get(fields_list)
        
        # 获取默认模板
        default_template = self.env['delivery.label.template'].search([
            ('active', '=', True)
        ], limit=1)
        
        if default_template:
            res['template_id'] = default_template.id
            
        return res

    def action_create_labels(self):
        """创建标签"""
        self.ensure_one()
        
        if self.quantity <= 0:
            raise ValidationError(_('标签数量必须大于0'))
        
        # 创建标签
        labels = []
        for i in range(self.quantity):
            label = self.env['delivery.label'].create({
                'picking_id': self.picking_id.id,
                'template_id': self.template_id.id,
                'notes': self.notes,
            })
            labels.append(label)
        
        # 返回标签列表视图
        return {
            'type': 'ir.actions.act_window',
            'name': f'创建的标签 ({len(labels)}个)',
            'res_model': 'delivery.label',
            'view_mode': 'list,form',
            'domain': [('id', 'in', [label.id for label in labels])],
            'context': {'default_picking_id': self.picking_id.id}
        }

    def action_create_and_print(self):
        """创建并打印标签"""
        self.ensure_one()
        
        if self.quantity <= 0:
            raise ValidationError(_('标签数量必须大于0'))
        
        # 创建标签
        labels = []
        for i in range(self.quantity):
            label = self.env['delivery.label'].create({
                'picking_id': self.picking_id.id,
                'template_id': self.template_id.id,
                'notes': self.notes,
            })
            labels.append(label)
        
        # 打印标签
        return self.env.ref('delivery_label.action_delivery_label_report').report_action(labels)
