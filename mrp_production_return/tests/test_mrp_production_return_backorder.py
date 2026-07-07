# -*- coding: utf-8 -*-

from odoo.addons.mrp.tests.common import TestMrpCommon
from odoo.tests import Form


class TestMrpProductionReturnBackorder(TestMrpCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.picking_type = cls.env['stock.picking.type'].search(
            [('code', '=', 'mrp_operation')],
            limit=1,
        )
        cls.picking_type.create_backorder = 'always'

    def _create_mo(self, qty=10):
        mo, _bom, _finished, _component_1, _component_2 = self.generate_mo(
            'none',
            'none',
            'none',
            qty_final=qty,
            qty_base_1=1,
            qty_base_2=1,
            picking_type_id=self.picking_type,
            consumption='flexible',
        )
        return mo

    def _set_qty_producing(self, production, qty):
        with Form(production) as production_form:
            production_form.qty_producing = qty

    def _assert_backorder_wizard_action(self, action):
        self.assertIsInstance(action, dict)
        self.assertEqual(action.get('type'), 'ir.actions.act_window')
        self.assertEqual(action.get('res_model'), 'mrp.production.backorder')

    def _confirm_backorder(self, action):
        self._assert_backorder_wizard_action(action)
        wizard = Form(
            self.env['mrp.production.backorder'].with_context(**action['context'])
        ).save()
        return wizard.action_backorder()

    def test_partial_production_always_backorder_still_asks(self):
        mo = self._create_mo(qty=10)
        self._set_qty_producing(mo, 8)

        action = mo.button_mark_done()

        self._assert_backorder_wizard_action(action)
        self.assertEqual(mo.state, 'progress')
        self.assertEqual(len(mo.procurement_group_id.mrp_production_ids), 1)

    def test_backorder_partial_production_still_asks_again(self):
        mo = self._create_mo(qty=10)
        self._set_qty_producing(mo, 5)
        self._confirm_backorder(mo.button_mark_done())

        backorder = (mo.procurement_group_id.mrp_production_ids - mo).sorted('id')
        self.assertEqual(len(backorder), 1)
        self._set_qty_producing(backorder, 4)

        action = backorder.button_mark_done()

        self._assert_backorder_wizard_action(action)
        self.assertEqual(backorder.state, 'progress')
        self.assertEqual(len(mo.procurement_group_id.mrp_production_ids), 2)

    def test_close_without_backorder_opens_component_return_wizard(self):
        mo = self._create_mo(qty=10)
        self._set_qty_producing(mo, 8)
        action = mo.button_mark_done()

        wizard = Form(
            self.env['mrp.production.backorder'].with_context(**action['context'])
        ).save()
        result = wizard.action_close_mo()

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get('type'), 'ir.actions.act_window')
        self.assertEqual(result.get('res_model'), 'mrp.production.return.wizard')
        self.assertEqual(result['context']['default_production_id'], mo.id)
