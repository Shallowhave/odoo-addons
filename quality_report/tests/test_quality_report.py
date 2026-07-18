from odoo.tests import TransactionCase


class TestQualityReport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.env.company.id)], limit=1
        )
        cls.customer_location = cls.env.ref('stock.stock_location_customers')
        cls.product = cls.env['product.product'].create({
            'name': 'Quality report lot product',
            'tracking': 'lot',
        })
        cls.lot = cls.env['stock.lot'].create({
            'name': 'QUALITY-REPORT-LOT-001',
            'product_id': cls.product.id,
        })
        cls.picking = cls.env['stock.picking'].create({
            'picking_type_id': cls.warehouse.out_type_id.id,
            'location_id': cls.warehouse.lot_stock_id.id,
            'location_dest_id': cls.customer_location.id,
        })
        cls.move = cls.env['stock.move'].create({
            'name': cls.product.display_name,
            'picking_id': cls.picking.id,
            'product_id': cls.product.id,
            'product_uom_qty': 10.0,
            'product_uom': cls.product.uom_id.id,
            'location_id': cls.warehouse.lot_stock_id.id,
            'location_dest_id': cls.customer_location.id,
        })
        cls.move_line = cls.env['stock.move.line'].create({
            'move_id': cls.move.id,
            'picking_id': cls.picking.id,
            'product_id': cls.product.id,
            'product_uom_id': cls.product.uom_id.id,
            'location_id': cls.warehouse.lot_stock_id.id,
            'location_dest_id': cls.customer_location.id,
            'lot_id': cls.lot.id,
            'quantity': 10.0,
        })
        team = cls.env['quality.alert.team'].search([], limit=1)
        cls.env['quality.alert'].create({
            'name': 'Production quality note',
            'team_id': team.id,
            'product_id': cls.product.id,
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'lot_id': cls.lot.id,
            'description': '<p>Lot quality note from production</p>',
        })

    def test_report_uses_quality_note_from_same_lot(self):
        quality_info = self.picking._get_quality_info()

        self.assertEqual(len(quality_info), 1)
        self.assertEqual(
            quality_info[0]['quality_note'],
            'Lot quality note from production',
        )
