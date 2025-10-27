# -*- encoding: utf-8 -*-
##############################################################################
#
# Grit - ifangtech.com
# Copyright (C) 2024 (https://ifangtech.com)
#
##############################################################################

from odoo import fields, models, api, _
from odoo.exceptions import UserError


class RFIDTag(models.Model):
    _name = 'rfid.tag'
    _description = 'RFID Tags'
    _order = 'create_date desc'

    @api.onchange('usage_type')
    def _picking_domain(self):
        for rec in self:
            if rec.usage_type == 'receipt':
                return {'domain': {'picking_id': [('picking_type_id.code', '=', 'incoming')]}}
            elif rec.usage_type == 'delivery':
                return {'domain': {'picking_id': [('picking_type_id.code', '=', 'outgoing')]}}
            else:
                return {'domain': {'picking_id': []}}

    name = fields.Char(string='RFID 标签', required=True)
    usage_type = fields.Selection([('receipt', '收货'), ('delivery', '发货'),
                                   ('product', '产品'), ('stock_prod_lot', '批次/序列号'), ('n_a', '未分配')],
                                  string="使用类型", required=True)
    usage = fields.Reference(selection=[('stock.picking', '调拨单'),
                                        ('product.product', '产品'), ('stock.lot', '批次/序列号')],
                             string="关联对象", compute='_get_usage', readonly=True)

    picking_id = fields.Many2one('stock.picking', string="调拨单", domain=_picking_domain)
    product_id = fields.Many2one('product.product', string="产品变体")
    stock_prod_lot_id = fields.Many2one('stock.lot', string="批次/序列号")
    assigned = fields.Boolean(string="已分配", compute="_compute_assigned")
    
    # ========== 生产关联字段 ==========
    production_id = fields.Many2one('mrp.production', string="生产订单", readonly=True)
    production_date = fields.Datetime(string="生产日期", readonly=True)
    quality_check_id = fields.Many2one('quality.check', string="质量检查", readonly=True)

    _sql_constraints = [
        ('rfid_tag_uniq_name', 'unique (name)', "RFID 编号必须唯一！"),
        ('rfid_tag_uniq_stock_prod_lot', 'unique (stock_prod_lot_id)',
         "一个批次/序列号只能关联一个 RFID 标签！")
    ]

    def _get_usage(self):
        for rec in self:
            if rec.usage_type in ['receipt', 'delivery'] and rec.picking_id:
                rec.usage = rec.picking_id
            elif rec.usage_type == 'product' and rec.product_id:
                rec.usage = rec.product_id
            elif rec.usage_type == 'stock_prod_lot' and rec.stock_prod_lot_id:
                rec.usage = rec.stock_prod_lot_id
            else:
                rec.usage = False

    def _compute_assigned(self):
        for rec in self:
            if rec.picking_id or rec.product_id or rec.stock_prod_lot_id:
                rec.assigned = True
            else:
                rec.assigned = False
    

    @api.onchange('usage_type')
    def _onchange_usage_type(self):
        if self.usage_type in ('receipt', 'delivery'):
            self.product_id = False
            self.stock_prod_lot_id = False
        elif self.usage_type == 'product':
            self.picking_id = False
            self.stock_prod_lot_id = False
        elif self.usage_type == 'stock_prod_lot':
            self.picking_id = False
            self.product_id = False
        else:
            self.picking_id = False
            self.product_id = False
            self.stock_prod_lot_id = False

    # @api.onchange('picking_id')
    # def _onchange_picking_id(self):
    #     for rec in self:
    #         if rec.picking_id:
    #             rec.picking_id.rfid_tag = rec.name
    #
    # @api.onchange('product_id')
    # def _onchange_product_id(self):
    #     for rec in self:
    #         if rec.product_id:
    #             rec.product_id.rfid_tag = rec.name
    #
    # @api.onchange('stock_prod_lot_id')
    # def _onchange_stock_prod_lot_id(self):
    #     for rec in self:
    #         if rec.stock_prod_lot_id:
    #             rec.stock_prod_lot_id.rfid_tag = rec.name

    # def write(self, vals):
    # @api.depends('usage_type')
    # def set_rfid_usage(self, vals):
    #     for rec in self:
    #         if rec.usage_type in ('receipt', 'delivery') and rec.picking_id:
    #             rec.picking_id.rfid_tag = rec.name
    #         if rec.usage_type == 'product' and rec.product_id:
    #             rec.product_id.rfid_tag = rec.name
    #         if rec.usage_type == 'stock_prod_lot' and rec.stock_prod_lot_id:
    #             rec.stock_prod_lot_id.rfid_tag = rec.name
        # res = super(RFIDTag, self).write(vals)
        # print(res)
        # return res

    def set_rfid_tag(self):
        # print("rfid_tag set_rfid_tag()", self.env.context)
        if self.env.context.get('skip_set_rfid_tag', False):
            return
        else:
            ctx = dict(self.env.context or {})
            ctx.update({'skip_set_rfid_tag_product': True})
            for rec in self:
                if rec.usage_type in ('receipt', 'delivery') and rec.picking_id:
                    # rec.picking_id.write({'rfid_tag': rec.name})
                    rec.picking_id.with_context(ctx).write({'rfid_tag': rec.id})
                if rec.usage_type == 'product' and rec.product_id:
                    # rec.product_id.write({'rfid_tag': rec.name})
                    rec.product_id.with_context(ctx).write({'rfid_tag': rec.id})
                if rec.usage_type == 'stock_prod_lot' and rec.stock_prod_lot_id:
                    # rec.stock_prod_lot_id.write({'rfid_tag': rec.name})
                    rec.stock_prod_lot_id.with_context(ctx).write({'rfid_tag': rec.id})

    @api.model
    def create(self, vals):
        res = super(RFIDTag, self).create(vals)
        res.set_rfid_tag()
        return res

    def write(self, values):
        vals_keys = values.keys()
        # NOTE: Setting the rfid_tag in current product/picking/lot as False
        #  before assigning the tag to new product/picking/lot
        # NOTE: values.get(<m2o_field>, False) == False, this check was added to remove the relationship
        #  on Product/Picking/Lot if we delete from RFID Tag view

        if 'picking_id' in vals_keys or values.get('picking_id', False) == False:
            self.picking_id.rfid_tag = False
        if 'product_id' in vals_keys or values.get('product_id', False) == False:
            self.product_id.rfid_tag = False
        if 'stock_prod_lot_id' in vals_keys or values.get('stock_prod_lot_id', False) == False:
            self.stock_prod_lot_id.rfid_tag = False

        res = super().write(values)
        self.set_rfid_tag()
        return res
    
