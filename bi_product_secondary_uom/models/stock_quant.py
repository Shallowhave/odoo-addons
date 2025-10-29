# -*- coding: utf-8 -*-

from odoo import api, fields, models, _

class StockQuant(models.Model):
    _inherit = 'stock.quant'

    lot_weight = fields.Float(string='重量(kg)', default=0, compute='_compute_lot_weight', store=True)
    lot_barrels = fields.Integer(string='桶数', default=0, compute='_compute_lot_barrels', store=True)
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
            # 由于lot_weight字段已从stock.move.line中移除，这里设置为0
            # 如果需要重量信息，应该从其他字段计算或直接设置为0
            quant.lot_weight = 0

    @api.depends('product_id', 'inventory_quantity_auto_apply')
    def _compute_lot_barrels(self):
        for quant in self:
            # 对于配液原料类型，从库存移动行中获取单位数量
            if hasattr(quant.product_id.product_tmpl_id, 'product_type') and quant.product_id.product_tmpl_id.product_type == 'solution_material':
                move_lines = self.env['stock.move.line'].search([
                    ('lot_id', '=', quant.lot_id.id),
                    ('state', '=', 'done'),
                    ('location_dest_id', '=', quant.location_id.id)
                ])
                # 使用lot_quantity字段替代lot_barrels
                lot_quantity = sum(move_lines.mapped('lot_quantity'))
                qty = sum(move_lines.mapped('qty_done'))
                if qty > 0 and lot_quantity > 0:
                    quant.lot_barrels = lot_quantity * quant.inventory_quantity_auto_apply / qty
                else:
                    quant.lot_barrels = 0
            else:
                quant.lot_barrels = 0