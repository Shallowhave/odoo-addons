from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import psycopg2.errors

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.freeform_quant_delivery.models.stock_quant import (
    StockQuant,
    _canonical_quant_identity,
    _complete_quant_identity,
    _quant_record_identity,
)
from odoo.addons.freeform_quant_delivery.wizard.freeform_delivery_wizard import (
    FreeformDeliveryWizard,
)

from .common import FreeformDeliveryCommon


class _RecoveringSavepoint:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        self.cursor.in_failed_transaction = False
        return self

    def __exit__(self, error_type, _error, _traceback):
        if error_type:
            self.cursor.in_failed_transaction = False
        return False


class _ModelEnvironment(SimpleNamespace):
    def __init__(self, *, cr, models=None):
        super().__init__(cr=cr, lang="en_US")
        self.models = models or {}

    def __getitem__(self, model_name):
        return self.models[model_name]


def _mock_quants(ids):
    quants = MagicMock()
    quants.ids = list(ids)
    quants.__iter__.return_value = iter(
        [
            SimpleNamespace(
                product_id=SimpleNamespace(id=quant_id + 100),
                company_id=SimpleNamespace(id=1),
                location_id=SimpleNamespace(
                    id=2, company_id=SimpleNamespace(id=1)
                ),
                lot_id=SimpleNamespace(id=None),
                package_id=SimpleNamespace(id=None),
                owner_id=SimpleNamespace(id=None),
            )
            for quant_id in ids
        ]
    )
    return quants


class _RecoveringCursor:
    def __init__(self, error_type=psycopg2.errors.LockNotAvailable):
        self.error_type = error_type
        self.in_failed_transaction = False
        self.calls = []

    def savepoint(self, *, flush):
        self.calls.append(("savepoint", flush))
        return _RecoveringSavepoint(self)

    def execute(self, query, params=None):
        if self.in_failed_transaction:
            raise AssertionError("outer transaction remained failed")
        self.calls.append(("execute", query, params))
        if "FOR UPDATE NOWAIT" in query:
            self.in_failed_transaction = True
            raise self.error_type()


