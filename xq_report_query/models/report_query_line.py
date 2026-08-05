from odoo import fields, models


REPORT_TYPES = [
    ('production', '生产报表'),
    ('finished_stock', '现有成品库存表'),
    ('raw_film_incoming', '原膜汇总表'),
    ('raw_film_stock', '现有原膜库存表'),
    ('solution_incoming', '原液来料汇总报表'),
    ('coating_stock', '现有涂液库存表'),
]


class ReportQueryLine(models.TransientModel):
    _name = 'xq.report.query.line'
    _description = '报表查询结果明细'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'xq.report.query.wizard',
        string='查询向导',
        required=True,
        ondelete='cascade',
    )
    owner_id = fields.Many2one(
        'res.users',
        string='创建人',
        required=True,
        default=lambda self: self.env.user,
        readonly=True,
        index=True,
    )
    sequence = fields.Integer(default=10)
    report_type = fields.Selection(REPORT_TYPES, required=True, index=True)

    # Common descriptive columns.
    report_date = fields.Date(string='业务日期')
    arrival_date = fields.Date(string='到货日期')
    product_name = fields.Char(string='产品名称')
    product_code = fields.Char(string='产品编码')
    product_category = fields.Char(string='产品分类')
    base_film_name = fields.Char(string='基膜品名')
    thickness = fields.Float(string='厚度 (μm)')
    width = fields.Float(string='宽度 (mm)')
    grade = fields.Char(string='等级')
    product_condition = fields.Char(string='产品状况')
    raw_lot_name = fields.Char(string='原膜批号')
    finished_lot_name = fields.Char(string='成品批号')
    lot_name = fields.Char(string='批号')
    supplier_name = fields.Char(string='供应商名称')
    uom_name = fields.Char(string='计量单位')

    # Production and film measurements.
    planned_meters = fields.Float(string='计划米数 (m)')
    actual_meters = fields.Float(string='实际米数 (m)')
    meters = fields.Float(string='米数 (m)')
    area_sqm = fields.Float(string='平方米数 (㎡)')
    yield_rate = fields.Float(string='良品率 (%)')

    # Inventory and liquid quantities.
    quantity = fields.Float(string='数量')
    stock_quantity = fields.Float(string='库存数量（卷/件）')
    quantity_kg = fields.Float(string='数量 (kg)')
    net_weight_ton = fields.Float(string='重量（净重吨）')

    quality_note = fields.Text(string='品质说明')
    remark = fields.Text(string='备注')
