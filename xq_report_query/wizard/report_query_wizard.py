import base64
import io
from datetime import datetime, time
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html2plaintext
from odoo.tools.misc import xlsxwriter

from ..models.report_query_line import REPORT_TYPES


class ReportQueryWizard(models.TransientModel):
    _name = 'xq.report.query.wizard'
    _description = '生产与库存报表查询'

    report_type = fields.Selection(
        REPORT_TYPES,
        string='报表类型',
        required=True,
        default='production',
    )
    date_from = fields.Date(
        string='开始日期',
        required=True,
        default=lambda self: self._default_date_from(),
        help='生产和来料报表按业务日期过滤；库存报表始终查询当前库存。',
    )
    date_to = fields.Date(
        string='结束日期',
        required=True,
        default=lambda self: fields.Date.context_today(self),
        help='生产和来料报表按业务日期过滤；库存报表始终查询当前库存。',
    )
    company_id = fields.Many2one(
        'res.company',
        string='公司',
        required=True,
        default=lambda self: self.env.company,
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='仓库',
        domain="[('company_id', '=', company_id)]",
    )
    product_categ_id = fields.Many2one('product.category', string='产品分类')
    product_id = fields.Many2one(
        'product.product',
        string='产品',
        domain="[('type', '!=', 'service')]",
    )
    lot_keyword = fields.Char(string='批次号关键字')

    production_line_ids = fields.One2many(
        'xq.report.query.line', 'wizard_id',
        string='生产报表明细',
        domain=[('report_type', '=', 'production')],
        readonly=True,
    )
    finished_stock_line_ids = fields.One2many(
        'xq.report.query.line', 'wizard_id',
        string='成品库存明细',
        domain=[('report_type', '=', 'finished_stock')],
        readonly=True,
    )
    raw_film_incoming_line_ids = fields.One2many(
        'xq.report.query.line', 'wizard_id',
        string='原膜汇总明细',
        domain=[('report_type', '=', 'raw_film_incoming')],
        readonly=True,
    )
    raw_film_stock_line_ids = fields.One2many(
        'xq.report.query.line', 'wizard_id',
        string='原膜库存明细',
        domain=[('report_type', '=', 'raw_film_stock')],
        readonly=True,
    )
    solution_incoming_line_ids = fields.One2many(
        'xq.report.query.line', 'wizard_id',
        string='原液来料明细',
        domain=[('report_type', '=', 'solution_incoming')],
        readonly=True,
    )
    coating_stock_line_ids = fields.One2many(
        'xq.report.query.line', 'wizard_id',
        string='涂液库存明细',
        domain=[('report_type', '=', 'coating_stock')],
        readonly=True,
    )
    result_count = fields.Integer(string='结果行数', readonly=True)
    file_data = fields.Binary(string='Excel 文件', readonly=True, attachment=False)
    file_name = fields.Char(string='文件名', readonly=True)

    @api.model
    def _default_date_from(self):
        today = fields.Date.context_today(self)
        return today.replace(day=1)

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for wizard in self:
            if wizard.date_from and wizard.date_to and wizard.date_from > wizard.date_to:
                raise ValidationError(_('开始日期不能晚于结束日期。'))

    @api.onchange('company_id')
    def _onchange_company_id(self):
        if self.warehouse_id and self.warehouse_id.company_id != self.company_id:
            self.warehouse_id = False

    def _date_bounds(self):
        self.ensure_one()
        start = datetime.combine(self.date_from, time.min)
        end = datetime.combine(self.date_to, time.max)
        return start, end

    def _warehouse_location_ids(self):
        self.ensure_one()
        if not self.warehouse_id or not self.warehouse_id.view_location_id:
            return set()
        locations = self.env['stock.location'].search([
            ('id', 'child_of', self.warehouse_id.view_location_id.id),
            ('usage', '=', 'internal'),
        ])
        return set(locations.ids)

    def _category_ids(self, keyword=None):
        self.ensure_one()
        if self.product_categ_id:
            return self.product_categ_id.ids
        if keyword:
            categories = self.env['product.category'].search([
                ('name', 'ilike', keyword),
            ])
            return categories.ids
        return []

    def _product_domain(self, product_field='product_id', keyword=None):
        self.ensure_one()
        domain = []
        if self.product_id:
            domain.append((product_field, '=', self.product_id.id))
        category_ids = self._category_ids(keyword=keyword) if not self.product_id else (
            self.product_categ_id.ids if self.product_categ_id else []
        )
        if category_ids:
            domain.append((f'{product_field}.product_tmpl_id.categ_id', 'child_of', category_ids))
        elif keyword and not self.product_id:
            # No matching category means there can be no matching report rows.
            domain.append((product_field, '=', False))
        return domain

    def _lot_domain(self, lot_field='lot_id', lot_name_field=None):
        self.ensure_one()
        if not self.lot_keyword:
            return []
        lot_domain = [(f'{lot_field}.name', 'ilike', self.lot_keyword)]
        if lot_name_field:
            lot_domain = ['|', *lot_domain, (lot_name_field, 'ilike', self.lot_keyword)]
        return lot_domain

    def _location_domain(self, field_name):
        self.ensure_one()
        if not self.warehouse_id:
            return []
        return [(field_name, 'child_of', self.warehouse_id.view_location_id.id)]

    def _clear_lines(self):
        self.env['xq.report.query.line'].sudo().search([
            ('wizard_id', 'in', self.ids),
        ]).unlink()

    def _category_text(self, product):
        category = product.product_tmpl_id.categ_id
        return category.complete_name or category.name or '' if category else ''

    def _category_matches(self, product, keyword):
        return keyword in self._category_text(product)

    @staticmethod
    def _is_area_uom(uom):
        name = (uom.name or '').lower() if uom else ''
        return any(token in name for token in ('平米', '平方米', 'sqm', 'm²', 'm2'))

    @staticmethod
    def _is_length_uom(uom):
        name = (uom.name or '').lower() if uom else ''
        return not ReportQueryWizard._is_area_uom(uom) and (
            '米' in name or name in ('m', 'meter', 'meters')
        )

    def _quantity_in_product_uom(self, quantity, from_uom, product):
        if not product or not product.uom_id or not from_uom:
            return quantity or 0.0
        return from_uom._compute_quantity(quantity or 0.0, product.uom_id)

    def _length_from_quantity(self, product, quantity, uom=None):
        quantity = quantity or 0.0
        uom = uom or (product and product.uom_id)
        if self._is_length_uom(uom):
            return quantity
        template = product.product_tmpl_id if product else False
        width = (template.product_width or 0.0) if template else 0.0
        if self._is_area_uom(uom):
            return quantity / (width / 1000.0) if width else 0.0
        product_length = (template.product_length or 0.0) if template else 0.0
        return quantity * product_length

    def _line_quantity_in_product_uom(self, line):
        if 'quantity_product_uom' in line._fields:
            return line.quantity_product_uom or 0.0
        return self._quantity_in_product_uom(
            line.quantity,
            line.product_uom_id,
            line.product_id,
        )

    def _line_meters(self, line):
        quantity = self._line_quantity_in_product_uom(line)
        return self._length_from_quantity(line.product_id, quantity, line.product_id.uom_id)

    @staticmethod
    def _area_from_meters(product, meters):
        width = (product.product_tmpl_id.product_width or 0.0) if product else 0.0
        return meters * width / 1000.0 if width else 0.0

    @staticmethod
    def _weight_ton(product, area_sqm):
        if not product:
            return 0.0
        template = product.product_tmpl_id
        if template.weight_per_sqm:
            return area_sqm * template.weight_per_sqm / 1000.0
        if template.finished_density and template.product_thickness:
            return (
                area_sqm * template.product_thickness * template.finished_density
                / 1_000_000.0
            )
        return 0.0

    def _lot_notes(self, lot, product=None):
        if not lot:
            return ''
        quant_model = self.env['stock.quant']
        if 'o_note1' not in quant_model._fields:
            return ''
        domain = [('lot_id', '=', lot.id)]
        if product:
            domain.append(('product_id', '=', product.id))
        notes = []
        for quant in quant_model.search(domain, order='id'):
            for field_name in ('o_note1', 'o_note2'):
                value = getattr(quant, field_name, False)
                if value and value not in notes:
                    notes.append(value)
        return ', '.join(notes)

    def _lot_arrival_date(self, lot, product=None):
        if not lot:
            return False
        domain = [
            ('lot_id', '=', lot.id),
            ('state', '=', 'done'),
            ('location_dest_id.usage', '=', 'internal'),
        ]
        if product:
            domain.append(('product_id', '=', product.id))
        line = self.env['stock.move.line'].search(domain, order='date asc, id asc', limit=1)
        return fields.Date.to_date(line.date) if line else False

    def _quality_note(self, lot, product):
        if not lot:
            return ''
        try:
            alerts = self.env['quality.alert'].search([
                ('lot_id', '=', lot.id),
                ('description', '!=', False),
            ], order='id desc', limit=1)
        except KeyError:
            return ''
        if not alerts:
            return ''
        return html2plaintext(alerts.description or '').strip()

    def _base_line_values(self, report_type, sequence, product):
        template = product.product_tmpl_id if product else False
        return {
            'wizard_id': self.id,
            'owner_id': self.env.user.id,
            'report_type': report_type,
            'sequence': sequence,
            'product_name': product.name if product else '',
            'product_code': product.default_code if product else '',
            'product_category': self._category_text(product) if product else '',
            'thickness': template.product_thickness if template else 0.0,
            'width': template.product_width if template else 0.0,
            'uom_name': product.uom_id.name if product and product.uom_id else '',
        }

    def _build_production_lines(self):
        start, end = self._date_bounds()
        domain = [
            ('state', '=', 'done'),
            ('company_id', '=', self.company_id.id),
            ('date_finished', '>=', start),
            ('date_finished', '<=', end),
        ]
        domain += self._product_domain(product_field='product_id')
        productions = self.env['mrp.production'].search(domain, order='date_finished asc, id asc')
        warehouse_locations = self._warehouse_location_ids()
        result = []
        sequence = 1
        for production in productions:
            finished_lines = production.move_finished_ids.filtered(
                lambda move: move.state == 'done' and move.product_id == production.product_id
            ).mapped('move_line_ids').filtered(lambda line: line.quantity > 0)
            if warehouse_locations:
                finished_lines = finished_lines.filtered(
                    lambda line: line.location_dest_id.id in warehouse_locations
                )
            if not finished_lines:
                continue

            raw_lines = production.move_raw_ids.filtered(
                lambda move: move.state == 'done'
            ).mapped('move_line_ids').filtered(lambda line: line.quantity > 0)
            raw_film_lines = raw_lines.filtered(
                lambda line: self._category_matches(line.product_id, '原膜')
            )
            if self.lot_keyword:
                keyword = self.lot_keyword.lower()
                matching_finished_lines = finished_lines.filtered(
                    lambda line: keyword in (
                        (line.lot_id.name if line.lot_id else line.lot_name or '')
                    ).lower()
                )
                matching_raw_lines = raw_film_lines.filtered(
                    lambda line: keyword in (
                        (line.lot_id.name if line.lot_id else line.lot_name or '')
                    ).lower()
                )
                if not matching_finished_lines and not matching_raw_lines:
                    continue
                if matching_finished_lines:
                    # A finished-lot hit narrows output rows but keeps all raw
                    # inputs so the row remains useful for traceability.
                    finished_lines = matching_finished_lines
                else:
                    # A raw-lot hit selects the whole production order: every
                    # finished line shares that order's consumed raw material.
                    raw_film_lines = matching_raw_lines
            raw_products = []
            raw_lots = []
            for raw_line in raw_film_lines:
                if raw_line.product_id.name not in raw_products:
                    raw_products.append(raw_line.product_id.name)
                if raw_line.lot_id and raw_line.lot_id.name not in raw_lots:
                    raw_lots.append(raw_line.lot_id.name)

            total_done_qty = sum(
                self._line_quantity_in_product_uom(line) for line in finished_lines
            )
            planned_total = self._length_from_quantity(
                production.product_id,
                production.product_uom_id._compute_quantity(
                    production.product_qty,
                    production.product_id.uom_id,
                ),
                production.product_id.uom_id,
            )
            for line in finished_lines:
                actual_meters = self._line_meters(line)
                line_qty = self._line_quantity_in_product_uom(line)
                planned_meters = (
                    planned_total * line_qty / total_done_qty
                    if total_done_qty else planned_total
                )
                values = self._base_line_values('production', sequence, production.product_id)
                values.update({
                    'report_date': fields.Date.to_date(production.date_finished),
                    'base_film_name': ', '.join(raw_products),
                    'raw_lot_name': ', '.join(raw_lots),
                    'finished_lot_name': line.lot_id.name if line.lot_id else '',
                    'planned_meters': round(planned_meters, 2),
                    'actual_meters': round(actual_meters, 2),
                    'area_sqm': round(self._area_from_meters(production.product_id, actual_meters), 2),
                    'yield_rate': round(actual_meters / planned_meters * 100.0, 2)
                    if planned_meters else 0.0,
                    'quality_note': self._quality_note(line.lot_id, production.product_id),
                    'remark': production.contract_no or production.origin or '',
                })
                result.append(values)
                sequence += 1
        return result

    def _quant_rows(self, keyword):
        domain = [
            ('company_id', '=', self.company_id.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ]
        domain += self._location_domain('location_id')
        domain += self._product_domain(keyword=keyword)
        domain += self._lot_domain()
        return self.env['stock.quant'].search(domain, order='product_id, lot_id, id')

    def _group_quants(self, quants):
        groups = {}
        for quant in quants:
            key = (quant.product_id.id, quant.lot_id.id or 0)
            if key not in groups:
                groups[key] = {
                    'product': quant.product_id,
                    'lot': quant.lot_id,
                    'quantity': 0.0,
                    'meters': 0.0,
                    'has_actual_meters': False,
                    'notes': [],
                    'arrival_date': False,
                }
            group = groups[key]
            group['quantity'] += quant.quantity or 0.0
            if quant.lot_id and quant.actual_length_m:
                group['meters'] += quant.actual_length_m
                group['has_actual_meters'] = True
            for note in (getattr(quant, 'o_note1', False), getattr(quant, 'o_note2', False)):
                if note and note not in group['notes']:
                    group['notes'].append(note)
            if quant.in_date:
                quant_date = fields.Date.to_date(quant.in_date)
                if not group['arrival_date'] or quant_date < group['arrival_date']:
                    group['arrival_date'] = quant_date
        return groups.values()

    def _build_finished_stock_lines(self):
        result = []
        for sequence, group in enumerate(self._group_quants(self._quant_rows('成品')), 1):
            product = group['product']
            quantity = group['quantity']
            meters = (
                group['meters'] if group['has_actual_meters']
                else self._length_from_quantity(product, quantity, product.uom_id)
            )
            area_sqm = self._area_from_meters(product, meters)
            values = self._base_line_values('finished_stock', sequence, product)
            values.update({
                'report_date': fields.Date.context_today(self),
                'lot_name': group['lot'].name if group['lot'] else '',
                'meters': round(meters, 2),
                'area_sqm': round(area_sqm, 2),
                'quantity': round(area_sqm, 2),
                'stock_quantity': round(quantity, 2),
                'net_weight_ton': round(self._weight_ton(product, area_sqm), 4),
                'remark': ', '.join(group['notes']),
            })
            result.append(values)
        return result

    def _build_raw_film_stock_lines(self):
        result = []
        for sequence, group in enumerate(self._group_quants(self._quant_rows('原膜')), 1):
            product = group['product']
            quantity = group['quantity']
            meters = (
                group['meters'] if group['has_actual_meters']
                else self._length_from_quantity(product, quantity, product.uom_id)
            )
            values = self._base_line_values('raw_film_stock', sequence, product)
            values.update({
                'arrival_date': group['arrival_date'] or self._lot_arrival_date(group['lot'], product),
                'lot_name': group['lot'].name if group['lot'] else '',
                'meters': round(meters, 2),
                'remark': ', '.join(group['notes']),
            })
            result.append(values)
        return result

    def _incoming_lines(self, keyword, report_type):
        start, end = self._date_bounds()
        domain = [
            ('state', '=', 'done'),
            ('company_id', '=', self.company_id.id),
            ('picking_id.picking_type_id.code', '=', 'incoming'),
            ('date', '>=', start),
            ('date', '<=', end),
            ('location_dest_id.usage', '=', 'internal'),
        ]
        domain += self._location_domain('location_dest_id')
        domain += self._product_domain(keyword=keyword)
        domain += self._lot_domain(lot_name_field='lot_name')
        lines = self.env['stock.move.line'].search(domain, order='date asc, id asc')
        return lines, report_type

    def _build_raw_film_incoming_lines(self):
        lines, report_type = self._incoming_lines('原膜', 'raw_film_incoming')
        result = []
        for sequence, line in enumerate(lines, 1):
            product = line.product_id
            meters = self._line_meters(line)
            values = self._base_line_values(report_type, sequence, product)
            values.update({
                'arrival_date': fields.Date.to_date(line.date),
                'lot_name': line.lot_id.name if line.lot_id else line.lot_name or '',
                'meters': round(meters, 2),
                'grade': '',
                'product_condition': '',
                'remark': self._lot_notes(line.lot_id, product),
            })
            result.append(values)
        return result

    def _line_quantity_kg(self, line):
        quantity = self._line_quantity_in_product_uom(line)
        product = line.product_id
        if not product or not product.uom_id:
            return quantity
        try:
            kg_uom = self.env.ref('uom.product_uom_kgm')
        except ValueError:
            return quantity
        if product.uom_id.category_id == kg_uom.category_id:
            return product.uom_id._compute_quantity(quantity, kg_uom)
        return quantity

    def _build_solution_incoming_lines(self):
        lines, report_type = self._incoming_lines('原液', 'solution_incoming')
        result = []
        for sequence, line in enumerate(lines.sorted(key=lambda record: (record.date, record.id)), 1):
            product = line.product_id
            values = self._base_line_values(report_type, sequence, product)
            supplier = line.picking_id.partner_id or line.move_id.purchase_line_id.order_id.partner_id
            quantity_kg = self._line_quantity_kg(line)
            values.update({
                'arrival_date': fields.Date.to_date(line.date),
                'lot_name': line.lot_id.name if line.lot_id else line.lot_name or '',
                'quantity': round(quantity_kg, 2),
                'quantity_kg': round(quantity_kg, 2),
                'supplier_name': supplier.name if supplier else '',
                'remark': self._lot_notes(line.lot_id, product),
            })
            result.append(values)
        return result

    def _build_coating_stock_lines(self):
        result = []
        solution_groups = list(self._group_quants(self._quant_rows('原液')))
        coating_groups = list(self._group_quants(self._quant_rows('涂液')))
        groups = solution_groups + coating_groups
        for sequence, group in enumerate(groups, 1):
            product = group['product']
            quantity = group['quantity']
            values = self._base_line_values('coating_stock', sequence, product)
            values.update({
                'arrival_date': group['arrival_date'] or self._lot_arrival_date(group['lot'], product),
                'lot_name': group['lot'].name if group['lot'] else '',
                'quantity': round(quantity, 2),
                'quantity_kg': round(quantity, 2),
                'remark': ', '.join(group['notes']),
            })
            result.append(values)
        return result

    def _build_lines(self):
        builders = {
            'production': self._build_production_lines,
            'finished_stock': self._build_finished_stock_lines,
            'raw_film_incoming': self._build_raw_film_incoming_lines,
            'raw_film_stock': self._build_raw_film_stock_lines,
            'solution_incoming': self._build_solution_incoming_lines,
            'coating_stock': self._build_coating_stock_lines,
        }
        return builders[self.report_type]()

    def _refresh_lines(self):
        self.ensure_one()
        self._check_dates()
        self._clear_lines()
        values = self._build_lines()
        if values:
            self.env['xq.report.query.line'].sudo().create(values)
        self.result_count = len(values)
        return self.env['xq.report.query.line'].search([
            ('wizard_id', '=', self.id),
            ('report_type', '=', self.report_type),
        ], order='sequence, id')

    def _reload_action(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('报表查询'),
            'res_model': self._name,
            'view_mode': 'form',
            'views': [(self.env.ref('xq_report_query.view_report_query_wizard_form').id, 'form')],
            'res_id': self.id,
            'target': 'current',
        }

    def action_query(self):
        self.ensure_one()
        self._refresh_lines()
        return self._reload_action()

    def _format_date(self, value):
        return fields.Date.to_string(value) if value else ''

    def _xlsx_formats(self, workbook):
        border = {'border': 1, 'align': 'center', 'valign': 'vcenter', 'font_name': 'SimSun'}
        return {
            'title': workbook.add_format({
                'bold': True, 'font_size': 16, 'align': 'center', 'valign': 'vcenter',
                'font_name': 'SimSun',
            }),
            'header': workbook.add_format({
                **border, 'bold': True, 'bg_color': '#FFFF00', 'font_size': 11,
                'text_wrap': True,
            }),
            'text': workbook.add_format({**border, 'font_size': 10}),
            'number': workbook.add_format({**border, 'font_size': 10, 'num_format': '0.00'}),
            'percent': workbook.add_format({**border, 'font_size': 10, 'num_format': '0.00%'}),
            'total_label': workbook.add_format({
                **border, 'bold': True, 'font_size': 10, 'align': 'right',
            }),
        }

    @staticmethod
    def _safe_text(value):
        return value or ''

    @staticmethod
    def _sum_formula(column, row_count):
        if not row_count:
            return '=0'
        return f'=SUM({column}3:{column}{row_count + 2})'

    @staticmethod
    def _average_formula(column, row_count):
        if not row_count:
            return '=0'
        return f'=IFERROR(AVERAGE({column}3:{column}{row_count + 2}),0)'

    def _write_rows(self, sheet, formats, headers, rows, widths, title, total=None):
        sheet.set_default_row(22)
        for index, width in enumerate(widths):
            sheet.set_column(index, index, width)
        sheet.merge_range(0, 0, 0, len(headers) - 1, title, formats['title'])
        for column, header in enumerate(headers):
            sheet.write(1, column, header, formats['header'])
        for row_number, row in enumerate(rows, 2):
            for column, value in enumerate(row):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    sheet.write_number(row_number, column, value, formats['number'])
                else:
                    text = '' if value in (None, False) else str(value)
                    # Keep business text as text so values beginning with '=' are
                    # never interpreted as formulas when opened in Excel.
                    sheet.write_string(row_number, column, text, formats['text'])
        if total:
            total_row = len(rows) + 2
            sheet.merge_range(total_row, 0, total_row, total['merge_to'], total['label'], formats['total_label'])
            for column, formula in total['formulas'].items():
                formula_format = total.get('formats', {}).get(column, formats['number'])
                sheet.write_formula(total_row, column, formula, formula_format)
        sheet.freeze_panes(2, 0)
        sheet.autofilter(1, 0, max(1, len(rows) + 1), len(headers) - 1)

    def _production_xlsx(self, workbook, formats, lines):
        sheet = workbook.add_worksheet('生产报表')
        headers = ['产品型号', '基膜品名', '厚度\n(μm)', '宽度(mm)', '原膜批号', '成品批号', '米数', '实际米数', '平方米数', '良品率', '品质说明', '备注']
        widths = [35, 14, 10, 12, 28, 24, 12, 12, 14, 12, 22, 20]
        row_values = []
        for index, line in enumerate(lines, 3):
            row_values.append([
                self._safe_text(line.product_name), self._safe_text(line.base_film_name),
                line.thickness, line.width, self._safe_text(line.raw_lot_name),
                self._safe_text(line.finished_lot_name), line.planned_meters,
                line.actual_meters, line.area_sqm, line.yield_rate / 100.0,
                self._safe_text(line.quality_note), self._safe_text(line.remark),
            ])
        self._write_rows(
            sheet, formats, headers, row_values, widths, '生  产  报  表',
            total={
                'merge_to': 5, 'label': '合计：',
                'formulas': {
                    6: self._sum_formula('G', len(lines)),
                    7: self._sum_formula('H', len(lines)),
                    8: self._sum_formula('I', len(lines)),
                    9: self._average_formula('J', len(lines)),
                },
                'formats': {9: formats['percent']},
            },
        )
        for row in range(3, len(lines) + 3):
            sheet.write_formula(row - 1, 8, f'=D{row}*H{row}/1000', formats['number'])
            sheet.write_formula(row - 1, 9, f'=IFERROR(H{row}/G{row},0)', formats['percent'])

    def _finished_stock_xlsx(self, workbook, formats, lines):
        sheet = workbook.add_worksheet('现有成品库存表')
        headers = ['序号', '产品名称', '厚度/μm', '宽度/㎜', '米数/m', '成品批号', '数量', '重量（净重吨）', '备注']
        widths = [8, 34, 12, 12, 12, 26, 12, 18, 22]
        rows = []
        for index, line in enumerate(lines, 1):
            rows.append([index, line.product_name, line.thickness, line.width, line.meters, line.lot_name, line.quantity, line.net_weight_ton, line.remark])
        self._write_rows(
            sheet, formats, headers, rows, widths, '现 有 成 品 库 存 表',
            total={
                'merge_to': 5, 'label': '合计',
                'formulas': {
                    6: self._sum_formula('G', len(lines)),
                    7: self._sum_formula('H', len(lines)),
                },
            },
        )
        for row in range(3, len(lines) + 3):
            sheet.write_formula(row - 1, 6, f'=D{row}*E{row}/1000', formats['number'])

    def _raw_film_xlsx(self, workbook, formats, lines, title, sheet_name):
        sheet = workbook.add_worksheet(sheet_name)
        headers = ['到货日期', '基膜品名', '厚度', '宽度', '米数', '等级', '原膜批号', '产品状况', '备注']
        widths = [14, 16, 12, 12, 12, 12, 26, 14, 22]
        rows = []
        for line in lines:
            rows.append([self._format_date(line.arrival_date), line.product_name, line.thickness, line.width, line.meters, line.grade, line.lot_name, line.product_condition, line.remark])
        self._write_rows(sheet, formats, headers, rows, widths, title)

    def _solution_incoming_xlsx(self, workbook, formats, lines):
        sheet = workbook.add_worksheet('原液来料汇总报表')
        headers = ['到货日期', '物料型号', '数量(KG）', '批号', '供应商名称', '备注']
        widths = [14, 22, 16, 24, 24, 22]
        rows = []
        for line in lines:
            rows.append([self._format_date(line.arrival_date), line.product_name, line.quantity_kg, line.lot_name, line.supplier_name, line.remark])
        self._write_rows(sheet, formats, headers, rows, widths, '原 液 来 料 汇 总 表')

    def _coating_stock_xlsx(self, workbook, formats, lines):
        sheet = workbook.add_worksheet('现有涂液库存表')
        headers = ['到货日期', '物料型号', '剩余数量(KG)', '批号', '备注']
        widths = [14, 22, 18, 24, 22]
        rows = []
        for line in lines:
            rows.append([self._format_date(line.arrival_date), line.product_name, line.quantity_kg, line.lot_name, line.remark])
        self._write_rows(sheet, formats, headers, rows, widths, '现 有 涂 液 库 存 表')

    def _build_workbook(self, lines):
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        formats = self._xlsx_formats(workbook)
        builders = {
            'production': self._production_xlsx,
            'finished_stock': self._finished_stock_xlsx,
            'raw_film_incoming': lambda wb, fm, ls: self._raw_film_xlsx(wb, fm, ls, '原 膜 汇 总 表', '原膜汇总表'),
            'raw_film_stock': lambda wb, fm, ls: self._raw_film_xlsx(wb, fm, ls, '现 有 原 膜 库 存 表', '现有原膜库存表'),
            'solution_incoming': self._solution_incoming_xlsx,
            'coating_stock': self._coating_stock_xlsx,
        }
        builders[self.report_type](workbook, formats, lines)
        workbook.close()
        return output.getvalue()

    def action_export_xlsx(self):
        self.ensure_one()
        lines = self._refresh_lines()
        content = self._build_workbook(lines)
        report_label = dict(REPORT_TYPES).get(self.report_type, '报表')
        filename = f'{report_label}_{self.date_from}_{self.date_to}.xlsx'
        self.write({
            'file_data': base64.b64encode(content),
            'file_name': filename,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': (
                f'/web/content?model={self._name}&id={self.id}&field=file_data'
                f'&filename_field=file_name&download=true&filename={quote(filename)}'
            ),
            'target': 'self',
        }
