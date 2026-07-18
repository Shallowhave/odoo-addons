# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "xq_rfid")
class TestQualityRfidFlow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        
        # Enable tracking for quality.check to avoid registry mismatch in tests if needed
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        
        cls.manager = cls.env["res.users"].create({
            "name": "RFID Manager",
            "login": "rfid_flow_manager",
            "groups_id": [(6, 0, [cls.env.ref("xq_rfid.group_rfid_manager").id, cls.env.ref("quality.group_quality_manager").id])],
        })
        
        cls.device = cls.env["rfid.device.config"].sudo().create({
            "name": "SI120X1 test",
            "device_type": "si120x1",
            "adapter_device_id": "reader-1",
            "company_id": cls.company.id,
            "active": True,
        })
        
        # Bypass connection test for setup
        cls.device.write({
            "validation_state": "validated",
            "connection_status": "connected",
        })
        
        cls.product = cls.env['product.product'].create({
            'name': 'Test Flow Product',
            'type': 'product',
            'tracking': 'lot',
        })
        
        cls.test_type = cls.env.ref("xq_rfid.test_type_rfid_write")
        
        cls.point = cls.env['quality.point'].create({
            'name': 'Test RFID Write Point',
            'product_ids': [(4, cls.product.id)],
            'picking_type_ids': [(4, cls.env['stock.picking.type'].search([('company_id', '=', cls.company.id)], limit=1).id)],
            'test_type_id': cls.test_type.id,
            'rfid_device_required': True,
            'rfid_device_id': cls.device.id,
            'company_id': cls.company.id,
        })
        
        # Prepare a production order
        cls.production = cls.env['mrp.production'].create({
            'product_id': cls.product.id,
            'product_qty': 1.0,
            'product_uom_id': cls.product.uom_id.id,
        })
        cls.production.action_confirm()
        
        cls.check = cls.env['quality.check'].create({
            'point_id': cls.point.id,
            'product_id': cls.product.id,
            'production_id': cls.production.id,
            'company_id': cls.company.id,
            'test_type_id': cls.test_type.id,
        })
        
        cls.env = cls.env(user=cls.manager)
        cls.check = cls.check.with_user(cls.manager)

    def test_first_pass_request_queues_operation_but_keeps_check_open(self):
        # Initial pass
        self.check.do_pass()
        
        # Check should remain open/pending
        self.assertEqual(self.check.quality_state, "none")
        
        # Operation should be queued
        op = self.env["rfid.operation"].search([("quality_check_id", "=", self.check.id)])
        self.assertEqual(len(op), 1)
        self.assertEqual(op.status, "queued")

    def test_completing_operation_via_callback_context_passes_check(self):
        op = self.env["rfid.operation"].search([("quality_check_id", "=", self.check.id)])
        if not op:
            # Re-queue if previous test cleaned up or didn't run
            self.check.do_pass()
            op = self.env["rfid.operation"].search([("quality_check_id", "=", self.check.id)])
            
        op.status = "succeeded"
        
        # Simulate cron or webhook calling do_pass again with context flag
        self.check.with_context(xq_rfid_complete_operation_id=op.id).do_pass()
        
        self.assertEqual(self.check.quality_state, "pass")

    def test_missing_finished_lot_does_not_create_operation_or_pass(self):
        label_point = self.env['quality.point'].create({
            'name': 'Test RFID Label Point',
            'product_ids': [(4, self.product.id)],
            'picking_type_ids': [(4, self.env['stock.picking.type'].search([('company_id', '=', self.company.id)], limit=1).id)],
            'test_type_id': self.env.ref("xq_rfid.test_type_rfid_label").id,
            'rfid_device_required': True,
            'rfid_device_id': self.device.id,
            'company_id': self.company.id,
        })
        
        check = self.env['quality.check'].create({
            'point_id': label_point.id,
            'product_id': self.product.id,
            'production_id': self.production.id,
            'company_id': self.company.id,
            'test_type_id': label_point.test_type_id.id,
        })
        
        # Label generation requires finished_lot
        with self.assertRaisesRegex(UserError, "请先设置成品批次/序列号"):
            check.do_pass()
            
        self.assertEqual(check.quality_state, "none")
        self.assertFalse(self.env["rfid.operation"].search([("quality_check_id", "=", check.id)]))
