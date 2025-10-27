# -*- coding: utf-8 -*-

from odoo import api, fields, models, _

class StockQuant(models.Model):
    _inherit = 'stock.quant'

    lot_weight = fields.Float(string='重量(kg)', default=0, compute='_compute_lot_weight', store=True)
    secondary_uom_id = fields.Many2one('uom.uom', compute='_quantity_secondary_compute', string="第二单位", store=True)

    secondary_quantity = fields.Float('第二单位在手', compute='_quantity_secondary_compute', digits='Product Unit of Measure', store=True)
    o_note1 = fields.Text(string='备注1')
    o_note2 = fields.Text(string='备注2')
    contract_no = fields.Char(string='合同号', compute='_compute_contract_no')

    def _compute_contract_no(self):
        for quant in self:
            contract_no = self.env['stock.move.line'].search([
                ('lot_id', '=', quant.lot_id.id),
                ('state', '=', 'done'),
                ('location_dest_id', '=', quant.location_id.id)
            ]).move_id.purchase_line_id.order_id.mapped("contract_no")
            if contract_no:
                quant.contract_no = "".join([i for i in contract_no if i])
            else:
                quant.contract_no = ""
    
    @api.depends('product_id', 'inventory_quantity_auto_apply', 'product_uom_id')
    def _quantity_secondary_compute(self):
        for quant in self:
            if quant.product_id.secondary_uom:
                uom_quantity = quant.product_id.uom_id._compute_quantity(
                    quant.inventory_quantity_auto_apply,
                    quant.product_id.secondary_uom_id,
                    rounding_method='HALF-UP'
                )
                quant.secondary_uom_id = quant.product_id.secondary_uom_id
                quant.secondary_quantity = uom_quantity

    @api.depends('product_id', 'inventory_quantity_auto_apply')
    def _compute_lot_weight(self):
        for quant in self:
            move_lines = self.env['stock.move.line'].search([
                ('lot_id', '=', quant.lot_id.id),
                ('state', '=', 'done'),
                ('location_dest_id', '=', quant.location_id.id)
            ])
            lot_weight = sum(move_lines.mapped('lot_weight'))
            qty = sum(move_lines.mapped('qty_done'))
            if qty > 0:
                quant.lot_weight = lot_weight * quant.inventory_quantity_auto_apply / qty
            else:
                quant.lot_weight = 0
