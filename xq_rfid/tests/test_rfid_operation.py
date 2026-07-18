# -*- coding: utf-8 -*-
from psycopg2.errors import UniqueViolation
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

@tagged("post_install", "-at_install", "xq_rfid")
class TestRfidOperation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.device = cls.env["rfid.device.config"].sudo().create({
            "name": "SI120X1 test device",
            "device_type": "si120x1",
            "adapter_device_id": "reader-1",
            "company_id": cls.company.id,
        })
        
        # We need a quality check for the factory method test
        cls.product = cls.env['product.product'].create({
            'name': 'Test Product',
            'type': 'consu',
        })
        
        # Set up a picking to attach the quality check to
        cls.picking_type = cls.env['stock.picking.type'].search([('company_id', '=', cls.company.id)], limit=1)
        cls.picking = cls.env['stock.picking'].create({
            'picking_type_id': cls.picking_type.id,
            'location_id': cls.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': cls.env.ref('stock.stock_location_stock').id,
        })
        
        # Create a move and move line
        cls.move = cls.env['stock.move'].create({
            'name': 'Test Move',
            'product_id': cls.product.id,
            'product_uom': cls.product.uom_id.id,
            'product_uom_qty': 1.0,
            'picking_id': cls.picking.id,
            'location_id': cls.picking.location_id.id,
            'location_dest_id': cls.picking.location_dest_id.id,
        })
        
        cls.check = cls.env['quality.check'].create({
            'product_id': cls.product.id,
            'picking_id': cls.picking.id,
            'company_id': cls.company.id,
        })

    def test_request_id_is_unique(self):
        op1 = self.env['rfid.operation'].create({
            'request_id': 'test-req-1',
            'device_id': self.device.id,
            'operation_type': 'inventory',
        })
        
        with self.assertRaises(Exception), mute_logger('odoo.sql_db'):
            self.env['rfid.operation'].create({
                'request_id': 'test-req-1',
                'device_id': self.device.id,
                'operation_type': 'write_and_verify',
            })
            
    def test_create_or_get_for_quality_check_is_idempotent(self):
        # Create first operation
        op1 = self.env['rfid.operation'].create_or_get_for_quality_check(self.check, self.device)
        self.assertTrue(op1.request_id.startswith(f"qc-{self.check.id}-write-"))
        self.assertEqual(op1.quality_check_id, self.check)
        self.assertEqual(op1.device_id, self.device)
        self.assertEqual(op1.operation_type, 'write_and_verify')
        self.assertEqual(op1.status, 'draft')
        
        # Get again without retrying - should return the same one
        op2 = self.env['rfid.operation'].create_or_get_for_quality_check(self.check, self.device)
        self.assertEqual(op1.id, op2.id)
        
        # Get with retry - should create a new one and mark old one as cancelled if it was draft/queued/failed
        op1.status = 'failed'
        op3 = self.env['rfid.operation'].create_or_get_for_quality_check(self.check, self.device, retry=True)
        self.assertNotEqual(op1.id, op3.id)
        self.assertEqual(op1.status, 'cancelled')
        self.assertEqual(op3.status, 'draft')
        
