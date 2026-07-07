# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from odoo.osv import expression
import logging

_logger = logging.getLogger(__name__)


class StockLot(models.Model):
    _inherit = 'stock.lot'

    mrp_auto_lot_needs_suffix = fields.Boolean(
        string='待补全批次号',
        copy=False,
        index=True,
        help='启用“只生成前缀和日期”时的内部标记。业务人员补全批次号后会自动清除。',
    )
    mrp_auto_lot_production_id = fields.Many2one(
        'mrp.production',
        string='自动批次来源制造单',
        copy=False,
        index=True,
        ondelete='set null',
        help='记录该批次号由哪张制造订单自动创建，用于防止制造订单选用已存在批次号。',
    )

    @api.model
    def _lock_lot_names(self, name_values):
        """Serialize duplicate checks for the same lot/serial name."""
        keys = sorted({name.strip() for name in name_values if isinstance(name, str) and name.strip()})
        for key in keys:
            self.env.cr.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)",
                [f'stock.lot.name:{key}'],
            )

    @api.model
    def _is_mrp_auto_lot_override_enabled(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'mrp_auto_lot_generate.override_generate_serial', 'True'
        ).lower() == 'true'

    @api.model
    def _get_context_production(self):
        production_id = self.env.context.get('default_production_id') or self.env.context.get('production_id')
        if not production_id:
            return self.env['mrp.production']
        try:
            production_id = int(production_id)
        except (TypeError, ValueError):
            return self.env['mrp.production']
        return self.env['mrp.production'].browse(production_id).exists()

    @api.model
    def _normalize_create_vals(self, vals_list):
        for vals in vals_list:
            if isinstance(vals.get('name'), str):
                vals['name'] = vals['name'].strip()

    @api.model
    def _generate_name_from_production(self, vals, production):
        if vals.get('name'):
            return

        try:
            lot_name = production._generate_batch_number()
        except Exception as error:
            _logger.error("[自动批次] 从制造订单创建批次号时生成名称失败：%s", str(error))
            return

        vals['name'] = lot_name
        if production._is_prefix_date_only_enabled():
            vals['mrp_auto_lot_needs_suffix'] = True
        _logger.info("[自动批次] 从制造订单 %s 自动生成批次号：%s", production.name, lot_name)

    @api.model
    def _apply_production_context_vals(self, vals_list, production):
        if not production:
            return

        for vals in vals_list:
            vals.setdefault('mrp_auto_lot_production_id', production.id)

        if production.product_id.tracking not in ['lot', 'serial']:
            return

        for vals in vals_list:
            vals['product_id'] = production.product_id.id
            vals['company_id'] = production.company_id.id
            self._generate_name_from_production(vals, production)

    def _find_committed_duplicate_lot(self, lot, include_placeholders=True):
        """Read latest committed duplicates outside the current transaction snapshot."""
        where_clauses = [
            "name = %s",
            "id != %s",
        ]
        params = [lot.name.strip(), lot.id]

        if lot.company_id:
            where_clauses.append("(company_id = %s OR company_id IS NULL)")
            params.append(lot.company_id.id)

        if not include_placeholders:
            where_clauses.append("COALESCE(mrp_auto_lot_needs_suffix, false) = false")

        query = "SELECT id FROM stock_lot WHERE %s LIMIT 1" % " AND ".join(where_clauses)
        with self.env.registry.cursor() as cr:
            cr.execute(query, params)
            row = cr.fetchone()
            return self.browse(row[0]) if row else self.browse()

    def _get_duplicate_lot_domain(self, lot):
        domain = [
            ('name', '=', lot.name.strip()),
            ('id', '!=', lot.id),
        ]
        if lot.company_id:
            domain = expression.AND([domain, [
                '|',
                ('company_id', '=', lot.company_id.id),
                ('company_id', '=', False),
            ]])
        if lot.mrp_auto_lot_needs_suffix:
            domain = expression.AND([domain, [
                ('mrp_auto_lot_needs_suffix', '=', False),
            ]])
        return domain

    def _find_duplicate_lot(self, lot):
        duplicate = self.sudo().search(self._get_duplicate_lot_domain(lot), limit=1)
        return duplicate or self._find_committed_duplicate_lot(
            lot,
            include_placeholders=not lot.mrp_auto_lot_needs_suffix,
        )

    def _check_duplicate_lot_name_across_company(self):
        """校验同一公司下批次/序列号名称不能重复。"""
        for lot in self.filtered('name'):
            if self._find_duplicate_lot(lot):
                raise ValidationError(_(
                    '批次/序列号 "%(lot_name)s" 已存在，不能重复使用。'
                ) % {'lot_name': lot.name})

    @api.constrains('name', 'product_id', 'company_id', 'mrp_auto_lot_needs_suffix')
    def _check_unique_lot(self):
        self._check_duplicate_lot_name_across_company()

    def write(self, vals):
        if isinstance(vals.get('name'), str):
            vals = dict(vals, name=vals['name'].strip())
            if 'mrp_auto_lot_needs_suffix' not in vals and any(lot.name != vals['name'] for lot in self):
                vals['mrp_auto_lot_needs_suffix'] = False
        if 'name' in vals or 'company_id' in vals:
            self._lock_lot_names(vals.get('name') or lot.name for lot in self)
        res = super().write(vals)
        if 'name' in vals or 'company_id' in vals:
            self._check_duplicate_lot_name_across_company()
        return res
    
    @api.model_create_multi
    def create(self, vals_list):
        """覆盖 create 方法，当从制造订单创建批次号时，自动生成批次号名称"""
        self._normalize_create_vals(vals_list)
        
        if self._is_mrp_auto_lot_override_enabled():
            self._apply_production_context_vals(vals_list, self._get_context_production())

        self._lock_lot_names(vals.get('name') for vals in vals_list)
        lots = super(StockLot, self).create(vals_list)
        lots._check_duplicate_lot_name_across_company()
        return lots
