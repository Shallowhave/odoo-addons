import base64
import zipfile
from io import BytesIO

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestReportQueryWizard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.category = cls.env['product.category'].create({'name': '测试成品'})
        cls.product = cls.env['product.template'].create({
            'name': '测试成品 1000mm',
            'categ_id': cls.category.id,
            'type': 'consu',
            'product_width': 1000,
            'product_length': 1200,
            'product_thickness': 188,
            'finished_density': 1.4,
        }).product_variant_id

    def _wizard(self, **values):
        defaults = {
            'report_type': 'finished_stock',
            'date_from': '2026-01-01',
            'date_to': '2026-01-31',
        }
        defaults.update(values)
        return self.env['xq.report.query.wizard'].create(defaults)

    def test_measurement_helpers_use_product_dimensions(self):
        wizard = self._wizard()
        self.assertEqual(wizard._length_from_quantity(self.product, 2, self.product.uom_id), 2400)
        self.assertEqual(wizard._area_from_meters(self.product, 2400), 2400)
        self.assertAlmostEqual(wizard._weight_ton(self.product, 2400), 0.63168, places=6)

    def test_invalid_date_range_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._wizard(date_from='2026-02-01', date_to='2026-01-31')

    def test_empty_report_can_be_exported_as_xlsx(self):
        wizard = self._wizard()
        action = wizard.action_export_xlsx()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertTrue(wizard.file_name.endswith('.xlsx'))
        content = base64.b64decode(wizard.file_data)
        self.assertTrue(zipfile.is_zipfile(BytesIO(content)))

    def test_finished_stock_export_keeps_template_formulas(self):
        wizard = self._wizard()
        values = wizard._base_line_values('finished_stock', 1, self.product)
        values.update({
            'lot_name': 'LOT-TEST-001',
            'meters': 1200.0,
            'quantity': 1200.0,
            'net_weight_ton': 0.324,
        })
        line = self.env['xq.report.query.line'].sudo().create(values)
        content = wizard._build_workbook(line)
        with zipfile.ZipFile(BytesIO(content)) as workbook:
            sheet_xml = workbook.read('xl/worksheets/sheet1.xml').decode()
        self.assertIn('<f>D3*E3/1000</f>', sheet_xml)
        self.assertIn('<f>SUM(G3:G3)</f>', sheet_xml)
        self.assertIn('<f>SUM(H3:H3)</f>', sheet_xml)
        self.assertEqual(wizard._sum_formula('G', 0), '=0')
        self.assertEqual(wizard._average_formula('J', 0), '=0')

    def test_result_lines_are_owned_by_current_user(self):
        wizard = self._wizard()
        values = wizard._base_line_values('finished_stock', 1, self.product)
        line = self.env['xq.report.query.line'].sudo().create(values)
        self.assertEqual(line.owner_id, self.env.user)
        self.assertEqual(line.wizard_id, wizard)
