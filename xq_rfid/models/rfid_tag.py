# -*- encoding: utf-8 -*-
##############################################################################
#
# Grit - ifangtech.com
# Copyright (C) 2024 (https://ifangtech.com)
#
##############################################################################

from odoo import fields, models, api, _
from odoo.exceptions import ValidationError


class RFIDTag(models.Model):
    _name = 'rfid.tag'
    _description = 'RFID Tags'
    _inherit = ['mail.thread']
    _order = 'create_date desc'
    _check_company_auto = True

    @api.onchange('usage_type')
    def _picking_domain(self):
        for rec in self:
            if rec.usage_type == 'receipt':
                return {'domain': {'picking_id': [('picking_type_id.code', '=', 'incoming')]}}
            elif rec.usage_type == 'delivery':
                return {'domain': {'picking_id': [('picking_type_id.code', '=', 'outgoing')]}}
            else:
                return {'domain': {'picking_id': []}}

    name = fields.Char(string='RFID 标签', required=True, default=lambda self: self._get_next_rfid_name())
    usage_type = fields.Selection([('receipt', '收货'), ('delivery', '发货'),
                                   ('product', '产品'), ('stock_prod_lot', '批次/序列号'), ('n_a', '未分配')],
                                  string="使用类型", required=True)
    usage = fields.Reference(selection=[('stock.picking', '调拨单'),
                                        ('product.product', '产品'), ('stock.lot', '批次/序列号')],
                             string="关联对象", compute='_get_usage', readonly=True)

    picking_id = fields.Many2one('stock.picking', string="调拨单", domain=_picking_domain, check_company=True)
    product_id = fields.Many2one('product.product', string="产品变体", check_company=True)
    stock_prod_lot_id = fields.Many2one('stock.lot', string="批次/序列号", check_company=True)
    assigned = fields.Boolean(string="已分配", compute="_compute_assigned")
    company_id = fields.Many2one(
        'res.company',
        string='公司',
        compute='_compute_company_id',
        store=True,
        readonly=False,
        index=True,
        default=lambda self: self.env.company,
    )
    
    # ========== 生产关联字段 ==========
    production_id = fields.Many2one('mrp.production', string="生产订单", readonly=True, check_company=True)
    production_date = fields.Datetime(string="生产日期", readonly=True)
    quality_check_id = fields.Many2one('quality.check', string="质量检查", readonly=True, check_company=True)

    _sql_constraints = [
        ('rfid_tag_uniq_name', 'unique (name)', "RFID 编号必须唯一！"),
        ('rfid_tag_uniq_product', 'unique (product_id)', "一个产品只能关联一个 RFID 标签！"),
        ('rfid_tag_uniq_picking', 'unique (picking_id)', "一个调拨单只能关联一个 RFID 标签！"),
        ('rfid_tag_uniq_stock_prod_lot', 'unique (stock_prod_lot_id)',
         "一个批次/序列号只能关联一个 RFID 标签！")
    ]

    @api.depends('picking_id.company_id', 'product_id.company_id', 'stock_prod_lot_id.company_id', 'production_id.company_id')
    def _compute_company_id(self):
        for rec in self:
            rec.company_id = (
                rec.picking_id.company_id
                or rec.product_id.company_id
                or rec.stock_prod_lot_id.company_id
                or rec.production_id.company_id
                or rec.company_id
                or rec.env.company
            )

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
    def _get_next_rfid_name(self):
        """获取下一个RFID标签名称"""
        return self.env['ir.sequence'].next_by_code('rfid.tag') or 'RFID000001'
    
    def _validate_usage_links(self):
        for tag in self:
            linked_fields = [
                bool(tag.picking_id),
                bool(tag.product_id),
                bool(tag.stock_prod_lot_id),
            ]
            if sum(linked_fields) > 1:
                raise ValidationError(_('一个 RFID 标签只能关联一种业务对象。'))
            if tag.usage_type in ('receipt', 'delivery') and (tag.product_id or tag.stock_prod_lot_id):
                raise ValidationError(_('收货/发货标签只能关联调拨单。'))
            if tag.usage_type == 'product' and (tag.picking_id or tag.stock_prod_lot_id):
                raise ValidationError(_('产品标签只能关联产品。'))
            if tag.usage_type == 'stock_prod_lot' and (tag.picking_id or tag.product_id):
                raise ValidationError(_('批次/序列号标签只能关联批次/序列号。'))
            if tag.usage_type == 'n_a' and any(linked_fields):
                raise ValidationError(_('未分配标签不能关联业务对象。'))

    @api.constrains('usage_type', 'picking_id', 'product_id', 'stock_prod_lot_id')
    def _check_usage_links(self):
        self._validate_usage_links()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name'):
                vals['name'] = self._get_next_rfid_name()
        records = super().create(vals_list)
        records._validate_usage_links()
        records.set_rfid_tag()
        return records

    def write(self, values):
        relation_fields = ('picking_id', 'product_id', 'stock_prod_lot_id')
        old_relations = {
            tag.id: {
                'picking_id': tag.picking_id,
                'product_id': tag.product_id,
                'stock_prod_lot_id': tag.stock_prod_lot_id,
            }
            for tag in self
        }

        values = dict(values)
        if 'usage_type' in values:
            if values['usage_type'] in ('receipt', 'delivery'):
                values.setdefault('product_id', False)
                values.setdefault('stock_prod_lot_id', False)
            elif values['usage_type'] == 'product':
                values.setdefault('picking_id', False)
                values.setdefault('stock_prod_lot_id', False)
            elif values['usage_type'] == 'stock_prod_lot':
                values.setdefault('picking_id', False)
                values.setdefault('product_id', False)
            elif values['usage_type'] == 'n_a':
                values.setdefault('picking_id', False)
                values.setdefault('product_id', False)
                values.setdefault('stock_prod_lot_id', False)

        res = super().write(values)

        ctx = dict(self.env.context or {})
        ctx.update({'skip_set_rfid_tag_product': True})
        for tag in self:
            for field_name in relation_fields:
                if field_name not in values:
                    continue
                old_record = old_relations.get(tag.id, {}).get(field_name)
                if old_record and old_record != tag[field_name]:
                    old_record.with_context(ctx).write({'rfid_tag': False})

        self._validate_usage_links()
        self.set_rfid_tag()
        return res
    
