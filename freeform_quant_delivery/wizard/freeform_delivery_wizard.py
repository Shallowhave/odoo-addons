import math
import uuid
from collections import defaultdict

import psycopg2.errors

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare, float_round

from ..models.stock_quant import _quant_record_identity


class FreeformDeliveryWizard(models.TransientModel):
    _name = "freeform.delivery.wizard"
    _description = "Free-form Delivery Wizard"
    _check_company_auto = True

    state = fields.Selection(
        [("select_products", "Select Products"), ("select_quants", "Select Stock")],
        default="select_products",
        required=True,
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    picking_type_id = fields.Many2one(
        "stock.picking.type", required=True, check_company=True
    )
    source_location_id = fields.Many2one(
        "stock.location",
        related="picking_type_id.default_location_src_id",
        readonly=True,
    )
    destination_location_id = fields.Many2one(
        "stock.location",
        related="picking_type_id.default_location_dest_id",
        readonly=True,
    )
    customer_id = fields.Many2one("res.partner", required=True)
    delivery_address_id = fields.Many2one("res.partner", required=True)
    note = fields.Html()
    eligible_product_ids = fields.Many2many(
        "product.product",
        compute="_compute_eligible_product_ids",
    )
    product_line_ids = fields.One2many(
        "freeform.delivery.product.line", "wizard_id", string="Products"
    )
    quant_line_ids = fields.One2many(
        "freeform.delivery.quant.line", "wizard_id", string="Available Stock"
    )
    picking_id = fields.Many2one("stock.picking", readonly=True, copy=False)
    request_token = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: str(uuid.uuid4()),
    )

    @api.depends_context("lang")
    def _compute_display_name(self):
        display_name = self.env._("Free-form Customer Delivery")
        for wizard in self:
            wizard.display_name = display_name

    @api.depends("company_id", "picking_type_id", "source_location_id")
    def _compute_eligible_product_ids(self):
        Quant = self.env["stock.quant"]
        for wizard in self:
            wizard.eligible_product_ids = False
            if (
                not wizard.company_id
                or not wizard.source_location_id
                or wizard.source_location_id.usage != "internal"
            ):
                continue
            quants = Quant.search(
                wizard._eligible_quant_domain(),
                order="product_id, lot_id, id",
            )
            eligible_quants = quants.filtered(
                lambda quant: float_compare(
                    quant.available_quantity,
                    0,
                    precision_rounding=quant.product_id.uom_id.rounding,
                )
                > 0
            )
            wizard.eligible_product_ids = eligible_quants.product_id

    def _eligible_quant_domain(self, products=None):
        self.ensure_one()
        domain = [
            ("company_id", "=", self.company_id.id),
            ("owner_id", "=", False),
            ("location_id", "child_of", self.source_location_id.id),
            ("location_id.usage", "=", "internal"),
            ("product_id.is_storable", "=", True),
            ("quantity", ">", 0),
        ]
        if products is not None:
            domain.append(("product_id", "in", products.ids))
        return domain

    def _default_delivery_address(self):
        self.ensure_one()
        if not self.customer_id:
            return self.env["res.partner"]
        address_id = self.customer_id.address_get(["delivery"])["delivery"]
        return self.env["res.partner"].browse(address_id)

    @api.onchange("customer_id")
    def _onchange_customer_id(self):
        for wizard in self:
            wizard.delivery_address_id = wizard._default_delivery_address()

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            if values.get("customer_id") and not values.get("delivery_address_id"):
                customer = self.env["res.partner"].browse(values["customer_id"])
                values["delivery_address_id"] = customer.address_get(
                    ["delivery"]
                )["delivery"]
        return super().create(values_list)

    def _check_manager_access(self):
        if not self.env.user.has_group("stock.group_stock_manager"):
            raise AccessError(
                _("Only inventory managers can create free-form deliveries.")
            )

    def _validate_header(self):
        self.ensure_one()
        if (
            self.company_id != self.env.company
            or self.picking_type_id.company_id != self.company_id
        ):
            raise UserError(_("The operation type must belong to the current company."))
        if self.picking_type_id.code != "outgoing":
            raise UserError(_("The operation type must be an outgoing delivery."))
        if not self.source_location_id or self.source_location_id.usage != "internal":
            raise UserError(_("The source location must be an internal location."))
        if (
            not self.destination_location_id
            or self.destination_location_id.usage != "customer"
        ):
            raise UserError(_("The destination location must be a customer location."))
        if not self.customer_id or not self.delivery_address_id:
            raise UserError(_("Select a customer and delivery address."))
        if (
            self.delivery_address_id.commercial_partner_id
            != self.customer_id.commercial_partner_id
        ):
            raise UserError(
                _("The delivery address must belong to the selected customer.")
            )
        if (
            self.delivery_address_id != self.customer_id
            and self.delivery_address_id.type != "delivery"
        ):
            raise UserError(_("Select a delivery address for the customer."))

    def _get_eligible_quants(self):
        self.ensure_one()
        products = self.product_line_ids.product_id
        quants = self.env["stock.quant"].search(
            self._eligible_quant_domain(products),
            order="product_id, lot_id, id",
        )
        return quants.filtered(
            lambda quant: float_compare(
                quant.available_quantity,
                0,
                precision_rounding=quant.product_id.uom_id.rounding,
            )
            > 0
        )

    def _validate_products(self):
        self.ensure_one()
        lines = self.product_line_ids
        if not lines or any(not line.product_id for line in lines):
            raise UserError(_("Select at least one product."))
        product_ids = [line.product_id.id for line in lines]
        if len(product_ids) != len(set(product_ids)):
            raise UserError(_("Each product can be selected only once."))
        products = lines.product_id
        if any(not product.is_storable for product in products):
            raise UserError(_("Only storable products can be delivered."))
        if any(product not in self.eligible_product_ids for product in products):
            raise UserError(
                _("Selected products must have available stock in the source location.")
            )
        return products

    def _regenerate_quant_lines(self):
        self.ensure_one()
        products = self.product_line_ids.product_id
        quants = self._get_eligible_quants()
        products_with_quants = quants.product_id
        if any(product not in products_with_quants for product in products):
            raise UserError(
                _("Every selected product must have eligible available stock.")
            )
        commands = [Command.clear()]
        commands.extend(
            Command.create(
                {
                    "quant_id": quant.id,
                    "product_id": quant.product_id.id,
                    "location_id": quant.location_id.id,
                    "lot_id": quant.lot_id.id,
                    "package_id": quant.package_id.id,
                    "owner_id": quant.owner_id.id,
                    "company_id": quant.company_id.id,
                    "product_uom_id": quant.product_id.uom_id.id,
                    "in_date": quant.in_date,
                    "on_hand_quantity": quant.quantity,
                    "reserved_quantity": quant.reserved_quantity,
                    "available_quantity": quant.available_quantity,
                    "lot_quantity": quant.lot_quantity,
                    "lot_unit_name": quant.lot_unit_name,
                    "lot_unit_name_custom": quant.lot_unit_name_custom,
                    "contract_no": quant.contract_no,
                    "calculated_length_m": quant.calculated_length_m,
                    "actual_length_m": quant.actual_length_m,
                    "actual_area_sqm": quant.actual_area_sqm,
                    "is_roll_product": quant.is_roll_product,
                    "product_width": quant.product_id.product_width,
                    "product_thickness": quant.product_id.product_thickness,
                    "product_length": quant.product_id.product_length,
                    "weight_per_sqm": quant.product_id.weight_per_sqm,
                }
            )
            for quant in quants
        )
        self.write({"quant_line_ids": commands})

    def _get_positive_selections(self):
        self.ensure_one()
        if self.state != "select_quants":
            raise UserError(_("Advance to stock selection first."))

        positive_lines = self.env["freeform.delivery.quant.line"]
        for line in self.quant_line_ids:
            rounding = line.product_uom_id.rounding
            quantity = line.selected_quantity
            if not math.isfinite(quantity):
                raise UserError(_("Selected quantities must be finite numbers."))
            quantity_comparison = float_compare(
                quantity,
                0,
                precision_rounding=rounding,
            )
            if quantity < 0 or quantity_comparison < 0:
                raise UserError(_("Selected quantities cannot be negative."))
            if quantity == 0:
                continue
            if quantity_comparison == 0:
                raise UserError(
                    _(
                        "The selected quantity for %(product)s is positive but "
                        "rounds to zero in %(uom)s.",
                        product=line.product_id.display_name,
                        uom=line.product_uom_id.display_name,
                    )
                )

            rounded_quantity = float_round(
                quantity,
                precision_rounding=rounding,
                rounding_method="HALF-UP",
            )
            tolerance = max(
                math.ulp(quantity),
                math.ulp(rounded_quantity),
                math.ulp(rounding),
            ) * 4
            if abs(quantity - rounded_quantity) > tolerance:
                raise UserError(
                    _(
                        "The selected quantity for %(product)s does not respect "
                        "the rounding precision of %(uom)s.",
                        product=line.product_id.display_name,
                        uom=line.product_uom_id.display_name,
                    )
                )
            if float_compare(
                quantity,
                line.available_quantity,
                precision_rounding=rounding,
            ) > 0:
                raise UserError(
                    _(
                        "The selected quantity for %(product)s exceeds the "
                        "displayed available quantity.",
                        product=line.product_id.display_name,
                    )
                )
            positive_lines |= line

        if not positive_lines:
            raise UserError(_("Select at least one stock row."))
        selected_products = self.product_line_ids.product_id
        if set(positive_lines.product_id.ids) != set(selected_products.ids):
            raise UserError(
                _("Every selected product must have a positive stock allocation.")
            )
        return positive_lines

    def _resolve_accessible_quants(self, lines):
        self.ensure_one()
        requested_ids = set(lines.quant_id.ids)
        if not requested_ids:
            raise UserError(_("Select at least one stock row."))
        Quant = self.env["stock.quant"]
        quants = Quant.search(
            [("id", "in", sorted(requested_ids))],
            order="id",
        )
        if set(quants.ids) != requested_ids:
            raise UserError(
                _(
                    "One or more selected stock rows no longer exist or are no "
                    "longer accessible. Return to stock selection and try again."
                )
            )
        return quants

    def _lock_selected_quants(self, quants):
        ids = sorted(quants.ids)
        if not ids:
            raise UserError(_("Select at least one stock row."))
        identities = [_quant_record_identity(quant) for quant in quants]
        if not self.env["stock.quant"]._try_advisory_lock_quant_identities(
            identities
        ):
            raise UserError(
                _(
                    "One or more selected stock rows are being processed. Retry "
                    "after the other operation finishes."
                )
            )
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute(
                    "SELECT id FROM stock_quant "
                    "WHERE id IN %s ORDER BY id FOR UPDATE NOWAIT",
                    [tuple(ids)],
                )
                locked_ids = [row[0] for row in self.env.cr.fetchall()]
                if locked_ids != ids:
                    raise UserError(
                        _(
                            "One or more selected stock rows disappeared before "
                            "they could be locked. Return to stock selection and "
                            "try again."
                        )
                    )
        except (
            psycopg2.errors.LockNotAvailable,
            psycopg2.errors.SerializationFailure,
        ) as error:
            raise UserError(
                _(
                    "One or more selected stock rows are being processed. Retry "
                    "after the other operation finishes."
                )
            ) from error

    def _duplicate_quant_identity_ids(self, quants):
        identity_keys = {_quant_record_identity(quant) for quant in quants}
        if not identity_keys:
            return set()

        products = quants.product_id
        candidate_quants = self.env["stock.quant"].sudo().search(
            [
                ("product_id", "in", products.ids),
                ("product_id.is_storable", "=", True),
            ],
            order="id",
        )
        quants_by_identity = defaultdict(list)
        for quant in candidate_quants:
            identity = _quant_record_identity(quant)
            if identity in identity_keys:
                quants_by_identity[identity].append(quant.id)
        return {
            quant_id
            for quant_ids in quants_by_identity.values()
            if len(quant_ids) > 1
            for quant_id in quant_ids
        }

    def _revalidate_locked_quants(self, lines, quants):
        self.ensure_one()
        fields_to_refresh = [
            "product_id",
            "location_id",
            "lot_id",
            "package_id",
            "owner_id",
            "company_id",
            "quantity",
            "reserved_quantity",
            "available_quantity",
            "lot_quantity",
            "lot_unit_name",
            "lot_unit_name_custom",
            "contract_no",
        ]
        quants.invalidate_recordset(fields_to_refresh)
        products = quants.product_id
        products.product_tmpl_id.invalidate_recordset(["is_storable", "uom_id"])
        products.invalidate_recordset(["is_storable", "uom_id"])
        quants.location_id.invalidate_recordset(["usage"])
        quant_by_id = {quant.id: quant for quant in quants}
        duplicate_quant_ids = self._duplicate_quant_identity_ids(quants)
        if duplicate_quant_ids:
            raise UserError(
                _(
                    "Duplicate stock rows share the same complete identity. "
                    "Please reconcile duplicate stock rows before creating the "
                    "delivery."
                )
            )

        source_scope = self.env["stock.location"].search(
            [("id", "child_of", self.source_location_id.id)]
        )
        source_location_ids = set(source_scope.ids)
        expected_products = set(self.product_line_ids.product_id.ids)
        product_totals = defaultdict(float)
        product_roundings = {}
        for line in lines:
            quant = quant_by_id.get(line.quant_id.id)
            if not quant:
                raise UserError(
                    _("A selected stock row is no longer available for processing.")
                )
            if quant.owner_id:
                raise UserError(_("Selected stock rows must remain company-owned."))
            if self.company_id != self.env.company:
                raise UserError(
                    _("The delivery company must remain the current company.")
                )
            if quant.company_id != self.env.company:
                raise UserError(
                    _("Selected stock rows must remain in the current company.")
                )
            if (
                quant.location_id.id not in source_location_ids
                or quant.location_id.usage != "internal"
            ):
                raise UserError(
                    _("Selected stock rows must remain in the source location scope.")
                )
            if not quant.product_id.is_storable:
                raise UserError(_("Only storable products can be delivered."))
            if line.product_uom_id != quant.product_id.uom_id:
                raise UserError(
                    _(
                        "The primary unit of measure has changed for one or more "
                        "selected stock rows. Return to stock selection and try again."
                    )
                )
            if quant.product_id.id not in expected_products:
                raise UserError(
                    _("Selected stock rows must belong to the Step 1 products.")
                )

            snapshot_identity = line._snapshot_identity()
            current_identity = (
                quant.product_id.id,
                quant.location_id.id,
                quant.lot_id.id,
                quant.package_id.id,
                quant.owner_id.id,
                quant.company_id.id,
            )
            if snapshot_identity != current_identity:
                raise UserError(
                    _(
                        "The identity has changed for one or more selected stock "
                        "rows. Return to stock selection and try again."
                    )
                )

            auxiliary_metadata_changed = (
                float_compare(
                    line.lot_quantity,
                    quant.lot_quantity,
                    precision_digits=2,
                )
                != 0
                or line.lot_unit_name != quant.lot_unit_name
                or line.lot_unit_name_custom != quant.lot_unit_name_custom
                or line.contract_no != quant.contract_no
            )
            if auxiliary_metadata_changed:
                raise UserError(
                    _(
                        "The auxiliary metadata has changed for one or more "
                        "selected stock rows. Return to stock selection and try "
                        "again."
                    )
                )

            rounding = quant.product_id.uom_id.rounding
            if float_compare(
                line.selected_quantity,
                quant.available_quantity,
                precision_rounding=rounding,
            ) > 0:
                raise UserError(
                    _(
                        "The selected quantity for %(product)s exceeds the latest "
                        "available quantity. Return to stock selection and try again.",
                        product=quant.product_id.display_name,
                    )
                )
            product_id = quant.product_id.id
            product_totals[product_id] += line.selected_quantity
            product_roundings[product_id] = rounding
        return {
            product_id: float_round(
                total,
                precision_rounding=product_roundings[product_id],
                rounding_method="HALF-UP",
            )
            for product_id, total in product_totals.items()
        }

    def _prepare_locked_selections(self):
        self.ensure_one()
        lines = self._get_positive_selections()
        quants = self._resolve_accessible_quants(lines)
        self._lock_selected_quants(quants)
        product_totals = self._revalidate_locked_quants(lines, quants)
        return lines, quants, product_totals

    def _reopen_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Free-form Delivery"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [
                (
                    self.env.ref(
                        "freeform_quant_delivery.freeform_delivery_wizard_form"
                    ).id,
                    "form",
                )
            ],
            "target": "current",
        }

    def _freeform_origin(self):
        self.ensure_one()
        return _(
            "Free-form delivery request %(token)s",
            token=self.request_token,
        )

    def _open_picking_form(self, picking):
        self.ensure_one()
        picking.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Delivery"),
            "res_model": "stock.picking",
            "res_id": picking.id,
            "view_mode": "form",
            "views": [(self.env.ref("stock.view_picking_form").id, "form")],
            "target": "current",
        }

    def _lock_for_confirmation(self):
        """Lock this exact transient row without poisoning the business savepoint."""
        self.ensure_one()
        wizard_id = self.id
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute(
                    "SELECT id, picking_id FROM freeform_delivery_wizard "
                    "WHERE id = %s FOR UPDATE NOWAIT",
                    [wizard_id],
                )
                locked_row = self.env.cr.fetchone()
        except (
            psycopg2.errors.LockNotAvailable,
            psycopg2.errors.SerializationFailure,
        ) as error:
            raise UserError(
                _("This delivery request is already being confirmed.")
            ) from error
        if not locked_row or locked_row[0] != wizard_id:
            raise UserError(
                _(
                    "This delivery request no longer exists. Close the wizard and "
                    "start a new request."
                )
            )

    def _create_picking(self):
        self.ensure_one()
        origin = self._freeform_origin()
        return self.env["stock.picking"].create(
            {
                "partner_id": self.delivery_address_id.id,
                "freeform_customer_id": self.customer_id.commercial_partner_id.id,
                "picking_type_id": self.picking_type_id.id,
                "location_id": self.source_location_id.id,
                "location_dest_id": self.destination_location_id.id,
                "note": self.note,
                "company_id": self.company_id.id,
                "user_id": self.env.user.id,
                "origin": origin,
                "is_freeform_quant_delivery": True,
                "freeform_request_token": self.request_token,
            }
        )

    def _create_zero_demand_moves(self, picking, totals):
        self.ensure_one()
        products = self.env["product.product"].browse(sorted(totals))
        if set(products.ids) != set(totals):
            raise UserError(_("One or more selected products no longer exist."))
        origin = self._freeform_origin()
        moves = self.env["stock.move"].create(
            [
                {
                    "name": product.display_name,
                    "product_id": product.id,
                    "product_uom_qty": 0.0,
                    "product_uom": product.uom_id.id,
                    "picking_id": picking.id,
                    "picking_type_id": picking.picking_type_id.id,
                    "location_id": picking.location_id.id,
                    "location_dest_id": picking.location_dest_id.id,
                    "company_id": picking.company_id.id,
                    "partner_id": picking.partner_id.id,
                    "origin": origin,
                }
                for product in products
            ]
        )
        return {move.product_id.id: move for move in moves}

    def _assert_no_automatic_reservation(self, moves):
        for move in moves:
            rounding = move.product_uom.rounding
            if float_compare(
                move.quantity,
                0,
                precision_rounding=rounding,
            ) > 0 or any(
                float_compare(
                    line.quantity_product_uom,
                    0,
                    precision_rounding=rounding,
                )
                > 0
                for line in move.move_line_ids
            ):
                raise UserError(
                    _("Zero-demand confirmation reserved stock unexpectedly.")
                )

    def _get_explicit_lot_quantity(self, selection, quant):
        if not selection.lot_unit_name:
            return False
        rounding = quant.product_id.uom_id.rounding
        if (
            float_compare(quant.quantity, 0, precision_rounding=rounding) > 0
            and quant.lot_quantity > 0
        ):
            return selection.selected_quantity / quant.quantity * quant.lot_quantity
        MoveLine = self.env["stock.move.line"]
        if MoveLine._should_default_lot_quantity_to_one(
            selection.lot_unit_name,
            selection.lot_unit_name_custom,
        ):
            return 1.0
        return selection.selected_quantity

    def _create_exact_move_line(self, values, _quant):
        return self.env["stock.move.line"].create(values)

    def _create_exact_move_lines(self, picking, moves_by_product, selections):
        self.ensure_one()
        quant_by_id = {quant.id: quant for quant in selections.quant_id}
        move_lines = self.env["stock.move.line"]
        for selection in selections.sorted(key=lambda line: line.quant_id.id):
            quant = quant_by_id[selection.quant_id.id]
            move = moves_by_product.get(quant.product_id.id)
            if not move:
                raise UserError(_("A selected product has no stock move."))
            values = move._prepare_move_line_vals(
                quantity=selection.selected_quantity
            )
            values.update(
                {
                    "picking_id": picking.id,
                    "company_id": picking.company_id.id,
                    "product_id": quant.product_id.id,
                    "location_id": quant.location_id.id,
                    "location_dest_id": picking.location_dest_id.id,
                    "lot_id": quant.lot_id.id or False,
                    "package_id": quant.package_id.id or False,
                    "owner_id": quant.owner_id.id or False,
                    "product_uom_id": quant.product_id.uom_id.id,
                    "quantity": selection.selected_quantity,
                    "picked": False,
                    "lot_unit_name": selection.lot_unit_name or False,
                    "lot_unit_name_custom": (
                        selection.lot_unit_name_custom or False
                    ),
                    "contract_no": selection.contract_no or False,
                    "lot_quantity": self._get_explicit_lot_quantity(
                        selection, quant
                    ),
                }
            )
            move_lines |= self._create_exact_move_line(values, quant)
        return move_lines

    def _assert_selected_quant_reservations(
        self, selections, quants, original_reserved_by_quant
    ):
        self.ensure_one()
        quants.invalidate_recordset(["reserved_quantity"])
        if self._duplicate_quant_identity_ids(quants):
            raise UserError(
                _(
                    "A duplicate stock row appeared while reserving the selected "
                    "stock. Retry after reconciling duplicate stock rows."
                )
            )
        selection_by_quant = {
            selection.quant_id.id: selection for selection in selections
        }
        for quant in quants:
            selection = selection_by_quant[quant.id]
            expected_reserved = (
                original_reserved_by_quant[quant.id] + selection.selected_quantity
            )
            if float_compare(
                quant.reserved_quantity,
                expected_reserved,
                precision_rounding=quant.product_id.uom_id.rounding,
            ) != 0:
                raise UserError(
                    _(
                        "The exact selected stock row was not reserved. Retry after "
                        "the latest stock operation finishes."
                    )
                )

    def _assert_exact_reservation(self, picking, selections, move_lines):
        self.ensure_one()
        moves = picking.move_ids
        expected_product_ids = set(self.product_line_ids.product_id.ids)
        if (
            len(moves) != len(expected_product_ids)
            or set(moves.product_id.ids) != expected_product_ids
        ):
            raise UserError(
                _("The delivery must contain exactly one move per product.")
            )
        if (
            len(move_lines) != len(selections)
            or set(picking.move_line_ids.ids) != set(move_lines.ids)
        ):
            raise UserError(
                _("The delivery contains missing or unrelated stock operation lines.")
            )

        move_by_product = {move.product_id.id: move for move in moves}
        line_by_identity = {}
        for move_line in move_lines:
            identity = (
                move_line.product_id.id,
                move_line.location_id.id,
                move_line.lot_id.id,
                move_line.package_id.id,
                move_line.owner_id.id,
                move_line.company_id.id,
            )
            if identity in line_by_identity:
                raise UserError(_("The delivery contains duplicate stock identities."))
            line_by_identity[identity] = move_line

        line_totals = defaultdict(float)
        for selection in selections:
            quant = selection.quant_id
            expected_identity = (
                quant.product_id.id,
                quant.location_id.id,
                quant.lot_id.id,
                quant.package_id.id,
                quant.owner_id.id,
                quant.company_id.id,
            )
            move_line = line_by_identity.get(expected_identity)
            rounding = quant.product_id.uom_id.rounding
            if (
                not move_line
                or move_line.move_id != move_by_product[quant.product_id.id]
                or move_line.picking_id != picking
                or move_line.location_dest_id != picking.location_dest_id
                or move_line.product_uom_id != quant.product_id.uom_id
                or float_compare(
                    move_line.quantity,
                    selection.selected_quantity,
                    precision_rounding=rounding,
                )
                != 0
                or float_compare(
                    move_line.quantity,
                    0,
                    precision_rounding=rounding,
                )
                <= 0
            ):
                raise UserError(
                    _("A stock operation does not match its exact selected stock row.")
                )
            if move_line.picked:
                raise UserError(_("Exact stock operation lines must remain unpicked."))
            line_totals[quant.product_id.id] += move_line.quantity_product_uom

        for product_id, move in move_by_product.items():
            rounding = move.product_uom.rounding
            if (
                float_compare(
                    move.product_uom_qty,
                    line_totals[product_id],
                    precision_rounding=rounding,
                )
                != 0
                or move.state != "assigned"
                or move.picked
            ):
                raise UserError(
                    _(
                        "Move demand and exact reserved quantity must match and be "
                        "assigned."
                    )
                )
        if picking.state != "assigned":
            raise UserError(_("The delivery was not fully assigned."))

    def action_confirm_delivery(self):
        self.ensure_one()
        with self.env.cr.savepoint():
            self._lock_for_confirmation()
            self.invalidate_recordset(["picking_id"])
            self._check_manager_access()
            if self.picking_id:
                return self._open_picking_form(self.picking_id)

            self._validate_header()
            if self.state != "select_quants":
                raise UserError(_("Advance to stock selection first."))
            selections, quants, totals = self._prepare_locked_selections()
            original_reserved_by_quant = {
                quant.id: quant.reserved_quantity for quant in quants
            }
            picking = self._create_picking()
            moves_by_product = self._create_zero_demand_moves(picking, totals)
            moves = self.env["stock.move"].concat(*moves_by_product.values())
            moves._action_confirm(merge=False)
            self._assert_no_automatic_reservation(moves)
            move_lines = self._create_exact_move_lines(
                picking,
                moves_by_product,
                selections,
            )
            for product_id, demand in totals.items():
                moves_by_product[product_id].with_context(
                    do_not_unreserve=True
                ).write({"product_uom_qty": demand})
            moves._recompute_state()
            self._assert_exact_reservation(picking, selections, move_lines)
            self._assert_selected_quant_reservations(
                selections,
                quants,
                original_reserved_by_quant,
            )
            self.picking_id = picking
        return self._open_picking_form(picking)

    def action_next(self):
        self.ensure_one()
        self._check_manager_access()
        self._validate_header()
        self._validate_products()
        self._regenerate_quant_lines()
        self.state = "select_quants"
        return self._reopen_action()

    def action_back(self):
        self.ensure_one()
        self._check_manager_access()
        self.quant_line_ids.unlink()
        self.state = "select_products"
        return self._reopen_action()
