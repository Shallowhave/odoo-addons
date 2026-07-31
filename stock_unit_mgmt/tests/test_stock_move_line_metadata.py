from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStockMoveLineMetadata(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.source_location = cls.env["stock.location"].create(
            {
                "name": "Move-line Metadata Source",
                "usage": "internal",
                "company_id": cls.company.id,
            }
        )
        cls.destination_location = cls.env["stock.location"].create(
            {
                "name": "Move-line Metadata Destination",
                "usage": "internal",
                "company_id": cls.company.id,
            }
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Move-line Metadata Product",
                "is_storable": True,
                "tracking": "lot",
                "company_id": cls.company.id,
                "enable_custom_units": True,
                "default_unit_config": "custom",
                "quick_unit_name": "Default Layer",
            }
        )
        cls.lot = cls.env["stock.lot"].create(
            {
                "name": "MOVE-LINE-METADATA-LOT",
                "product_id": cls.product.id,
                "company_id": cls.company.id,
            }
        )
        cls.move = cls.env["stock.move"].create(
            {
                "name": cls.product.display_name,
                "product_id": cls.product.id,
                "product_uom_qty": 1.0,
                "product_uom": cls.product.uom_id.id,
                "location_id": cls.source_location.id,
                "location_dest_id": cls.destination_location.id,
                "company_id": cls.company.id,
            }
        )

    def _base_move_line_values(self):
        return {
            "move_id": self.move.id,
            "product_id": self.product.id,
            "product_uom_id": self.product.uom_id.id,
            "location_id": self.source_location.id,
            "location_dest_id": self.destination_location.id,
            "company_id": self.company.id,
            "lot_id": self.lot.id,
            "quantity": 0.0,
        }

    def test_explicit_false_and_zero_are_not_defaulted(self):
        MoveLine = self.env["stock.move.line"]
        no_unit_values = {
            **self._base_move_line_values(),
            "lot_unit_name": False,
            "lot_quantity": 0.0,
        }
        custom_values = {
            **self._base_move_line_values(),
            "lot_unit_name": "custom",
            "lot_unit_name_custom": "",
            "lot_quantity": 0.0,
        }

        MoveLine._apply_default_lot_unit_values(
            [no_unit_values, custom_values]
        )

        self.assertFalse(no_unit_values["lot_unit_name"])
        self.assertEqual(no_unit_values["lot_quantity"], 0.0)
        self.assertEqual(custom_values["lot_unit_name"], "custom")
        self.assertEqual(custom_values["lot_unit_name_custom"], "")
        self.assertEqual(custom_values["lot_quantity"], 0.0)

    def test_absent_values_still_receive_defaults(self):
        MoveLine = self.env["stock.move.line"]
        values = self._base_move_line_values()

        MoveLine._apply_default_lot_unit_values([values])

        self.assertEqual(values["lot_unit_name"], "custom")
        self.assertEqual(values["lot_unit_name_custom"], "Default Layer")
        self.assertEqual(values["lot_quantity"], 1.0)

    def test_purchase_receipt_empty_unit_uses_product_default_without_weight(self):
        self.product.product_tmpl_id.write(
            {
                "default_unit_config": "kg",
                "quick_unit_name": False,
            }
        )
        vendor = self.env["res.partner"].create({"name": "Unit Default Vendor"})
        purchase = self.env["purchase.order"].create(
            {
                "partner_id": vendor.id,
                "company_id": self.company.id,
            }
        )
        purchase_line = self.env["purchase.order.line"].create(
            {
                "order_id": purchase.id,
                "name": self.product.display_name,
                "product_id": self.product.id,
                "product_qty": 1.0,
                "product_uom": self.product.uom_id.id,
                "price_unit": 1.0,
                "date_planned": fields.Datetime.now(),
            }
        )
        incoming_type = self.env["stock.picking.type"].search(
            [
                ("code", "=", "incoming"),
                ("company_id", "=", self.company.id),
            ],
            limit=1,
        )
        self.assertTrue(incoming_type)
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": incoming_type.id,
                "location_id": incoming_type.default_location_src_id.id,
                "location_dest_id": self.destination_location.id,
                "company_id": self.company.id,
            }
        )
        purchase_move = self.env["stock.move"].create(
            {
                "name": self.product.display_name,
                "product_id": self.product.id,
                "product_uom_qty": 1.0,
                "product_uom": self.product.uom_id.id,
                "location_id": picking.location_id.id,
                "location_dest_id": picking.location_dest_id.id,
                "company_id": self.company.id,
                "picking_id": picking.id,
                "purchase_line_id": purchase_line.id,
            }
        )
        values = {
            "move_id": purchase_move.id,
            "product_id": self.product.id,
            "product_uom_id": self.product.uom_id.id,
            "location_id": picking.location_id.id,
            "location_dest_id": picking.location_dest_id.id,
            "company_id": self.company.id,
            "lot_unit_name": False,
            "lot_quantity": 0.0,
            "quantity": 1.0,
        }

        move_line = self.env["stock.move.line"].create(values)

        self.assertEqual(move_line.lot_unit_name, "kg")
        self.assertEqual(move_line.lot_quantity, 0.0)

    def test_explicit_false_contract_skips_fallback(self):
        package = self.env["stock.quant.package"].create(
            {"name": "Explicit Contract Package"}
        )
        quant = self.env["stock.quant"].create(
            {
                "product_id": self.product.id,
                "location_id": self.source_location.id,
                "lot_id": self.lot.id,
                "package_id": package.id,
                "quantity": 1.0,
                "contract_no": "SHOULD-NOT-LEAK",
            }
        )
        self.assertEqual(quant.contract_no, "SHOULD-NOT-LEAK")

        line = self.env["stock.move.line"].create(
            {
                **self._base_move_line_values(),
                "package_id": package.id,
                "contract_no": False,
            }
        )

        self.assertFalse(line.contract_no)

    def test_contract_fallback_uses_complete_quant_identity(self):
        selected_package = self.env["stock.quant.package"].create(
            {"name": "Selected Contract Package"}
        )
        other_package = self.env["stock.quant.package"].create(
            {"name": "Other Contract Package"}
        )
        other_owner = self.env["res.partner"].create(
            {"name": "Other Contract Owner"}
        )
        selected_quant = self.env["stock.quant"].create(
            {
                "product_id": self.product.id,
                "location_id": self.source_location.id,
                "lot_id": self.lot.id,
                "package_id": selected_package.id,
                "quantity": 1.0,
                "contract_no": "SELECTED-CONTRACT",
            }
        )
        other_quant = self.env["stock.quant"].create(
            {
                "product_id": self.product.id,
                "location_id": self.source_location.id,
                "lot_id": self.lot.id,
                "package_id": other_package.id,
                "owner_id": other_owner.id,
                "quantity": 1.0,
                "contract_no": "OTHER-CONTRACT",
            }
        )
        self.assertGreater(other_quant.id, selected_quant.id)

        line = self.env["stock.move.line"].create(
            {
                **self._base_move_line_values(),
                "package_id": selected_package.id,
                "owner_id": False,
            }
        )

        self.assertEqual(line.contract_no, "SELECTED-CONTRACT")

    def test_write_contract_fallback_uses_post_write_complete_identity(self):
        selected_package = self.env["stock.quant.package"].create(
            {"name": "Write Selected Contract Package"}
        )
        other_package = self.env["stock.quant.package"].create(
            {"name": "Write Other Contract Package"}
        )
        other_owner = self.env["res.partner"].create(
            {"name": "Write Other Contract Owner"}
        )
        selected_quant = self.env["stock.quant"].create(
            {
                "product_id": self.product.id,
                "location_id": self.source_location.id,
                "lot_id": self.lot.id,
                "package_id": selected_package.id,
                "quantity": 1.0,
                "contract_no": "WRITE-SELECTED-CONTRACT",
            }
        )
        other_quant = self.env["stock.quant"].create(
            {
                "product_id": self.product.id,
                "location_id": self.source_location.id,
                "lot_id": self.lot.id,
                "package_id": other_package.id,
                "owner_id": other_owner.id,
                "quantity": 1.0,
                "contract_no": "WRITE-OTHER-CONTRACT",
            }
        )
        self.assertGreater(other_quant.id, selected_quant.id)
        line = self.env["stock.move.line"].create(
            {
                **self._base_move_line_values(),
                "package_id": False,
                "owner_id": False,
                "contract_no": False,
            }
        )

        line.write({"package_id": selected_package.id, "owner_id": False})

        self.assertEqual(line.package_id, selected_package)
        self.assertFalse(line.owner_id)
        self.assertEqual(line.contract_no, "WRITE-SELECTED-CONTRACT")

    def test_multi_record_write_resolves_contract_per_complete_identity(self):
        first_package = self.env["stock.quant.package"].create(
            {"name": "Multi-write First Contract Package"}
        )
        second_package = self.env["stock.quant.package"].create(
            {"name": "Multi-write Second Contract Package"}
        )
        for package, contract_no in (
            (first_package, "MULTI-FIRST-CONTRACT"),
            (second_package, "MULTI-SECOND-CONTRACT"),
        ):
            self.env["stock.quant"].create(
                {
                    "product_id": self.product.id,
                    "location_id": self.source_location.id,
                    "lot_id": self.lot.id,
                    "package_id": package.id,
                    "quantity": 1.0,
                    "contract_no": contract_no,
                }
            )
        lines = self.env["stock.move.line"].create(
            [
                {
                    **self._base_move_line_values(),
                    "package_id": package.id,
                    "contract_no": False,
                }
                for package in (first_package, second_package)
            ]
        )

        lines.write({"owner_id": False})

        self.assertEqual(
            lines.filtered(lambda line: line.package_id == first_package).contract_no,
            "MULTI-FIRST-CONTRACT",
        )
        self.assertEqual(
            lines.filtered(lambda line: line.package_id == second_package).contract_no,
            "MULTI-SECOND-CONTRACT",
        )

    def test_write_explicit_false_contract_skips_fallback(self):
        package = self.env["stock.quant.package"].create(
            {"name": "Write Explicit Contract Package"}
        )
        self.env["stock.quant"].create(
            {
                "product_id": self.product.id,
                "location_id": self.source_location.id,
                "lot_id": self.lot.id,
                "package_id": package.id,
                "quantity": 1.0,
                "contract_no": "WRITE-SHOULD-NOT-LEAK",
            }
        )
        line = self.env["stock.move.line"].create(
            {
                **self._base_move_line_values(),
                "package_id": package.id,
                "contract_no": "ORIGINAL-CONTRACT",
            }
        )

        line.write({"contract_no": False})

        self.assertFalse(line.contract_no)
