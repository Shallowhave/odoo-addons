# -*- encoding: utf-8 -*-
##############################################################################
#
# Grit - ifangtech.com
# Copyright (C) 2024 (https://ifangtech.com)
#
##############################################################################

from odoo import fields, models, api


class Product(models.Model):
    _inherit = "product.product"

    rfid_tag = fields.Many2one('rfid.tag', string='RFID 标签', readonly=1,
                               domain=[('usage_type', 'in', ('product', 'n_a')), ('product_id', '=', False)])

    # rfid_tag = fields.Char(string="RFID Tag", copy=False,  # related='rfid_id.name',
    #                        help="RFID Tag number used for product identification.")

    # _sql_constraints = [(
    #     'rfid_tag_uniq', 'unique (rfid_tag)',
    #     "A RFID tag cannot be linked to multiple Products."
    # )]

    # @api.onchange('rfid_tag')
    # def _onchange_rfid_tag(self):
    #     print("Origin", self._origin.rfid_tag.id, self._origin.rfid_tag.name, self._origin.rfid_tag.product_id)
    #     print("New", self.rfid_tag.id, self.rfid_tag.name, self.rfid_tag.product_id)
    #     ctx = dict(self.env.context or {})
    #     ctx.update({'skip_set_rfid_tag': True})
    #     self.with_context(ctx)._origin.rfid_tag.product_id = False
    #     self.with_context(ctx).rfid_tag.product_id = self.ids[0]

    # def write(self, values):
    #     print("Origin", self._origin.rfid_tag.id, self._origin.rfid_tag.name, self._origin.rfid_tag.product_id)
    #     print("New", self.rfid_tag.id, self.rfid_tag.name, self.rfid_tag.product_id)
    #     vals_keys = values.keys()
    #     skip_write = self.env.context.get('skip_set_rfid_tag_product', False)
    #     if 'rfid_tag' in vals_keys and not skip_write:
    #         ctx = dict(self.env.context or {})
    #         ctx.update({'skip_set_rfid_tag': True})
    #         self.with_context(ctx)._origin.rfid_tag.product_id = False
    #         self.with_context(ctx).rfid_tag.product_id = self.ids[0]
    #     res = super().write(values)
    #     return res


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    rfid_tag = fields.Char(string="RFID 标签", compute='_compute_rfid_tag',
                           inverse='_set_rfid_tag', search='_search_rfid_tag')

    @api.depends('product_variant_ids.rfid_tag')
    def _compute_rfid_tag(self):
        self.rfid_tag = False
        for template in self:
            # TODO master: update product_variant_count depends and use it instead
            variant_count = len(template.product_variant_ids)
            if variant_count == 1:
                template.rfid_tag = template.product_variant_ids.rfid_tag.name
            elif variant_count == 0:
                archived_variants = template.with_context(active_test=False).product_variant_ids
                if len(archived_variants) == 1:
                    template.rfid_tag = archived_variants.rfid_tag.name

    def _search_rfid_tag(self, operator, value):
        templates = self.with_context(active_test=False).search([('product_variant_ids.rfid_tag', operator, value)])
        return [('id', 'in', templates.ids)]

    def _set_rfid_tag(self):
        if len(self.product_variant_ids) == 1:
            self.product_variant_ids.rfid_tag = self.rfid_tag
