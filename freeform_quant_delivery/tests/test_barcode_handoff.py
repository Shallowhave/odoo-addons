from odoo import Command
from odoo.tests import tagged

from .common import FreeformDeliveryCommon


@tagged("post_install", "-at_install")
class TestBarcodeAndReportHandoff(FreeformDeliveryCommon):
    def _wizard_with_selections(self, selections, *, products):
        wizard = self._create_freeform_wizard(products=products)
        wizard.action_next()
        quantities = {quant.id: quantity for quant, quantity in selections.items()}
        for line in wizard.quant_line_ids:
            if line.quant_id.id in quantities:
                line.selected_quantity = quantities[line.quant_id.id]
        return wizard

    def _create_area_uom(self):
        category = self.env["uom.category"].create(
            {"name": "Free-form Area Integration"}
        )
        return self.env["uom.uom"].create(
            {
                "name": "平米",
                "category_id": category.id,
                "uom_type": "reference",
                "rounding": 0.01,
            }
        )

    def _create_integration_picking(self):
        area_uom = self._create_area_uom()
        area_product = self._create_storable_product(
            name="Free-form Area Product",
            tracking="lot",
            uom=area_uom,
        )
        area_product.write(
            {
                "default_code": "FFD-AREA",
                "product_width": 2000.0,
                "product_thickness": 800,
                "weight_per_sqm": 1.5,
            }
        )
        area_lot = self.env["stock.lot"].create(
            {
                "name": "FFD-AREA-LOT",
                "product_id": area_product.id,
                "company_id": self.company.id,
            }
        )
        area_package = self.env["stock.quant.package"].create(
            {"name": "FFD-AREA-PACKAGE"}
        )
        area_quant = self._set_quant_quantity(
            area_product,
            self.source_child_location,
            10.0,
            lot=area_lot,
            package=area_package,
        )
        area_quant.write(
            {
                "lot_quantity": 20.0,
                "lot_unit_name": "custom",
                "lot_unit_name_custom": "Square Meter Bundle",
                "contract_no": "CONTRACT-AREA-007",
            }
        )
        area_quant.invalidate_recordset(
            [
                "lot_quantity",
                "lot_unit_name",
                "lot_unit_name_custom",
                "contract_no",
                "calculated_length_m",
            ]
        )

        wizard = self._wizard_with_selections(
            {area_quant: 4.0, self.quant: 1.25},
            products=area_product | self.product,
        )
        form_action = wizard.action_confirm_delivery()
        return wizard.picking_id, form_action, area_quant

    @staticmethod
    def _record_by_id(records):
        return {record["id"]: record for record in records}

    def _render_delivery_report_html(self, picking):
        report = self.env.ref("delivery_report.action_delivery_report")
        html, _report_type = report._render_qweb_html(
            report.report_name,
            [picking.id],
        )
        return html.decode() if isinstance(html, bytes) else html

    def test_exact_custom_identity_and_barcode_payload(self):
        self.env.user.write(
            {
                "groups_id": [
                    Command.link(self.env.ref("stock.group_production_lot").id),
                    Command.link(self.env.ref("stock.group_tracking_lot").id),
                    Command.link(self.env.ref("uom.group_uom").id),
                ]
            }
        )
        picking, form_action, area_quant = self._create_integration_picking()
        area_line = picking.move_line_ids.filtered(
            lambda line: line.product_id == area_quant.product_id
        ).ensure_one()
        untracked_line = (picking.move_line_ids - area_line).ensure_one()

        self.assertEqual(area_line.location_id, area_quant.location_id)
        self.assertEqual(area_line.lot_id, area_quant.lot_id)
        self.assertEqual(area_line.package_id, area_quant.package_id)
        self.assertEqual(area_line.owner_id, area_quant.owner_id)
        self.assertEqual(area_line.product_uom_id, area_quant.product_id.uom_id)
        self.assertEqual(area_line.quantity, 4.0)
        self.assertFalse(area_line.picked)
        self.assertEqual(area_line.lot_quantity, 8.0)
        self.assertEqual(area_line.lot_unit_name, "custom")
        self.assertEqual(area_line.lot_unit_name_custom, "Square Meter Bundle")
        self.assertEqual(area_line.contract_no, "CONTRACT-AREA-007")
        self.assertEqual(area_quant.calculated_length_m, 5.0)
        self.assertEqual(area_line.delivery_weight, 6.0)
        self.assertFalse(untracked_line.lot_id)
        self.assertEqual(untracked_line.quantity, 1.25)
        self.assertFalse(untracked_line.picked)

        self.assertEqual(form_action["type"], "ir.actions.act_window")
        self.assertEqual(form_action["res_model"], "stock.picking")
        self.assertEqual(form_action["res_id"], picking.id)
        self.assertEqual(
            form_action["views"],
            [(self.env.ref("stock.view_picking_form").id, "form")],
        )

        data = picking._get_stock_barcode_data()
        barcode_lines = self._record_by_id(
            data["records"]["stock.move.line"]
        )
        self.assertEqual(set(barcode_lines), set(picking.move_line_ids.ids))
        for line in picking.move_line_ids:
            record = barcode_lines[line.id]
            self.assertEqual(record["product_id"], line.product_id.id)
            self.assertEqual(record["lot_id"], line.lot_id.id or False)
            self.assertEqual(record["package_id"], line.package_id.id or False)
            self.assertEqual(record["location_id"], line.location_id.id)
            self.assertEqual(record["location_dest_id"], line.location_dest_id.id)
            self.assertEqual(record["product_uom_id"], line.product_uom_id.id)
            self.assertEqual(record["quantity"], line.quantity)
            self.assertFalse(record["picked"])

        lots = self._record_by_id(data["records"]["stock.lot"])
        packages = self._record_by_id(data["records"]["stock.quant.package"])
        self.assertIn(area_quant.lot_id.id, lots)
        self.assertEqual(lots[area_quant.lot_id.id]["name"], area_quant.lot_id.name)
        self.assertIn(area_quant.package_id.id, packages)
        self.assertEqual(
            packages[area_quant.package_id.id]["name"],
            area_quant.package_id.name,
        )

        barcode_action = picking.action_open_picking_client_action()
        self.assertEqual(barcode_action["type"], "ir.actions.client")
        self.assertEqual(barcode_action["tag"], "stock_barcode_client_action")
        self.assertEqual(barcode_action["context"]["active_id"], picking.id)

    def test_delivery_and_quality_report_helpers_use_exact_lines(self):
        picking, _form_action, area_quant = self._create_integration_picking()
        area_line = picking.move_line_ids.filtered(
            lambda line: line.product_id == area_quant.product_id
        ).ensure_one()

        delivery = picking._get_lot_serial_info()
        self.assertEqual(len(delivery["lot_info"]), 1)
        lot_info = delivery["lot_info"][0]
        self.assertEqual(lot_info["lot_name"], area_quant.lot_id.name)
        self.assertEqual(lot_info["package_name"], area_quant.package_id.name)
        self.assertEqual(lot_info["quantity"], 4.0)
        self.assertEqual(lot_info["length"], 2.0)
        self.assertEqual(delivery["total_quantity"], 4.0)
        self.assertEqual(delivery["total_length"], 2.0)

        lot_details = area_line._get_lot_details()
        self.assertEqual(lot_details["lot_name"], area_quant.lot_id.name)
        self.assertEqual(lot_details["quantity"], 4.0)
        self.assertEqual(lot_details["uom"], area_quant.product_id.uom_id.name)

        quality = picking._get_quality_info()
        quality_by_product = {row["product_code"]: row for row in quality}
        self.assertEqual(quality_by_product["FFD-AREA"]["lot_name"], area_quant.lot_id.name)
        self.assertEqual(quality_by_product["FFD-AREA"]["quantity"], 4.0)
        untracked = next(
            row for row in quality if row["product"] == self.product.name
        )
        self.assertEqual(untracked["lot_name"], "-")
        self.assertEqual(untracked["quantity"], 1.25)

    def test_delivery_report_hides_freeform_request_token(self):
        picking, _form_action, _area_quant = self._create_integration_picking()

        html = self._render_delivery_report_html(picking)

        self.assertNotIn(picking.freeform_request_token, html)
        self.assertEqual(picking._get_delivery_order_reference(), picking.name)

        picking.write(
            {
                "is_freeform_quant_delivery": False,
                "freeform_request_token": False,
                "origin": "SO-REPORT-001",
            }
        )
        self.assertEqual(
            picking._get_delivery_order_reference(),
            "SO-REPORT-001",
        )

    def test_delivery_report_uses_selected_customer_and_omits_conversion_column(self):
        picking, _form_action, _area_quant = self._create_integration_picking()
        self.customer.street = "Selected Customer Street"
        self.delivery_address.street = "Selected Delivery Street"

        html = self._render_delivery_report_html(picking)

        self.assertEqual(
            picking._get_delivery_report_customer(),
            self.customer.commercial_partner_id,
        )
        self.assertIn(self.customer.name, html)
        self.assertNotIn(self.delivery_address.name, html)
        self.assertIn(self.delivery_address.street, html)
        self.assertNotIn(self.customer.street, html)
        self.assertNotIn("转换", html)

    def test_delivery_report_formats_converted_length_to_two_decimals(self):
        picking, _form_action, area_quant = self._create_integration_picking()
        area_quant.product_id.product_tmpl_id.product_width = 920.0

        delivery = picking._get_lot_serial_info()

        self.assertAlmostEqual(delivery["lot_info"][0]["length"], 4.0 / 0.92)
        self.assertEqual(delivery["lot_info"][0]["length_display"], "4.35")
        self.assertIn("4.35", self._render_delivery_report_html(picking))

    def test_operation_editor_commands_keep_freeform_move_demand_in_sync(self):
        picking, _form_action, area_quant = self._create_integration_picking()
        move = picking.move_ids.filtered(
            lambda candidate: candidate.product_id == area_quant.product_id
        ).ensure_one()
        extra_lot = self.env["stock.lot"].create(
            {
                "name": "FFD-AREA-LOT-EXTRA",
                "product_id": area_quant.product_id.id,
                "company_id": self.company.id,
            }
        )
        extra_package = self.env["stock.quant.package"].create(
            {"name": "FFD-AREA-PACKAGE-EXTRA"}
        )
        extra_quant = self._set_quant_quantity(
            area_quant.product_id,
            self.source_child_location,
            2.0,
            lot=extra_lot,
            package=extra_package,
        )
        extra_line_values = move._prepare_move_line_vals(quantity=2.0)
        extra_line_values.update(
            {
                "quant_id": extra_quant.id,
                "lot_id": extra_lot.id,
                "package_id": extra_package.id,
                "location_id": self.source_child_location.id,
                "location_dest_id": picking.location_dest_id.id,
                "quantity": 2.0,
                "picked": False,
            }
        )

        move.write(
            {
                "move_line_ids": [Command.create(extra_line_values)],
                "quantity": 6.0,
            }
        )
        extra_line = move.move_line_ids.filtered(
            lambda line: line.lot_id == extra_lot
        ).ensure_one()
        self.assertEqual(move.product_uom_qty, 6.0)
        self.assertEqual(move.state, "assigned")

        move.write(
            {
                "move_line_ids": [
                    Command.update(extra_line.id, {"quantity": 1.5})
                ],
                "quantity": 5.5,
            }
        )
        self.assertEqual(move.product_uom_qty, 5.5)
        self.assertEqual(move.state, "assigned")

        move.write(
            {
                "move_line_ids": [Command.delete(extra_line.id)],
                "quantity": 4.0,
            }
        )
        self.assertEqual(move.product_uom_qty, 4.0)
        self.assertEqual(move.state, "assigned")

    def test_direct_scan_quantity_does_not_redefine_freeform_demand(self):
        picking, _form_action, area_quant = self._create_integration_picking()
        area_line = picking.move_line_ids.filtered(
            lambda line: line.product_id == area_quant.product_id
        ).ensure_one()
        move = area_line.move_id

        area_line.write({"quantity": 2.0, "picked": True})

        self.assertEqual(move.product_uom_qty, 4.0)
        self.assertEqual(move.quantity, 2.0)
        self.assertEqual(move.state, "partially_available")

    def test_reports_group_multiple_move_lines_for_same_lot(self):
        picking, _form_action, area_quant = self._create_integration_picking()
        area_line = picking.move_line_ids.filtered(
            lambda line: line.product_id == area_quant.product_id
        ).ensure_one()
        self.env["stock.move.line"].create(
            {
                "picking_id": picking.id,
                "move_id": area_line.move_id.id,
                "company_id": area_line.company_id.id,
                "product_id": area_line.product_id.id,
                "product_uom_id": area_line.product_uom_id.id,
                "lot_id": area_line.lot_id.id,
                "package_id": area_line.package_id.id,
                "location_id": area_line.location_id.id,
                "location_dest_id": area_line.location_dest_id.id,
                "quantity": 2.0,
            }
        )

        delivery = picking._get_lot_serial_info()
        self.assertEqual(len(delivery["lot_info"]), 1)
        self.assertEqual(delivery["lot_info"][0]["quantity"], 6.0)
        self.assertEqual(delivery["lot_info"][0]["length"], 3.0)
        self.assertEqual(delivery["total_quantity"], 6.0)
        self.assertEqual(delivery["total_length"], 3.0)

        quality = picking._get_quality_info()
        area_rows = [
            row for row in quality if row["product_code"] == "FFD-AREA"
        ]
        self.assertEqual(len(area_rows), 1)
        self.assertEqual(area_rows[0]["quantity"], 6.0)