@tagged("post_install", "-at_install")
class TestStockQuantAdvisoryLocking(FreeformDeliveryCommon):
    def test_complete_identity_derives_company_and_preserves_nulls(self):
        identity = _complete_quant_identity(
            SimpleNamespace(id=11),
            SimpleNamespace(id=33, company_id=SimpleNamespace(id=22)),
        )

        self.assertEqual(identity, (11, 22, 33, None, None, None))
        self.assertEqual(
            _canonical_quant_identity(identity),
            "stock.quant|product=11|company=22|location=33|lot=<null>|"
            "package=<null>|owner=<null>",
        )

    def test_try_locks_sorted_canonical_identities_and_stops_on_false(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [(True,), (False,)]
        quant_model = SimpleNamespace(env=SimpleNamespace(cr=cursor))
        identities = [
            (9, 1, 2, None, None, None),
            (3, 1, 2, 4, None, None),
            (9, 1, 2, None, None, None),
        ]

        result = StockQuant._try_advisory_lock_quant_identities(
            quant_model, identities
        )

        self.assertFalse(result)
        canonical = sorted({_canonical_quant_identity(item) for item in identities})
        self.assertEqual(
            cursor.execute.call_args_list,
            [
                call(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                    [key],
                )
                for key in canonical
            ],
        )

    def test_blocking_advisory_lock_uses_stable_postgresql_hash(self):
        cursor = MagicMock()
        quant_model = SimpleNamespace(env=SimpleNamespace(cr=cursor))
        identity = (11, None, 22, None, 44, None)

        StockQuant._acquire_quant_identity_advisory_lock(quant_model, identity)

        cursor.execute.assert_called_once_with(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [_canonical_quant_identity(identity)],
        )

    def test_update_available_quantity_blocks_before_delegating(self):
        quant_model = self.env["stock.quant"]
        calls = []
        sentinel = object()

        def acquire(records, identity):
            calls.append(("lock", identity))

        def parent(records, *args, **kwargs):
            calls.append(("super", args, kwargs))
            return sentinel

        with (
            patch.object(
                StockQuant,
                "_acquire_quant_identity_advisory_lock",
                autospec=True,
                side_effect=acquire,
            ),
            patch(
                "odoo.addons.stock.models.stock_quant.StockQuant._update_available_quantity",
                autospec=True,
                side_effect=parent,
            ) as parent_update,
        ):
            result = quant_model._update_available_quantity(
                self.product,
                self.source_location,
                quantity=5.0,
            )

        self.assertIs(result, sentinel)
        self.assertEqual(calls[0], ("lock", (
            self.product.id,
            self.company.id,
            self.source_location.id,
            None,
            None,
            None,
        )))
        self.assertEqual(calls[1][0], "super")
        parent_update.assert_called_once_with(
            quant_model,
            self.product,
            self.source_location,
            quantity=5.0,
            reserved_quantity=False,
            lot_id=None,
            package_id=None,
            owner_id=None,
            in_date=None,
        )


@tagged("post_install", "-at_install")
class TestExactReservationValidation(FreeformDeliveryCommon):
    def _wizard_at_quant_step(self, *quants, quantity=0.0, products=None):
        products = products or quants[0].product_id
        wizard = self._create_freeform_wizard(products=products)
        wizard.action_next()
        selected_quant_ids = set(quant.id for quant in quants)
        wizard.quant_line_ids.filtered(
            lambda line: line.quant_id.id in selected_quant_ids
        ).write({"selected_quantity": quantity})
        return wizard, wizard.quant_line_ids.filtered(
            lambda line: line.quant_id.id in selected_quant_ids
        )

    def test_zero_selection_is_rejected(self):
        wizard, _line = self._wizard_at_quant_step(self.quant)

        with self.assertRaisesRegex(UserError, "Select at least one stock row"):
            wizard._prepare_locked_selections()

    def test_negative_selection_is_rejected(self):
        wizard, line = self._wizard_at_quant_step(self.quant)
        line.write({"selected_quantity": -1.0})

        with self.assertRaisesRegex(UserError, "cannot be negative"):
            wizard._prepare_locked_selections()

    def test_selected_quantity_has_no_storage_precision_declaration(self):
        field = self.env["freeform.delivery.quant.line"]._fields[
            "selected_quantity"
        ]

        self.assertIsNone(field._digits)

    def test_tiny_negative_selection_reaches_validator(self):
        wizard, line = self._wizard_at_quant_step(self.quant)
        line.write({"selected_quantity": -0.004})
        self.assertEqual(line.selected_quantity, -0.004)

        with self.assertRaisesRegex(UserError, "cannot be negative"):
            wizard._prepare_locked_selections()

    def test_nonfinite_selection_reaches_validator(self):
        for quantity in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(quantity=quantity):
                wizard, line = self._wizard_at_quant_step(self.quant)
                line.write({"selected_quantity": quantity})
                with self.assertRaisesRegex(UserError, "finite numbers"):
                    wizard._prepare_locked_selections()

    def test_selection_above_displayed_availability_is_rejected(self):
        wizard, line = self._wizard_at_quant_step(self.quant)
        line.selected_quantity = line.available_quantity + 1.0

        with self.assertRaisesRegex(UserError, "displayed available"):
            wizard._prepare_locked_selections()

    def _whole_unit_stock(self):
        category = self.env["uom.category"].create({"name": "Whole Test Units"})
        whole_uom = self.env["uom.uom"].create(
            {
                "name": "Whole Test Unit",
                "category_id": category.id,
                "uom_type": "reference",
                "rounding": 1.0,
            }
        )
        product = self._create_storable_product(
            name="Whole-unit Product", uom=whole_uom
        )
        quant = self._set_quant_quantity(product, self.source_location, 5.0)
        return whole_uom, quant

    def test_selection_must_respect_primary_uom_rounding(self):
        _whole_uom, quant = self._whole_unit_stock()
        wizard, line = self._wizard_at_quant_step(quant)
        line.selected_quantity = 1.5

        with self.assertRaisesRegex(UserError, "rounding precision"):
            wizard._prepare_locked_selections()

    def test_binary_float_boundary_respects_primary_uom_rounding(self):
        wizard, line = self._wizard_at_quant_step(self.quant)
        line.write({"selected_quantity": 0.1 + 0.2})

        lines, quants, totals = wizard._prepare_locked_selections()

        self.assertEqual(lines.quant_id, quants)
        self.assertEqual(totals, {self.product.id: 0.3})

    def test_fine_rounding_accepts_binary_representation_noise(self):
        category = self.env["uom.category"].create({"name": "Fine Test Units"})
        fine_uom = self.env["uom.uom"].create(
            {
                "name": "Fine Test Unit",
                "category_id": category.id,
                "uom_type": "reference",
                "rounding": 0.000001,
            }
        )
        product = self._create_storable_product(
            name="Fine-rounding Product",
            uom=fine_uom,
        )
        quant = self._set_quant_quantity(product, self.source_location, 5.0)
        wizard, line = self._wizard_at_quant_step(quant)
        line.write({"selected_quantity": 0.1 + 0.2})

        _lines, _quants, totals = wizard._prepare_locked_selections()

        self.assertEqual(totals, {product.id: 0.3})

    def test_positive_quantity_that_rounds_to_zero_is_rejected(self):
        wizard, line = self._wizard_at_quant_step(self.quant)
        self.assertEqual(line.product_uom_id.rounding, 0.01)
        self.assertEqual(
            self.env["decimal.precision"].precision_get("Product Unit of Measure"),
            2,
        )
        line.write({"selected_quantity": 0.004})
        self.assertEqual(line.selected_quantity, 0.004)

        with patch.object(
            FreeformDeliveryWizard,
            "_lock_selected_quants",
            autospec=True,
        ) as lock_selected:
            with self.assertRaisesRegex(UserError, "positive but rounds to zero"):
                wizard._prepare_locked_selections()
        lock_selected.assert_not_called()

    def test_every_step_one_product_requires_a_positive_allocation(self):
        wizard = self._create_freeform_wizard(
            products=[self.product, self.tracked_product]
        )
        wizard.action_next()
        wizard.quant_line_ids.filtered(
            lambda line: line.product_id == self.product
        ).selected_quantity = 1.0

        with self.assertRaisesRegex(UserError, "Every selected product"):
            wizard._prepare_locked_selections()

    def test_quant_line_rejects_quant_and_snapshot_tampering(self):
        wizard, line = self._wizard_at_quant_step(self.quant, quantity=1.0)
        replacement_package = self.env["stock.quant.package"].create(
            {"name": "Task 4 Tamper Package"}
        )
        tamper_values = {
            "wizard_id": wizard.id,
            "quant_id": self.tracked_quant.id,
            "product_uom_id": self.tracked_product.uom_id.id,
            "product_id": self.tracked_product.id,
            "location_id": self.source_child_location.id,
            "lot_id": self.lot.id,
            "package_id": replacement_package.id,
            "owner_id": self.customer.id,
            "company_id": self.company.id,
            "in_date": self.tracked_quant.in_date,
            "on_hand_quantity": 1.0,
            "reserved_quantity": 1.0,
            "available_quantity": 1.0,
            "lot_quantity": 1.0,
            "lot_unit_name": "roll",
            "lot_unit_name_custom": "Tampered",
            "contract_no": "TAMPER",
            "calculated_length_m": 1.0,
            "actual_length_m": 1.0,
            "actual_area_sqm": 1.0,
            "is_roll_product": True,
            "product_width": 1,
            "product_thickness": 1,
            "product_length": 1.0,
            "weight_per_sqm": 1.0,
        }
        for field_name, value in tamper_values.items():
            with self.subTest(field=field_name):
                with self.assertRaisesRegex(UserError, "Only the selected quantity"):
                    line.write({field_name: value})

        line.write({"selected_quantity": 2.0})
        self.assertEqual(line.selected_quantity, 2.0)

    def test_quant_line_rejects_framework_managed_field_in_caller_values(self):
        _wizard, line = self._wizard_at_quant_step(self.quant)

        with self.assertRaisesRegex(UserError, "Only the selected quantity"):
            line.write({"selected_quantity": 1.0, "write_uid": self.env.user.id})

    def test_quant_line_unknown_field_is_rejected_deterministically(self):
        wizard, line = self._wizard_at_quant_step(self.quant)

        with self.assertRaisesRegex(UserError, "Only the selected quantity"):
            line.write({"unexpected_rpc_field": wizard.id})

    def test_quant_line_unlink_remains_allowed(self):
        _wizard, line = self._wizard_at_quant_step(self.quant)
        self.assertTrue(line.unlink())

    def test_all_complete_identity_mismatches_are_rejected(self):
        identity_fields = (
            "product_id",
            "location_id",
            "lot_id",
            "package_id",
            "owner_id",
            "company_id",
        )
        for field_index, field_name in enumerate(identity_fields):
            with self.subTest(field=field_name):
                wizard, line = self._wizard_at_quant_step(self.quant, quantity=1.0)
                changed_identity = list(line._snapshot_identity())
                changed_identity[field_index] = -1
                with patch.object(
                    type(line),
                    "_snapshot_identity",
                    autospec=True,
                    return_value=tuple(changed_identity),
                ):
                    with self.assertRaisesRegex(UserError, "identity has changed"):
                        wizard._prepare_locked_selections()

    def test_selected_quant_must_be_company_owned(self):
        owner = self.env["res.partner"].create({"name": "Task 4 Stock Owner"})
        wizard, line = self._wizard_at_quant_step(self.quant, quantity=1.0)
        self.quant.owner_id = owner
        with patch.object(
            type(line),
            "_snapshot_identity",
            autospec=True,
            return_value=(
                self.quant.product_id.id,
                self.quant.location_id.id,
                self.quant.lot_id.id,
                self.quant.package_id.id,
                owner.id,
                self.quant.company_id.id,
            ),
        ):
            with self.assertRaisesRegex(UserError, "company-owned"):
                wizard._prepare_locked_selections()

    def test_wizard_company_must_remain_the_active_company(self):
        other_company = self.env["res.company"].create(
            {"name": "Task 4 Active Company"}
        )
        wizard, _line = self._wizard_at_quant_step(self.quant, quantity=1.0)

        with self.assertRaisesRegex(UserError, "delivery company must remain"):
            wizard.with_company(other_company)._prepare_locked_selections()

    def test_selected_quant_must_remain_in_current_company(self):
        other_company = self.env["res.company"].create({"name": "Task 4 Company"})
        other_location = self.env["stock.location"].create(
            {
                "name": "Task 4 Other-company Stock",
                "usage": "internal",
                "company_id": other_company.id,
            }
        )
        wizard, line = self._wizard_at_quant_step(self.quant, quantity=1.0)
        self.quant.location_id = other_location
        with patch.object(
            type(line),
            "_snapshot_identity",
            autospec=True,
            return_value=(
                self.quant.product_id.id,
                other_location.id,
                self.quant.lot_id.id,
                self.quant.package_id.id,
                self.quant.owner_id.id,
                other_company.id,
            ),
        ):
            with self.assertRaisesRegex(UserError, "current company"):
                wizard._prepare_locked_selections()

    def test_selected_quant_must_remain_in_source_scope(self):
        outside_location = self.env["stock.location"].create(
            {
                "name": "Task 4 Outside Source",
                "usage": "internal",
                "company_id": self.company.id,
            }
        )
        wizard, line = self._wizard_at_quant_step(self.quant, quantity=1.0)
        self.quant.location_id = outside_location
        with patch.object(
            type(line),
            "_snapshot_identity",
            autospec=True,
            return_value=(
                self.quant.product_id.id,
                outside_location.id,
                self.quant.lot_id.id,
                self.quant.package_id.id,
                self.quant.owner_id.id,
                self.quant.company_id.id,
            ),
        ):
            with self.assertRaisesRegex(UserError, "source location"):
                wizard._prepare_locked_selections()

    def test_selected_quant_must_remain_available(self):
        wizard, _line = self._wizard_at_quant_step(self.quant, quantity=5.0)
        self.env["stock.quant"]._update_reserved_quantity(
            self.product,
            self.quant.location_id,
            8.0,
            lot_id=self.quant.lot_id,
            package_id=self.quant.package_id,
            owner_id=self.quant.owner_id,
        )

        with self.assertRaisesRegex(UserError, "latest available"):
            wizard._prepare_locked_selections()

    def test_post_lock_revalidation_discards_stale_quant_cache(self):
        wizard, _line = self._wizard_at_quant_step(self.quant, quantity=5.0)
        self.assertEqual(self.quant.available_quantity, 12.0)
        self.env.cr.execute(
            "UPDATE stock_quant SET reserved_quantity = %s WHERE id = %s",
            [10.0, self.quant.id],
        )

        with self.assertRaisesRegex(UserError, "latest available"):
            wizard._prepare_locked_selections()

    def test_inaccessible_quant_is_rejected_before_locking(self):
        wizard, _line = self._wizard_at_quant_step(self.quant, quantity=1.0)
        self.env["ir.rule"].create(
            {
                "name": "Hide Task 4 Quant",
                "model_id": self.env["ir.model"]._get_id("stock.quant"),
                "domain_force": repr([("id", "!=", self.quant.id)]),
                "perm_read": True,
            }
        )

        with patch.object(
            FreeformDeliveryWizard,
            "_lock_selected_quants",
            autospec=True,
        ) as lock_selected:
            with self.assertRaisesRegex(UserError, "no longer accessible"):
                wizard.with_user(self.stock_manager)._prepare_locked_selections()
        lock_selected.assert_not_called()

    def test_lock_uses_exact_sql_sorted_ids_and_fetches_every_result(self):
        cursor = MagicMock()
        cursor.savepoint.return_value = nullcontext()
        cursor.fetchall.return_value = [(2,), (7,), (9,)]
        quant_locker = MagicMock()
        quant_locker._try_advisory_lock_quant_identities.return_value = True
        wizard = SimpleNamespace(
            env=_ModelEnvironment(cr=cursor, models={"stock.quant": quant_locker})
        )
        quants = _mock_quants([9, 2, 7])

        FreeformDeliveryWizard._lock_selected_quants(wizard, quants)

        quant_locker._try_advisory_lock_quant_identities.assert_called_once_with(
            [
                (109, 1, 2, None, None, None),
                (102, 1, 2, None, None, None),
                (107, 1, 2, None, None, None),
            ]
        )
        cursor.savepoint.assert_called_once_with(flush=False)
        cursor.execute.assert_called_once_with(
            "SELECT id FROM stock_quant WHERE id IN %s ORDER BY id FOR UPDATE NOWAIT",
            [(2, 7, 9)],
        )
        cursor.fetchall.assert_called_once_with()

    def test_lock_rejects_a_missing_returned_id(self):
        cursor = MagicMock()
        cursor.savepoint.return_value = nullcontext()
        cursor.fetchall.return_value = [(2,), (9,)]
        quant_locker = MagicMock()
        quant_locker._try_advisory_lock_quant_identities.return_value = True
        wizard = SimpleNamespace(
            env=_ModelEnvironment(cr=cursor, models={"stock.quant": quant_locker})
        )

        with self.assertRaisesRegex(UserError, "disappeared before"):
            FreeformDeliveryWizard._lock_selected_quants(
                wizard,
                _mock_quants([9, 2, 7]),
            )

    def test_lock_contention_is_translated_and_outer_cursor_remains_usable(self):
        cursor = _RecoveringCursor()
        quant_locker = MagicMock()
        quant_locker._try_advisory_lock_quant_identities.return_value = True
        wizard = SimpleNamespace(
            env=_ModelEnvironment(cr=cursor, models={"stock.quant": quant_locker})
        )

        with self.assertRaisesRegex(UserError, "being processed"):
            FreeformDeliveryWizard._lock_selected_quants(
                wizard,
                _mock_quants([3]),
            )
        cursor.execute("SELECT 1")

        self.assertFalse(cursor.in_failed_transaction)
        self.assertEqual(cursor.calls[0], ("savepoint", False))
        self.assertEqual(cursor.calls[-1], ("execute", "SELECT 1", None))

    def test_quant_serialization_failure_is_translated_and_cursor_recovers(self):
        cursor = _RecoveringCursor(psycopg2.errors.SerializationFailure)
        quant_locker = MagicMock()
        quant_locker._try_advisory_lock_quant_identities.return_value = True
        wizard = SimpleNamespace(
            env=_ModelEnvironment(cr=cursor, models={"stock.quant": quant_locker})
        )

        with self.assertRaisesRegex(UserError, "being processed"):
            FreeformDeliveryWizard._lock_selected_quants(
                wizard,
                _mock_quants([3]),
            )
        cursor.execute("SELECT 1")

        self.assertFalse(cursor.in_failed_transaction)

    def test_advisory_contention_is_translated_before_row_lock(self):
        cursor = MagicMock()
        quant_locker = MagicMock()
        quant_locker._try_advisory_lock_quant_identities.return_value = False
        wizard = SimpleNamespace(
            env=_ModelEnvironment(cr=cursor, models={"stock.quant": quant_locker})
        )

        with self.assertRaisesRegex(UserError, "being processed"):
            FreeformDeliveryWizard._lock_selected_quants(
                wizard,
                _mock_quants([3]),
            )

        cursor.savepoint.assert_not_called()
        cursor.execute.assert_not_called()

    def test_selected_product_must_remain_storable(self):
        wizard, _line = self._wizard_at_quant_step(self.quant, quantity=1.0)
        self.quant.product_id.is_storable = False

        with self.assertRaisesRegex(UserError, "Only storable products"):
            wizard._prepare_locked_selections()

    def test_post_lock_revalidation_discards_stale_related_product_cache(self):
        wizard, _line = self._wizard_at_quant_step(self.quant, quantity=1.0)
        self.assertTrue(self.product.is_storable)
        self.env.cr.execute(
            "UPDATE product_template SET is_storable = FALSE WHERE id = %s",
            [self.product.product_tmpl_id.id],
        )

        with self.assertRaisesRegex(UserError, "Only storable products"):
            wizard._prepare_locked_selections()

    def test_post_lock_revalidation_discards_stale_related_location_cache(self):
        wizard, _line = self._wizard_at_quant_step(self.quant, quantity=1.0)
        self.assertEqual(self.source_location.usage, "internal")
        self.env.cr.execute(
            "UPDATE stock_location SET usage = 'inventory' WHERE id = %s",
            [self.source_location.id],
        )

        with self.assertRaisesRegex(UserError, "source location"):
            wizard._prepare_locked_selections()

    def test_product_totals_use_primary_uom_rounding(self):
        child_quant_a = self._set_quant_quantity(
            self.product, self.source_child_location, 4.0
        )
        second_child_location = self.env["stock.location"].create(
            {
                "name": "Free-form Source Second Child",
                "usage": "internal",
                "location_id": self.source_location.id,
                "company_id": self.company.id,
            }
        )
        child_quant_b = self._set_quant_quantity(
            self.product, second_child_location, 4.0
        )
        wizard = self._create_freeform_wizard(products=[self.product])
        wizard.action_next()
        wizard.quant_line_ids.filtered(
            lambda line: line.quant_id == self.quant
        ).selected_quantity = 0.1
        wizard.quant_line_ids.filtered(
            lambda line: line.quant_id == child_quant_a
        ).selected_quantity = 0.2
        wizard.quant_line_ids.filtered(
            lambda line: line.quant_id == child_quant_b
        ).selected_quantity = 0.3

        lines, quants, totals = wizard._prepare_locked_selections()

        self.assertEqual(lines.quant_id, quants)
        self.assertEqual(totals, {self.product.id: 0.6})

    def test_duplicate_complete_quant_identity_requires_reconciliation(self):
        self.env["stock.quant"].sudo().create(
            {
                "product_id": self.product.id,
                "location_id": self.quant.location_id.id,
                "lot_id": self.quant.lot_id.id,
                "package_id": self.quant.package_id.id,
                "owner_id": self.quant.owner_id.id,
                "quantity": 0.0,
            }
        )
        wizard, _line = self._wizard_at_quant_step(self.quant, quantity=1.0)

        with self.assertRaisesRegex(UserError, "reconcile duplicate stock rows"):
            wizard._prepare_locked_selections()

    def test_post_reservation_requires_selected_quant_delta(self):
        wizard, line = self._wizard_at_quant_step(self.quant, quantity=1.0)
        lines, quants, _totals = wizard._prepare_locked_selections()
        original_reserved = self.quant.reserved_quantity

        with self.assertRaisesRegex(UserError, "exact selected stock row"):
            wizard._assert_selected_quant_reservations(
                lines,
                quants,
                {self.quant.id: original_reserved},
            )

        self.assertEqual(line.selected_quantity, 1.0)
        self.assertEqual(self.quant.reserved_quantity, original_reserved)

    def test_post_reservation_reruns_duplicate_identity_detection(self):
        wizard, _line = self._wizard_at_quant_step(self.quant, quantity=1.0)
        lines, quants, _totals = wizard._prepare_locked_selections()
        original_reserved = self.quant.reserved_quantity
        duplicate = self.env["stock.quant"].sudo().create(
            {
                "product_id": self.quant.product_id.id,
                "company_id": self.quant.company_id.id,
                "location_id": self.quant.location_id.id,
                "lot_id": self.quant.lot_id.id,
                "package_id": self.quant.package_id.id,
                "owner_id": self.quant.owner_id.id,
                "quantity": 0.0,
            }
        )

        with self.assertRaisesRegex(UserError, "duplicate stock row appeared"):
            wizard._assert_selected_quant_reservations(
                lines,
                quants,
                {self.quant.id: original_reserved},
            )

        self.assertTrue(duplicate.exists())

    def test_complete_duplicate_identity_includes_company(self):
        base_identity = _quant_record_identity(
            SimpleNamespace(
                product_id=SimpleNamespace(id=11),
                company_id=SimpleNamespace(id=22),
                location_id=SimpleNamespace(
                    id=33, company_id=SimpleNamespace(id=22)
                ),
                lot_id=SimpleNamespace(id=44),
                package_id=SimpleNamespace(id=55),
                owner_id=SimpleNamespace(id=66),
            )
        )
        other_company_identity = _quant_record_identity(
            SimpleNamespace(
                product_id=SimpleNamespace(id=11),
                company_id=SimpleNamespace(id=77),
                location_id=SimpleNamespace(
                    id=33, company_id=SimpleNamespace(id=77)
                ),
                lot_id=SimpleNamespace(id=44),
                package_id=SimpleNamespace(id=55),
                owner_id=SimpleNamespace(id=66),
            )
        )

        self.assertNotEqual(base_identity, other_company_identity)
        self.assertEqual(base_identity, (11, 22, 33, 44, 55, 66))

    def test_duplicate_detection_uses_every_persistable_identity_component(self):
        Quant = self.env["stock.quant"].sudo()
        other_location = self.env["stock.location"].create(
            {
                "name": "Task 4 Identity Location",
                "usage": "internal",
                "location_id": self.source_location.id,
                "company_id": self.company.id,
            }
        )
        other_lot = self.env["stock.lot"].create(
            {
                "name": "Task 4 Identity Lot",
                "product_id": self.tracked_product.id,
                "company_id": self.company.id,
            }
        )
        other_package = self.env["stock.quant.package"].create(
            {"name": "Task 4 Identity Package"}
        )
        other_owner = self.env["res.partner"].create(
            {"name": "Task 4 Identity Owner"}
        )
        other_product = self._create_storable_product(
            name="Task 4 Identity Product"
        )
        location_control = Quant.create(
            {
                "product_id": self.tracked_product.id,
                "location_id": other_location.id,
                "lot_id": self.lot.id,
                "package_id": self.package.id,
                "quantity": 0.0,
            }
        )
        lot_control = Quant.create(
            {
                "product_id": self.tracked_product.id,
                "location_id": self.source_child_location.id,
                "lot_id": other_lot.id,
                "package_id": self.package.id,
                "quantity": 0.0,
            }
        )
        package_control = Quant.create(
            {
                "product_id": self.tracked_product.id,
                "location_id": self.source_child_location.id,
                "lot_id": self.lot.id,
                "package_id": other_package.id,
                "quantity": 0.0,
            }
        )
        owner_control = Quant.create(
            {
                "product_id": self.tracked_product.id,
                "location_id": self.source_child_location.id,
                "lot_id": self.lot.id,
                "package_id": self.package.id,
                "owner_id": other_owner.id,
                "quantity": 0.0,
            }
        )
        product_control = Quant.create(
            {
                "product_id": other_product.id,
                "location_id": self.source_location.id,
                "quantity": 0.0,
            }
        )
        wizard = self._create_freeform_wizard(products=[self.product])
        selected_quants = self.quant | self.tracked_quant | product_control

        self.assertNotEqual(
            location_control.location_id, self.tracked_quant.location_id
        )
        self.assertNotEqual(lot_control.lot_id, self.tracked_quant.lot_id)
        self.assertNotEqual(
            package_control.package_id, self.tracked_quant.package_id
        )
        self.assertNotEqual(owner_control.owner_id, self.tracked_quant.owner_id)
        self.assertNotEqual(product_control.product_id, self.quant.product_id)
        self.assertFalse(wizard._duplicate_quant_identity_ids(selected_quants))


@tagged("post_install", "-at_install")
class TestAtomicExactReservation(FreeformDeliveryCommon):
    def _wizard_with_selections(self, selections, *, products=None, note=False):
        if products is None:
            products = self.env["product.product"].concat(
                *(quant.product_id for quant in selections)
            )
        wizard = self._create_freeform_wizard(products=products)
        if note:
            wizard.note = note
        wizard.action_next()
        quantities_by_quant = {
            quant.id: quantity for quant, quantity in selections.items()
        }
        for line in wizard.quant_line_ids:
            if line.quant_id.id in quantities_by_quant:
                line.selected_quantity = quantities_by_quant[line.quant_id.id]
        return wizard

    def _set_auxiliary_snapshot(
        self,
        quant,
        *,
        lot_quantity,
        lot_unit_name,
        lot_unit_name_custom=False,
        contract_no=False,
    ):
        quant.write(
            {
                "lot_quantity": lot_quantity,
                "lot_unit_name": lot_unit_name,
                "lot_unit_name_custom": lot_unit_name_custom,
                "contract_no": contract_no,
            }
        )
        quant.invalidate_recordset(
            [
                "lot_quantity",
                "lot_unit_name",
                "lot_unit_name_custom",
                "contract_no",
            ]
        )

    def test_exact_non_fifo_quant_selection(self):
        fifo_quant = self.quant
        selected_quant = self._set_quant_quantity(
            self.product,
            self.source_child_location,
            6.0,
        )
        wizard = self._wizard_with_selections({selected_quant: 4.0})

        action = wizard.action_confirm_delivery()
        picking = wizard.picking_id
        line = picking.move_line_ids

        self.assertEqual(action["res_model"], "stock.picking")
        self.assertEqual(action["res_id"], picking.id)
        self.assertEqual(
            action["views"],
            [(self.env.ref("stock.view_picking_form").id, "form")],
        )
        self.assertEqual(picking.state, "assigned")
        self.assertEqual(picking.move_ids.state, "assigned")
        self.assertFalse(picking.move_ids.picked)
        self.assertFalse(line.picked)
        self.assertEqual(line.location_id, selected_quant.location_id)
        self.assertEqual(line.package_id, selected_quant.package_id)
        self.assertEqual(line.quantity, 4.0)
        self.assertEqual(picking.move_ids.product_uom_qty, 4.0)
        self.assertEqual(fifo_quant.reserved_quantity, 0.0)
        self.assertEqual(selected_quant.reserved_quantity, 4.0)

    def test_multiple_quants_products_and_partial_source_package(self):
        second_untracked_quant = self._set_quant_quantity(
            self.product,
            self.source_child_location,
            4.0,
        )
        selections = {
            self.quant: 2.5,
            second_untracked_quant: 1.5,
            self.tracked_quant: 3.0,
        }
        wizard = self._wizard_with_selections(
            selections,
            products=self.product | self.tracked_product,
        )

        wizard.action_confirm_delivery()
        picking = wizard.picking_id
        demand_by_product = {
            move.product_id.id: move.product_uom_qty for move in picking.move_ids
        }

        self.assertEqual(len(picking.move_ids), 2)
        self.assertEqual(len(picking.move_line_ids), 3)
        self.assertEqual(
            demand_by_product,
            {self.product.id: 4.0, self.tracked_product.id: 3.0},
        )
        tracked_line = picking.move_line_ids.filtered(
            lambda line: line.product_id == self.tracked_product
        )
        self.assertEqual(tracked_line.lot_id, self.lot)
        self.assertEqual(tracked_line.package_id, self.package)
        self.assertEqual(tracked_line.quantity, 3.0)
        self.assertEqual(self.tracked_quant.reserved_quantity, 3.0)
        self.assertEqual(picking.state, "assigned")
        self.assertTrue(all(not line.picked for line in picking.move_line_ids))

    def test_all_local_reservation_policies_keep_exact_identity(self):
        policy_labels = dict(
            self.env["stock.picking.type"]._fields["reservation_method"].selection
        )
        self.assertEqual(set(policy_labels), {"at_confirm", "manual", "by_date"})
        for policy in policy_labels:
            with self.subTest(policy=policy):
                self.picking_type.reservation_method = policy
                policy_location = self.env["stock.location"].create(
                    {
                        "name": f"Free-form Policy {policy}",
                        "usage": "internal",
                        "location_id": self.source_location.id,
                        "company_id": self.company.id,
                    }
                )
                selected_quant = self._set_quant_quantity(
                    self.product,
                    policy_location,
                    2.0,
                )
                wizard = self._wizard_with_selections({selected_quant: 1.0})

                wizard.action_confirm_delivery()

                self.assertEqual(wizard.picking_id.state, "assigned")
                self.assertEqual(
                    wizard.picking_id.move_line_ids.location_id,
                    selected_quant.location_id,
                )
                self.assertEqual(self.quant.reserved_quantity, 0.0)
                self.assertEqual(selected_quant.reserved_quantity, 1.0)

    def test_tracked_and_untracked_lines_are_assigned_and_unpicked(self):
        wizard = self._wizard_with_selections(
            {self.quant: 2.0, self.tracked_quant: 1.0},
            products=self.product | self.tracked_product,
        )

        wizard.action_confirm_delivery()

        lines = wizard.picking_id.move_line_ids
        self.assertEqual(
            lines.mapped("product_id"),
            self.product | self.tracked_product,
        )
        self.assertFalse(
            lines.filtered(
                lambda line: line.product_id == self.product
            ).lot_id
        )
        self.assertEqual(
            lines.filtered(
                lambda line: line.product_id == self.tracked_product
            ).lot_id,
            self.lot,
        )
        self.assertEqual(wizard.picking_id.state, "assigned")
        self.assertTrue(
            all(move.state == "assigned" for move in wizard.picking_id.move_ids)
        )
        self.assertTrue(all(not line.picked for line in lines))

    def test_auxiliary_balance_is_prorated_from_locked_quant(self):
        self._set_auxiliary_snapshot(
            self.tracked_quant,
            lot_quantity=14.0,
            lot_unit_name="roll",
            contract_no="CONTRACT-PRORATE",
        )
        wizard = self._wizard_with_selections({self.tracked_quant: 3.5})

        wizard.action_confirm_delivery()
        line = wizard.picking_id.move_line_ids

        self.assertEqual(line.lot_unit_name, "roll")
        self.assertEqual(line.lot_quantity, 7.0)
        self.assertEqual(line.contract_no, "CONTRACT-PRORATE")

    def test_auxiliary_snapshot_change_rejects_confirmation_without_stock_mutation(self):
        self._set_auxiliary_snapshot(
            self.tracked_quant,
            lot_quantity=14.0,
            lot_unit_name="roll",
            contract_no="CONTRACT-SNAPSHOT",
        )
        wizard = self._wizard_with_selections({self.tracked_quant: 3.5})
        picking_count = self.env["stock.picking"].search_count([])
        move_count = self.env["stock.move"].search_count([])
        reserved_quantity = self.tracked_quant.reserved_quantity
        self._set_auxiliary_snapshot(
            self.tracked_quant,
            lot_quantity=10.0,
            lot_unit_name="roll",
            contract_no="CONTRACT-CHANGED",
        )

        with self.assertRaisesRegex(UserError, "auxiliary metadata has changed"):
            wizard.action_confirm_delivery()

        self.assertEqual(self.env["stock.picking"].search_count([]), picking_count)
        self.assertEqual(self.env["stock.move"].search_count([]), move_count)
        self.assertEqual(self.tracked_quant.reserved_quantity, reserved_quantity)
        self.assertFalse(wizard.picking_id)

    def test_proration_uses_current_locked_quant_balance(self):
        selection = SimpleNamespace(
            selected_quantity=3.5,
            lot_quantity=999.0,
            lot_unit_name="roll",
            lot_unit_name_custom=False,
        )
        quant = SimpleNamespace(
            product_id=self.tracked_product,
            quantity=7.0,
            lot_quantity=14.0,
        )
        wizard = self._create_freeform_wizard(products=[self.tracked_product])

        lot_quantity = wizard._get_explicit_lot_quantity(selection, quant)

        self.assertEqual(lot_quantity, 7.0)

    def test_package_like_auxiliary_fallback_is_one(self):
        self._set_auxiliary_snapshot(
            self.tracked_quant,
            lot_quantity=0.0,
            lot_unit_name="box",
            contract_no="CONTRACT-BOX",
        )
        wizard = self._wizard_with_selections({self.tracked_quant: 2.0})

        wizard.action_confirm_delivery()
        line = wizard.picking_id.move_line_ids

        self.assertEqual(line.lot_unit_name, "box")
        self.assertEqual(line.lot_quantity, 1.0)
        self.assertEqual(line.contract_no, "CONTRACT-BOX")

    def test_continuous_auxiliary_fallback_beats_conflicting_product_default(self):
        self._set_auxiliary_snapshot(
            self.tracked_quant,
            lot_quantity=0.0,
            lot_unit_name="sqm",
            contract_no="CONTRACT-SQM",
        )
        wizard = self._wizard_with_selections({self.tracked_quant: 2.5})
        MoveLine = self.env.registry["stock.move.line"]
        original_defaults = MoveLine._get_default_lot_unit_values

        def conflicting_defaults(records, product):
            defaults = original_defaults(records, product)
            return {
                **defaults,
                "lot_unit_name": "roll",
                "lot_quantity": 99.0,
            }

        with patch.object(
            MoveLine,
            "_get_default_lot_unit_values",
            conflicting_defaults,
        ):
            wizard.action_confirm_delivery()
        line = wizard.picking_id.move_line_ids

        self.assertEqual(line.lot_unit_name, "sqm")
        self.assertEqual(line.lot_quantity, 2.5)
        self.assertEqual(line.contract_no, "CONTRACT-SQM")

    def test_no_auxiliary_unit_blocks_conflicting_product_defaults(self):
        self._set_auxiliary_snapshot(
            self.tracked_quant,
            lot_quantity=0.0,
            lot_unit_name=False,
            lot_unit_name_custom=False,
            contract_no=False,
        )
        wizard = self._wizard_with_selections({self.tracked_quant: 1.5})
        MoveLine = self.env.registry["stock.move.line"]

        with patch.object(
            MoveLine,
            "_get_default_lot_unit_values",
            return_value={
                "lot_unit_name": "custom",
                "lot_unit_name_custom": "Conflicting Default",
                "lot_quantity": 99.0,
            },
        ):
            wizard.action_confirm_delivery()
        line = wizard.picking_id.move_line_ids

        self.assertFalse(line.lot_unit_name)
        self.assertFalse(line.lot_unit_name_custom)
        self.assertEqual(line.lot_quantity, 0.0)
        self.assertFalse(line.contract_no)

    def test_custom_auxiliary_name_and_contract_are_preserved(self):
        self._set_auxiliary_snapshot(
            self.tracked_quant,
            lot_quantity=0.0,
            lot_unit_name="custom",
            lot_unit_name_custom="Pallet Layer",
            contract_no="CONTRACT-CUSTOM",
        )
        wizard = self._wizard_with_selections({self.tracked_quant: 1.5})

        wizard.action_confirm_delivery()
        line = wizard.picking_id.move_line_ids

        self.assertEqual(line.lot_unit_name, "custom")
        self.assertEqual(line.lot_unit_name_custom, "Pallet Layer")
        self.assertEqual(line.lot_quantity, 1.0)
        self.assertEqual(line.contract_no, "CONTRACT-CUSTOM")

    def test_confirmation_does_not_call_public_action_assign(self):
        wizard = self._wizard_with_selections({self.quant: 1.0})
        Picking = self.env.registry["stock.picking"]

        with patch.object(
            Picking,
            "action_assign",
            autospec=True,
            side_effect=AssertionError("action_assign must not be called"),
        ) as action_assign:
            wizard.action_confirm_delivery()

        action_assign.assert_not_called()
        self.assertEqual(wizard.picking_id.state, "assigned")

    def test_regular_user_cannot_reopen_an_idempotent_result(self):
        wizard = self._wizard_with_selections({self.quant: 1.0})
        wizard.action_confirm_delivery()

        with self.assertRaises(AccessError):
            wizard.with_user(self.stock_user).action_confirm_delivery()

    def test_sequential_confirmation_returns_same_picking(self):
        wizard = self._wizard_with_selections({self.quant: 1.0})

        first_action = wizard.action_confirm_delivery()
        first_picking = wizard.picking_id
        first_reserved = self.quant.reserved_quantity
        second_action = wizard.action_confirm_delivery()

        self.assertEqual(second_action["res_id"], first_action["res_id"])
        self.assertEqual(wizard.picking_id, first_picking)
        self.assertEqual(
            self.env["stock.picking"].search_count(
                [("freeform_request_token", "=", wizard.request_token)]
            ),
            1,
        )
        self.assertEqual(self.quant.reserved_quantity, first_reserved)

    def _assert_confirmation_rolled_back(
        self, wizard, quant_a, quant_b, original_a, original_b
    ):
        request_token = wizard.request_token
        self.assertFalse(wizard.picking_id)
        self.env.invalidate_all()
        wizard.invalidate_recordset(["picking_id"])
        quant_a.invalidate_recordset(["reserved_quantity"])
        quant_b.invalidate_recordset(["reserved_quantity"])
        self.assertFalse(
            self.env["stock.picking"].search(
                [("freeform_request_token", "=", request_token)]
            )
        )
        self.assertEqual(quant_a.reserved_quantity, original_a)
        self.assertEqual(quant_b.reserved_quantity, original_b)
        self.assertFalse(wizard.picking_id)

    def _rollback_fixture(self):
        wizard = self._wizard_with_selections(
            {self.quant: 1.0, self.tracked_quant: 1.0},
            products=self.product | self.tracked_product,
        )
        return (
            wizard,
            self.quant.reserved_quantity,
            self.tracked_quant.reserved_quantity,
        )

    def test_failure_after_picking_creation_rolls_back_whole_confirmation(self):
        wizard, original_a, original_b = self._rollback_fixture()
        original = FreeformDeliveryWizard._create_picking

        def create_then_fail(records):
            original(records)
            raise UserError("fault after picking creation")

        with patch.object(FreeformDeliveryWizard, "_create_picking", create_then_fail):
            with self.assertRaisesRegex(UserError, "after picking creation"):
                wizard.action_confirm_delivery()

        self._assert_confirmation_rolled_back(
            wizard, self.quant, self.tracked_quant, original_a, original_b
        )

    def test_failure_after_move_confirmation_rolls_back_whole_confirmation(self):
        wizard, original_a, original_b = self._rollback_fixture()
        original = FreeformDeliveryWizard._assert_no_automatic_reservation

        def assert_then_fail(records, moves):
            original(records, moves)
            raise UserError("fault after move confirmation")

        with patch.object(
            FreeformDeliveryWizard,
            "_assert_no_automatic_reservation",
            assert_then_fail,
        ):
            with self.assertRaisesRegex(UserError, "after move confirmation"):
                wizard.action_confirm_delivery()

        self._assert_confirmation_rolled_back(
            wizard, self.quant, self.tracked_quant, original_a, original_b
        )

    def test_failure_after_first_exact_line_rolls_back_whole_confirmation(self):
        wizard, original_a, original_b = self._rollback_fixture()
        original = FreeformDeliveryWizard._create_exact_move_line
        calls = 0

        def create_first_then_fail(records, values, quant):
            nonlocal calls
            calls += 1
            line = original(records, values, quant)
            if calls == 1:
                raise UserError("fault after first exact line")
            return line

        with patch.object(
            FreeformDeliveryWizard,
            "_create_exact_move_line",
            create_first_then_fail,
        ):
            with self.assertRaisesRegex(UserError, "after first exact line"):
                wizard.action_confirm_delivery()

        self.assertEqual(calls, 1)
        self._assert_confirmation_rolled_back(
            wizard, self.quant, self.tracked_quant, original_a, original_b
        )

    def test_failure_after_final_assertion_rolls_back_whole_confirmation(self):
        wizard, original_a, original_b = self._rollback_fixture()
        original = FreeformDeliveryWizard._assert_selected_quant_reservations

        def assert_then_fail(records, selections, quants, original_reserved):
            original(records, selections, quants, original_reserved)
            raise UserError("fault after final assertion")

        with patch.object(
            FreeformDeliveryWizard,
            "_assert_selected_quant_reservations",
            assert_then_fail,
        ):
            with self.assertRaisesRegex(UserError, "after final assertion"):
                wizard.action_confirm_delivery()

        self._assert_confirmation_rolled_back(
            wizard, self.quant, self.tracked_quant, original_a, original_b
        )

    def test_header_is_revalidated_before_stock_creation(self):
        wizard = self._wizard_with_selections({self.quant: 1.0})
        self.delivery_address.type = "contact"

        with self.assertRaisesRegex(UserError, "delivery address"):
            wizard.action_confirm_delivery()

        self.assertFalse(
            self.env["stock.picking"].search(
                [("freeform_request_token", "=", wizard.request_token)]
            )
        )
        self.assertFalse(wizard.picking_id)

    def test_action_and_picking_metadata(self):
        note = "<p>Handle exact selected stock only.</p>"
        wizard = self._wizard_with_selections({self.quant: 1.0}, note=note)

        action = wizard.action_confirm_delivery()
        picking = wizard.picking_id

        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "stock.picking")
        self.assertEqual(action["res_id"], picking.id)
        self.assertEqual(action["view_mode"], "form")
        self.assertEqual(action["target"], "current")
        self.assertEqual(picking.partner_id, self.delivery_address)
        self.assertEqual(
            picking.freeform_customer_id,
            self.customer.commercial_partner_id,
        )
        self.assertEqual(picking.picking_type_id, self.picking_type)
        self.assertEqual(picking.location_id, self.source_location)
        self.assertEqual(picking.location_dest_id, self.destination_location)
        self.assertEqual(picking.company_id, self.company)
        self.assertEqual(picking.user_id, self.env.user)
        self.assertEqual(picking.note, note)
        self.assertTrue(picking.is_freeform_quant_delivery)
        self.assertEqual(picking.freeform_request_token, wizard.request_token)
        self.assertIn(wizard.request_token, picking.origin)
        self.assertEqual(picking.move_ids.origin, picking.origin)

    def test_exact_assertion_rejects_unrelated_line(self):
        wizard = self._wizard_with_selections({self.quant: 1.0})
        wizard.action_confirm_delivery()
        picking = wizard.picking_id
        move = picking.move_ids
        unrelated_line = self.env["stock.move.line"].create(
            {
                **move._prepare_move_line_vals(quantity=0),
                "quantity": 0.0,
                "picked": False,
            }
        )

        with self.assertRaisesRegex(UserError, "unrelated stock operation lines"):
            wizard._assert_exact_reservation(
                picking,
                wizard._get_positive_selections(),
                picking.move_line_ids - unrelated_line,
            )

    def test_exact_assertion_rejects_picked_line(self):
        wizard = self._wizard_with_selections({self.quant: 1.0})
        wizard.action_confirm_delivery()
        picking = wizard.picking_id

        with (
            self.assertRaisesRegex(UserError, "must remain unpicked"),
            self.cr.savepoint(),
        ):
            picking.move_line_ids.picked = True
            wizard._assert_exact_reservation(
                picking,
                wizard._get_positive_selections(),
                picking.move_line_ids,
            )

    def test_wizard_lock_uses_nowait_and_fetches_exact_row(self):
        cursor = MagicMock()
        cursor.savepoint.return_value = nullcontext()
        cursor.fetchone.return_value = (41, False)
        wizard = SimpleNamespace(
            id=41,
            env=SimpleNamespace(cr=cursor, lang="en_US"),
            ensure_one=MagicMock(),
        )

        FreeformDeliveryWizard._lock_for_confirmation(wizard)

        cursor.savepoint.assert_called_once_with(flush=False)
        cursor.execute.assert_called_once_with(
            "SELECT id, picking_id FROM freeform_delivery_wizard "
            "WHERE id = %s FOR UPDATE NOWAIT",
            [41],
        )
        cursor.fetchone.assert_called_once_with()

    def test_wizard_lock_rejects_wrong_returned_row(self):
        cursor = MagicMock()
        cursor.savepoint.return_value = nullcontext()
        cursor.fetchone.return_value = (99, False)
        wizard = SimpleNamespace(
            id=41,
            env=SimpleNamespace(cr=cursor, lang="en_US"),
            ensure_one=MagicMock(),
        )

        with self.assertRaisesRegex(UserError, "no longer exists"):
            FreeformDeliveryWizard._lock_for_confirmation(wizard)

    def test_wizard_lock_rejects_missing_row(self):
        cursor = MagicMock()
        cursor.savepoint.return_value = nullcontext()
        cursor.fetchone.return_value = None
        wizard = SimpleNamespace(
            id=41,
            env=SimpleNamespace(cr=cursor, lang="en_US"),
            ensure_one=MagicMock(),
        )

        with self.assertRaisesRegex(UserError, "no longer exists"):
            FreeformDeliveryWizard._lock_for_confirmation(wizard)

    def test_wizard_lock_contention_is_translated(self):
        cursor = _RecoveringCursor()
        wizard = SimpleNamespace(
            id=41,
            env=SimpleNamespace(cr=cursor, lang="en_US"),
            ensure_one=MagicMock(),
        )

        with self.assertRaisesRegex(UserError, "already being confirmed"):
            FreeformDeliveryWizard._lock_for_confirmation(wizard)
        cursor.execute("SELECT 1")

        self.assertFalse(cursor.in_failed_transaction)

    def test_wizard_serialization_failure_is_translated_and_cursor_recovers(self):
        cursor = _RecoveringCursor(psycopg2.errors.SerializationFailure)
        wizard = SimpleNamespace(
            id=41,
            env=SimpleNamespace(cr=cursor, lang="en_US"),
            ensure_one=MagicMock(),
        )

        with self.assertRaisesRegex(UserError, "already being confirmed"):
            FreeformDeliveryWizard._lock_for_confirmation(wizard)
        cursor.execute("SELECT 1")

        self.assertFalse(cursor.in_failed_transaction)
