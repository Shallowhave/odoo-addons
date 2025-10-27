# -*- coding: utf-8 -*-

from odoo import models, fields, api


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    delivery_label_ids = fields.One2many(
        'delivery.label',
        'picking_id',
        string='发货标签'
    )
    
    delivery_label_count = fields.Integer(
        string='标签数量',
        compute='_compute_delivery_label_count'
    )
    
    has_delivery_labels = fields.Boolean(
        string='有发货标签',
        compute='_compute_has_delivery_labels',
        store=True
    )

    @api.depends('delivery_label_ids')
    def _compute_delivery_label_count(self):
        for picking in self:
            picking.delivery_label_count = len(picking.delivery_label_ids)

    @api.depends('delivery_label_ids')
    def _compute_has_delivery_labels(self):
        for picking in self:
            picking.has_delivery_labels = bool(picking.delivery_label_ids)

    def action_create_delivery_label(self):
        """创建发货标签"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': '创建发货标签',
            'res_model': 'delivery.label.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_picking_id': self.id,
                'default_partner_id': self.partner_id.id,
            }
        }

    def action_view_delivery_labels(self):
        """查看发货标签"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'发货单 {self.name} 的标签',
            'res_model': 'delivery.label',
            'view_mode': 'list,form',
            'domain': [('picking_id', '=', self.id)],
            'context': {'default_picking_id': self.id}
        }

    def action_print_all_labels(self):
        """打印所有标签"""
        self.ensure_one()
        if not self.delivery_label_ids:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': '提示',
                    'message': '没有找到发货标签',
                    'type': 'warning',
                }
            }
        
        return self.env.ref('delivery_label.action_delivery_label_report').report_action(self.delivery_label_ids)
