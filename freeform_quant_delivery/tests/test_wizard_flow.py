from lxml import etree

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, TransactionCase, tagged
from odoo.tools import float_compare

from .common import FreeformDeliveryCommon


@tagged("post_install", "-at_install")
class TestFreeformDeliveryWizardFlow(FreeformDeliveryCommon):
    def _new_wizard(self, products=None):
        return self._create_freeform_wizard(products=products or [])

    def test_step_one_has_no_quantity_and_step_two_does_not_reserve(self):
        self.assertNotIn(
            "quantity", self.env["freeform.delivery.product.line"]._fields
        )
        wizard = self._new_wizard()
        wizard.product_line_ids = [Command.create({"product_id": self.product.id})]
        before = self.quant.reserved_quantity
        wizard.action_next()
        self.assertEqual(wizard.state, "select_quants")
        self.assertEqual(wizard.quant_line_ids.quant_id, self.quant)
        self.assertEqual(self.quant.reserved_quantity, before)
        self.assertFalse(
            self.env["stock.move.line"].search(
                [
                    ("product_id", "=", self.product.id),
                    ("location_id", "=", self.source_location.id),
                ]
            )
        )

    def test_regular_stock_user_cannot_create_wizard(self):
        with self.assertRaises(AccessError):
            self._create_freeform_wizard(
                products=[self.product], user=self.stock_user
            )

    def test_regular_stock_user_cannot_advance(self):
        wizard = self._new_wizard([self.product]).with_user(self.stock_user)
        with self.assertRaises(AccessError):
            wizard.action_next()

    def test_transient_model_acls_are_manager_only(self):
        for model_name in (
            "freeform.delivery.wizard",
            "freeform.delivery.product.line",
            "freeform.delivery.quant.line",
        ):
            with self.subTest(model=model_name):
                manager_model = self.env[model_name].with_user(self.stock_manager)
                regular_user_model = self.env[model_name].with_user(self.stock_user)
                for operation in ("create", "read", "write", "unlink"):
                    with self.subTest(model=model_name, operation=operation):
                        manager_model.check_access(operation)
                        self.assertTrue(manager_model.has_access(operation))
                        with self.assertRaises(AccessError):
                            regular_user_model.check_access(operation)

    def test_wizard_action_and_views_are_manager_only_odoo_18_architectures(self):
        manager_group = self.env.ref("stock.group_stock_manager")
        action = self.env.ref(
            "freeform_quant_delivery.action_freeform_delivery_wizard"
        )
        self.assertEqual(action.groups_id, manager_group)
        self.assertEqual(action.target, "current")
        self.assertEqual(action.view_mode, "form")

        view_specs = (
            ("freeform_delivery_wizard_form", "form"),
            ("freeform_delivery_quant_line_list", "list"),
            ("freeform_delivery_quant_line_search", "search"),
        )
        for xml_id, root_tag in view_specs:
            with self.subTest(view=xml_id):
                view = self.env.ref(f"freeform_quant_delivery.{xml_id}")
                self.assertEqual(view.groups_id, manager_group)
                arch = etree.fromstring(view.arch_db)
                self.assertEqual(arch.tag, root_tag)
                self.assertFalse(arch.xpath("//*[@attrs]"))
                self.assertFalse(arch.xpath("//tree"))

        form_arch = etree.fromstring(
            self.env.ref(
                "freeform_quant_delivery.freeform_delivery_wizard_form"
            ).arch_db
        )
        self.assertTrue(form_arch.xpath("//field[@name='company_id'][@invisible='1']"))
        self.assertTrue(
            form_arch.xpath(
                "//field[@name='company_id'][@groups='base.group_multi_company']"
            )
        )
        self.assertTrue(
            form_arch.xpath("//field[@name='request_token'][@invisible='1']")
        )

    def test_manager_menu_contract(self):
        menu = self.env.ref("freeform_quant_delivery.menu_freeform_delivery")
        action = self.env.ref(
            "freeform_quant_delivery.action_freeform_delivery_wizard"
        )
        self.assertEqual(menu.parent_id, self.env.ref("stock.menu_stock_warehouse_mgmt"))
        self.assertEqual(menu.action, action)
        self.assertEqual(menu.groups_id, self.env.ref("stock.group_stock_manager"))

    def test_quant_line_list_and_search_contract(self):
        list_view = self.env.ref(
            "freeform_quant_delivery.freeform_delivery_quant_line_list"
        )
        list_arch = etree.fromstring(list_view.arch_db)
        self.assertEqual(list_arch.get("editable"), "bottom")
        self.assertEqual(list_arch.get("create"), "0")
        self.assertEqual(list_arch.get("delete"), "0")
        self.assertEqual(
            [field.get("name") for field in list_arch.xpath("./field")],
            [
                "product_id",
                "location_id",
                "lot_id",
                "package_id",
                "in_date",
                "on_hand_quantity",
                "reserved_quantity",
                "available_quantity",
                "selected_quantity",
                "product_uom_id",
                "lot_quantity",
                "lot_unit_name",
                "lot_unit_name_custom",
                "contract_no",
                "calculated_length_m",
                "actual_length_m",
                "actual_area_sqm",
                "is_roll_product",
                "product_width",
                "product_thickness",
                "product_length",
                "weight_per_sqm",
            ],
        )

        search_view = self.env.ref(
            "freeform_quant_delivery.freeform_delivery_quant_line_search"
        )
        search_arch = etree.fromstring(search_view.arch_db)
        filters = {
            node.get("name"): node for node in search_arch.xpath("//filter")
        }
        self.assertEqual(
            filters["selected"].get("domain"),
            "[('selected_quantity', '>', 0)]",
        )
        self.assertEqual(
            filters["with_lot"].get("domain"),
            "[('lot_id', '!=', False)]",
        )
        self.assertEqual(
            filters["with_package"].get("domain"),
            "[('package_id', '!=', False)]",
        )
        for field_name in ("product_id", "lot_id", "location_id", "package_id"):
            self.assertTrue(search_arch.xpath(f"./field[@name='{field_name}']"))
            group_filter = filters[
                f"group_by_{field_name.removesuffix('_id')}"
            ]
            self.assertEqual(
                group_filter.get("context"),
                f"{{'group_by': '{field_name}'}}",
            )

    def test_action_open_quant_lines_requires_step_two_and_manager(self):
        wizard = self._new_wizard([self.product])
        with self.assertRaises(UserError):
            wizard.action_open_quant_lines()

        wizard.action_next()
        with self.assertRaises(AccessError):
            wizard.with_user(self.stock_user).action_open_quant_lines()

        action = wizard.action_open_quant_lines()
        list_view = self.env.ref(
            "freeform_quant_delivery.freeform_delivery_quant_line_list"
        )
        search_view = self.env.ref(
            "freeform_quant_delivery.freeform_delivery_quant_line_search"
        )
        self.assertEqual(action["res_model"], "freeform.delivery.quant.line")
        self.assertEqual(action["view_mode"], "list")
        self.assertEqual(action["views"], [(list_view.id, "list")])
        self.assertEqual(action["search_view_id"], search_view.id)
        self.assertEqual(action["domain"], [("wizard_id", "=", wizard.id)])
        self.assertEqual(action["context"], {"default_wizard_id": wizard.id})
        self.assertEqual(action["target"], "current")

    def test_confirmation_requires_step_two_without_stock_side_effects(self):
        wizard = self._new_wizard([self.product])
        picking_count = self.env["stock.picking"].search_count([])
        move_count = self.env["stock.move"].search_count([])
        reserved_quantity = self.quant.reserved_quantity

        with self.assertRaisesRegex(UserError, "Advance to stock selection first"):
            wizard.action_confirm_delivery()

        self.assertEqual(self.env["stock.picking"].search_count([]), picking_count)
        self.assertEqual(self.env["stock.move"].search_count([]), move_count)
        self.assertEqual(self.quant.reserved_quantity, reserved_quantity)
        self.assertFalse(wizard.picking_id)

    def test_regular_user_cannot_confirm_after_step_two_without_stock_side_effects(self):
        wizard = self._new_wizard([self.product])
        wizard.action_next()
        wizard.quant_line_ids.filtered(
            lambda line: line.quant_id == self.quant
        ).selected_quantity = 1.0
        picking_count = self.env["stock.picking"].search_count([])
        move_count = self.env["stock.move"].search_count([])
        reserved_quantity = self.quant.reserved_quantity

        with self.assertRaises(AccessError):
            wizard.with_user(self.stock_user).action_confirm_delivery()

        self.assertEqual(self.env["stock.picking"].search_count([]), picking_count)
        self.assertEqual(self.env["stock.move"].search_count([]), move_count)
        self.assertEqual(self.quant.reserved_quantity, reserved_quantity)
        self.assertFalse(wizard.picking_id)

    def test_picking_form_metadata_inheritance_is_manager_only(self):
        view = self.env.ref(
            "freeform_quant_delivery.stock_picking_form_freeform_metadata"
        )
        self.assertEqual(view.inherit_id, self.env.ref("stock.view_picking_form"))
        self.assertFalse(view.groups_id)
        arch = etree.fromstring(view.arch_db)
        fields_by_name = {
            field.get("name"): field
            for field in arch.xpath("//field")
            if field.get("name")
        }
        for field_name in (
            "is_freeform_quant_delivery",
            "freeform_customer_id",
            "freeform_request_token",
        ):
            self.assertEqual(fields_by_name[field_name].get("readonly"), "1")
            self.assertEqual(
                fields_by_name[field_name].get("groups"),
                "stock.group_stock_manager",
            )

    def test_form_defaults_step_one_company_and_request_token(self):
        wizard_form = Form(self.env["freeform.delivery.wizard"])
        self.assertEqual(wizard_form.state, "select_products")
        self.assertEqual(wizard_form.company_id, self.company)
        self.assertTrue(wizard_form.request_token)

    def test_stock_manager_creates_and_navigates_wizard_as_manager(self):
        self.assertEqual(self.stock_manager.company_id, self.company)
        self.assertEqual(self.stock_manager.company_ids, self.company)
        wizard = self._create_freeform_wizard(
            products=[self.product], user=self.stock_manager
        )
        self.assertEqual(wizard.env.user, self.stock_manager)
        self.assertEqual(wizard.product_line_ids.env.user, self.stock_manager)

        next_action = wizard.action_next()
        self.assertEqual(wizard.state, "select_quants")
        self.assertEqual(wizard.quant_line_ids.quant_id, self.quant)
        self.assertEqual(next_action["res_id"], wizard.id)

        back_action = wizard.action_back()
        self.assertEqual(wizard.state, "select_products")
        self.assertFalse(wizard.quant_line_ids)
        self.assertEqual(back_action["res_id"], wizard.id)

    def test_eligible_products_use_current_source_scope(self):
        outside_location = self.env["stock.location"].create(
            {
                "name": "Outside Free-form Source",
                "usage": "internal",
                "company_id": self.company.id,
            }
        )
        outside_product = self._create_storable_product(name="Outside Source Product")
        self._set_quant_quantity(outside_product, outside_location, 2.0)

        wizard = self._new_wizard()
        self.assertIn(self.product, wizard.eligible_product_ids)
        self.assertIn(self.tracked_product, wizard.eligible_product_ids)
        self.assertNotIn(outside_product, wizard.eligible_product_ids)

    def test_empty_product_selection_is_rejected(self):
        with self.assertRaises(UserError):
            self._new_wizard().action_next()

    def test_action_back_discards_generated_quant_lines(self):
        wizard = self._new_wizard([self.product])
        wizard.action_next()
        action = wizard.action_back()
        self.assertEqual(wizard.state, "select_products")
        self.assertFalse(wizard.quant_line_ids)
        self.assertEqual(action["res_id"], wizard.id)

    def test_quant_line_snapshots_all_source_values_and_remains_unchanged(self):
        product_values = {
            "product_width": 1250,
            "product_thickness": 42,
            "product_length": 88.5,
            "weight_per_sqm": 0.75,
        }
        self.tracked_product.write(product_values)
        initial_quant_values = {
            "lot_quantity": 3.5,
            "lot_unit_name": "custom",
            "lot_unit_name_custom": "Test Coil",
            "contract_no": "FFD-CONTRACT-001",
            "calculated_length_m": 70.25,
            "actual_length_m": 619.5,
            "actual_area_sqm": 774.38,
            "is_roll_product": True,
        }
        computed_snapshot_fields = [
            self.tracked_quant._fields[field_name]
            for field_name in (
                "lot_quantity",
                "lot_unit_name",
                "lot_unit_name_custom",
                "calculated_length_m",
                "actual_length_m",
                "actual_area_sqm",
                "is_roll_product",
            )
        ]
        with self.env.protecting(computed_snapshot_fields, self.tracked_quant):
            self.tracked_quant.write(initial_quant_values)

        wizard = self._new_wizard([self.tracked_product])
        wizard.action_next()
        line = wizard.quant_line_ids
        expected = {
            "quant_id": self.tracked_quant.id,
            "product_id": self.tracked_product.id,
            "location_id": self.source_child_location.id,
            "lot_id": self.lot.id,
            "package_id": self.package.id,
            "owner_id": False,
            "company_id": self.company.id,
            "product_uom_id": self.tracked_product.uom_id.id,
            "in_date": self.tracked_quant.in_date,
            "on_hand_quantity": self.tracked_quant.quantity,
            "reserved_quantity": self.tracked_quant.reserved_quantity,
            "available_quantity": self.tracked_quant.available_quantity,
            "lot_quantity": self.tracked_quant.lot_quantity,
            "lot_unit_name": self.tracked_quant.lot_unit_name,
            "lot_unit_name_custom": self.tracked_quant.lot_unit_name_custom,
            "contract_no": self.tracked_quant.contract_no,
            "calculated_length_m": self.tracked_quant.calculated_length_m,
            "actual_length_m": self.tracked_quant.actual_length_m,
            "actual_area_sqm": self.tracked_quant.actual_area_sqm,
            "is_roll_product": self.tracked_quant.is_roll_product,
            **product_values,
        }
        self.assertRecordValues(line, [expected])

        replacement_product = self._create_storable_product(
            name="Snapshot Replacement Product", tracking="lot"
        )
        replacement_product.write(
            {
                "product_width": 333,
                "product_thickness": 21,
                "product_length": 44.0,
                "weight_per_sqm": 1.5,
            }
        )
        self.tracked_product.write(
            {
                "product_width": 999,
                "product_thickness": 11,
                "product_length": 12.0,
                "weight_per_sqm": 2.5,
            }
        )
        replacement_package = self.env["stock.quant.package"].create(
            {"name": "FFD-PACKAGE-REPLACEMENT"}
        )
        replacement_owner = self.env["res.partner"].create(
            {"name": "FFD Replacement Owner"}
        )
        replacement_location = self.env["stock.location"].create(
            {
                "name": "FFD Replacement Location",
                "usage": "internal",
                "location_id": self.source_location.id,
                "company_id": self.company.id,
            }
        )
        changed_in_date = fields.Datetime.subtract(
            self.tracked_quant.in_date, days=1
        )
        changed_quant_values = {
            "product_id": replacement_product.id,
            "location_id": replacement_location.id,
            "lot_id": False,
            "package_id": replacement_package.id,
            "owner_id": replacement_owner.id,
            "in_date": changed_in_date,
            "quantity": 5.0,
            "reserved_quantity": 2.0,
            "lot_quantity": 1.25,
            "lot_unit_name": "roll",
            "lot_unit_name_custom": False,
            "contract_no": "FFD-CONTRACT-CHANGED",
            "calculated_length_m": 4.0,
            "actual_length_m": 5.0,
            "actual_area_sqm": 6.0,
            "is_roll_product": False,
        }
        with self.env.protecting(computed_snapshot_fields, self.tracked_quant):
            self.tracked_quant.write(changed_quant_values)
        self.assertRecordValues(
            self.tracked_quant,
            [
                {
                    "product_id": replacement_product.id,
                    "location_id": replacement_location.id,
                    "lot_id": False,
                    "package_id": replacement_package.id,
                    "owner_id": replacement_owner.id,
                    "in_date": changed_in_date,
                    "quantity": 5.0,
                    "reserved_quantity": 2.0,
                    "lot_quantity": 1.25,
                    "lot_unit_name": "roll",
                    "lot_unit_name_custom": False,
                    "contract_no": "FFD-CONTRACT-CHANGED",
                    "calculated_length_m": 4.0,
                    "actual_length_m": 5.0,
                    "actual_area_sqm": 6.0,
                    "is_roll_product": False,
                }
            ],
        )
        self.assertEqual(self.tracked_quant.available_quantity, 3.0)
        self.assertRecordValues(line, [expected])

    def test_invalid_delivery_address_is_rejected(self):
        invalid_address = self.env["res.partner"].create(
            {"name": "Invoice Address", "parent_id": self.customer.id, "type": "invoice"}
        )
        wizard = self._new_wizard([self.product])
        wizard.delivery_address_id = invalid_address
        with self.assertRaises(UserError):
            wizard.action_next()

        other_customer = self.env["res.partner"].create({"name": "Other Customer"})
        other_delivery = self.env["res.partner"].create(
            {"name": "Other Delivery", "parent_id": other_customer.id, "type": "delivery"}
        )
        wizard.delivery_address_id = other_delivery
        with self.assertRaises(UserError):
            wizard.action_next()

    def test_wizard_company_must_be_current_company(self):
        other_company = self.env["res.company"].create(
            {"name": "Other Wizard Company"}
        )
        wizard = self.env["freeform.delivery.wizard"].new(
            {
                "company_id": other_company.id,
                "picking_type_id": self.picking_type.id,
                "customer_id": self.customer.id,
                "delivery_address_id": self.delivery_address.id,
            }
        )
        with self.assertRaises(UserError):
            wizard._validate_header()

    def test_picking_type_company_must_match_wizard_company(self):
        other_company = self.env["res.company"].create(
            {"name": "Other Picking Type Company"}
        )
        other_source = self.env["stock.location"].create(
            {
                "name": "Other Picking Source",
                "usage": "internal",
                "company_id": other_company.id,
            }
        )
        other_picking_type = self.env["stock.picking.type"].with_company(
            other_company
        ).create(
            {
                "name": "Other Company Deliveries",
                "sequence_code": "OCD",
                "code": "outgoing",
                "company_id": other_company.id,
                "default_location_src_id": other_source.id,
                "default_location_dest_id": self.destination_location.id,
            }
        )
        wizard = self.env["freeform.delivery.wizard"].new(
            {
                "company_id": self.company.id,
                "picking_type_id": other_picking_type.id,
                "customer_id": self.customer.id,
                "delivery_address_id": self.delivery_address.id,
            }
        )
        with self.assertRaises(UserError):
            wizard._validate_header()

    def test_non_internal_source_is_rejected(self):
        supplier_source = self.env["stock.location"].create(
            {
                "name": "Free-form Supplier Source",
                "usage": "supplier",
                "company_id": False,
            }
        )
        picking_type = self.env["stock.picking.type"].create(
            {
                "name": "Invalid Source Deliveries",
                "sequence_code": "ISD",
                "code": "outgoing",
                "company_id": self.company.id,
                "default_location_src_id": supplier_source.id,
                "default_location_dest_id": self.destination_location.id,
            }
        )
        wizard = self._new_wizard([self.product])
        wizard.picking_type_id = picking_type
        with self.assertRaises(UserError):
            wizard.action_next()

    def test_non_customer_destination_is_rejected(self):
        picking_type = self.env["stock.picking.type"].create(
            {
                "name": "Invalid Destination Deliveries",
                "sequence_code": "IDD",
                "code": "outgoing",
                "company_id": self.company.id,
                "default_location_src_id": self.source_location.id,
                "default_location_dest_id": self.source_child_location.id,
            }
        )
        wizard = self._new_wizard([self.product])
        wizard.picking_type_id = picking_type
        with self.assertRaises(UserError):
            wizard.action_next()

    def test_non_outgoing_type_is_rejected(self):
        internal_type = self.env["stock.picking.type"].create(
            {
                "name": "Free-form Internal",
                "sequence_code": "FFI",
                "code": "internal",
                "company_id": self.company.id,
                "default_location_src_id": self.source_location.id,
                "default_location_dest_id": self.source_child_location.id,
            }
        )
        wizard = self._new_wizard([self.product])
        wizard.picking_type_id = internal_type
        with self.assertRaises(UserError):
            wizard.action_next()

    def test_non_storable_product_is_rejected(self):
        service = self.env["product.product"].create(
            {"name": "Free-form Service", "is_storable": False}
        )
        wizard = self._new_wizard([service])
        with self.assertRaises(UserError):
            wizard.action_next()

    def test_duplicate_product_is_rejected(self):
        wizard = self.env["freeform.delivery.wizard"].new(
            {
                "company_id": self.company.id,
                "picking_type_id": self.picking_type.id,
                "customer_id": self.customer.id,
                "delivery_address_id": self.delivery_address.id,
            }
        )
        wizard.update(
            {
                "product_line_ids": [
                    Command.create({"product_id": self.product.id}),
                    Command.create({"product_id": self.product.id}),
                ]
            }
        )
        with self.assertRaises(UserError):
            wizard._validate_products()

    def test_owner_quant_is_excluded(self):
        owner = self.env["res.partner"].create({"name": "Third-party Owner"})
        owner_product = self._create_storable_product(name="Owner Stock Product")
        self.env["stock.quant"]._update_available_quantity(
            owner_product,
            self.source_location,
            5.0,
            owner_id=owner,
        )
        wizard = self._new_wizard([owner_product])
        with self.assertRaises(UserError):
            wizard.action_next()
        self.assertNotIn(owner_product, wizard.eligible_product_ids)

    def test_current_company_isolation(self):
        other_company = self.env["res.company"].create({"name": "Other FFD Company"})
        other_location = self.env["stock.location"].create(
            {
                "name": "Other Company Stock",
                "usage": "internal",
                "company_id": other_company.id,
            }
        )
        other_product = self.env["product.product"].with_company(other_company).create(
            {
                "name": "Other Company Product",
                "is_storable": True,
                "company_id": other_company.id,
            }
        )
        self.env["stock.quant"].with_company(other_company)._update_available_quantity(
            other_product, other_location, 4.0
        )
        wizard = self._new_wizard([self.product])
        self.assertNotIn(other_product, wizard.eligible_product_ids)

    def test_selected_product_with_no_eligible_quant_is_rejected(self):
        no_stock_product = self._create_storable_product(name="No Stock Product")
        wizard = self._new_wizard([no_stock_product])
        with self.assertRaises(UserError):
            wizard.action_next()

    def test_zero_available_quant_is_not_eligible(self):
        reserved_product = self._create_storable_product(name="Fully Reserved Product")
        quant = self._set_quant_quantity(
            reserved_product, self.source_location, 3.0
        )
        self.env["stock.quant"]._update_available_quantity(
            reserved_product,
            self.source_location,
            reserved_quantity=3.0,
        )
        wizard = self._new_wizard([reserved_product])
        self.assertEqual(
            float_compare(
                quant.available_quantity,
                0,
                precision_rounding=reserved_product.uom_id.rounding,
            ),
            0,
        )
        with self.assertRaises(UserError):
            wizard.action_next()
