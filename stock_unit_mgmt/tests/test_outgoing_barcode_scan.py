from odoo.addons.stock_barcode.tests.test_barcode_client_action import (
    TestBarcodeClientAction,
)
from odoo.tests import tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestOutgoingBarcodeScan(TestBarcodeClientAction):
    def setUp(self):
        super().setUp()
        self.barcode_user = new_test_user(
            self.env,
            login="stock_unit_barcode_test",
            groups=(
                "base.group_user,"
                "stock.group_stock_manager,"
                "stock.group_production_lot,"
                "stock.group_tracking_lot,"
                "uom.group_uom"
            ),
            company_id=self.env.company.id,
        )
        self.picking_type_out.write(
            {
                "use_existing_lots": True,
                "use_create_lots": False,
            }
        )

    def test_lot_scan_uses_full_reserved_area_quantity(self):
        category = self.env["uom.category"].create(
            {"name": "Barcode Roll Area"}
        )
        self.env["uom.uom"].create(
            {
                "name": "卷",
                "category_id": category.id,
                "uom_type": "reference",
                "rounding": 1.0,
            }
        )
        area_uom = self.env["uom.uom"].create(
            {
                "name": "平米",
                "category_id": category.id,
                "uom_type": "smaller",
                "factor": 2160.0,
                "rounding": 0.01,
            }
        )
        product = self.env["product.product"].create(
            {
                "name": "Barcode Area Roll",
                "is_storable": True,
                "tracking": "lot",
                "uom_id": area_uom.id,
                "uom_po_id": area_uom.id,
            }
        )
        lot = self.env["stock.lot"].create(
            {
                "name": "BARCODE-AREA-LOT",
                "product_id": product.id,
                "company_id": self.env.company.id,
            }
        )
        package = self.env["stock.quant.package"].create(
            {"name": "BARCODE-AREA-PACKAGE"}
        )
        reserved_quantity = 2162.16
        self.env["stock.quant"]._update_available_quantity(
            product,
            self.stock_location,
            reserved_quantity,
            lot_id=lot,
            package_id=package,
        )
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": self.picking_type_out.id,
                "location_id": self.stock_location.id,
                "location_dest_id": self.customer_location.id,
            }
        )
        move = self.env["stock.move"].create(
            {
                "name": product.display_name,
                "picking_id": picking.id,
                "product_id": product.id,
                "product_uom": area_uom.id,
                "product_uom_qty": reserved_quantity,
                "location_id": self.stock_location.id,
                "location_dest_id": self.customer_location.id,
            }
        )
        move._action_confirm(merge=False)
        line = self.env["stock.move.line"].create(
            {
                "picking_id": picking.id,
                "move_id": move.id,
                "company_id": self.env.company.id,
                "product_id": product.id,
                "product_uom_id": area_uom.id,
                "location_id": self.stock_location.id,
                "location_dest_id": self.customer_location.id,
                "lot_id": lot.id,
                "package_id": package.id,
                "quantity": reserved_quantity,
                "picked": False,
            }
        )
        move._recompute_state()

        self.assertEqual(line.qty_done, 0.0)
        self.assertEqual(line.quantity, reserved_quantity)
        self.start_tour(
            self._get_client_action_url(picking.id),
            "stock_unit_outgoing_lot_full_area_quantity",
            login=self.barcode_user.login,
            timeout=180,
        )
