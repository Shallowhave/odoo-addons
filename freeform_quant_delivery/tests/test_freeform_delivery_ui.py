from odoo import Command
from odoo.addons.stock_barcode.tests.test_barcode_client_action import (
    TestBarcodeClientAction,
)
from odoo.tests import new_test_user, tagged


@tagged("post_install", "-at_install")
class TestFreeformDeliveryBarcodeUI(TestBarcodeClientAction):
    def setUp(self):
        super().setUp()
        self.clean_access_rights()
        self.barcode_user = new_test_user(
            self.env,
            login="freeform_barcode_manager",
            groups="base.group_user,stock.group_stock_manager",
            company_id=self.env.company.id,
            company_ids=[Command.link(self.env.company.id)],
        )
        self.barcode_user.password = self.barcode_user.login
        self.barcode_user.write(
            {
                "groups_id": [
                    Command.link(self.env.ref("stock.group_production_lot").id),
                    Command.link(self.env.ref("stock.group_tracking_lot").id),
                    Command.link(self.env.ref("stock.group_tracking_owner").id),
                    Command.link(self.env.ref("uom.group_uom").id),
                ]
            }
        )
        self.picking_type_out.write(
            {
                "use_existing_lots": True,
                "use_create_lots": False,
                "show_reserved_sns": True,
                "reservation_method": "manual",
            }
        )
        self.area_uom = self.env.ref("uom.uom_square_meter")
        self.picking_type_out.default_location_src_id = self.stock_location
        self.customer = self.env["res.partner"].create(
            {"name": "Free-form Barcode Customer"}
        )
        self.delivery_address = self.env["res.partner"].create(
            {
                "name": "Free-form Barcode Delivery Address",
                "parent_id": self.customer.id,
                "type": "delivery",
            }
        )

    def _create_quant(
        self, product, suffix, quantity, *, package=False, owner=False
    ):
        lot = self.env["stock.lot"].create(
            {
                "name": f"FFD-LOT-{suffix}",
                "product_id": product.id,
                "company_id": self.env.company.id,
            }
        )
        package = package or self.env["stock.quant.package"].create(
            {"name": f"FFD-PACKAGE-{suffix}"}
        )
        self.env["stock.quant"]._update_available_quantity(
            product,
            self.stock_location,
            quantity,
            lot_id=lot,
            package_id=package,
            owner_id=owner,
        )
        quant = self.env["stock.quant"]._gather(
            product,
            self.stock_location,
            lot_id=lot,
            package_id=package,
            owner_id=owner,
            strict=True,
        ).ensure_one()
        return quant, lot, package

    def _create_generated_picking(self, suffix, reserved_quantity):
        product = self.env["product.product"].create(
            {
                "name": f"Free-form Barcode Area {suffix}",
                "barcode": f"FFD-AREA-{suffix}",
                "is_storable": True,
                "tracking": "lot",
                "uom_id": self.area_uom.id,
                "uom_po_id": self.area_uom.id,
            }
        )
        target_quant, target_lot, target_package = self._create_quant(
            product, suffix, reserved_quantity
        )
        decoy_quant, _decoy_lot, _decoy_package = self._create_quant(
            product,
            f"{suffix}-DECOY",
            0.5,
            package=target_package,
        )

        wizard = self.env["freeform.delivery.wizard"].create(
            {
                "company_id": self.env.company.id,
                "picking_type_id": self.picking_type_out.id,
                "customer_id": self.customer.id,
                "delivery_address_id": self.delivery_address.id,
                "product_line_ids": [
                    Command.create({"product_id": product.id})
                ],
            }
        )
        wizard.action_next()
        quantities = {target_quant.id: reserved_quantity, decoy_quant.id: 0.5}
        for quant_line in wizard.quant_line_ids:
            if quant_line.quant_id.id in quantities:
                quant_line.selected_quantity = quantities[quant_line.quant_id.id]
        form_action = wizard.action_confirm_delivery()
        picking = wizard.picking_id
        target_line = picking.move_line_ids.filtered(
            lambda line: line.lot_id == target_lot
        ).ensure_one()

        self.assertEqual(form_action["res_model"], "stock.picking")
        self.assertEqual(form_action["res_id"], picking.id)
        self.assertEqual(len(picking.move_line_ids), 2)
        self.assertEqual(target_line.location_id, self.stock_location)
        self.assertEqual(target_line.lot_id, target_lot)
        self.assertEqual(target_line.package_id, target_package)
        self.assertFalse(target_line.owner_id)
        self.assertFalse(target_line.picked)
        return (
            picking,
            target_line,
            target_lot,
            target_package,
            set(picking.move_line_ids.ids),
        )

    def _assert_original_lines_survive(
        self,
        picking,
        target_line,
        lot,
        package,
        original_line_ids,
    ):
        picking.invalidate_recordset(["move_line_ids"])
        target_line.invalidate_recordset()
        self.assertTrue(target_line.exists())
        self.assertEqual(set(picking.move_line_ids.ids), original_line_ids)
        self.assertEqual(target_line.location_id, self.stock_location)
        self.assertEqual(target_line.lot_id, lot)
        self.assertEqual(target_line.package_id, package)
        self.assertFalse(target_line.owner_id)
        replacement_lines = picking.move_line_ids.filtered(
            lambda candidate: candidate.id != target_line.id
            and candidate.lot_id == lot
        )
        self.assertFalse(replacement_lines)

    def test_prefilled_lot_package_scan_uses_remaining_area_reservation(self):
        fixture = self._create_generated_picking("IMPLICIT", 4.0)

        self.start_tour(
            self._get_client_action_url(fixture[0].id),
            "freeform_delivery_barcode_remaining_quantity",
            login=self.barcode_user.login,
            timeout=180,
        )

        self._assert_original_lines_survive(*fixture)
        self.assertEqual(fixture[1].quantity, 4.0)
        self.assertTrue(fixture[1].picked)

    def test_explicit_gs1_quantity_wins_over_remaining_reservation(self):
        nomenclature = self.env.ref(
            "barcodes_gs1_nomenclature.default_gs1_nomenclature"
        )
        nomenclature.gs1_separator_fnc1 = "~"
        self.env.company.nomenclature_id = nomenclature
        fixture = self._create_generated_picking("EXPLICIT", 5.0)

        self.start_tour(
            self._get_client_action_url(fixture[0].id),
            "freeform_delivery_barcode_explicit_gs1_quantity",
            login=self.barcode_user.login,
            timeout=180,
        )

        self._assert_original_lines_survive(*fixture)
        self.assertEqual(fixture[1].quantity, 2.0)
        self.assertTrue(fixture[1].picked)
