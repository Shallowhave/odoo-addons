import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import psycopg2.errors

from odoo import SUPERUSER_ID, api
from odoo.exceptions import UserError
from odoo.modules.registry import Registry
from odoo.sql_db import db_connect
from odoo.tests.common import BaseCase, get_db_name, tagged
from odoo.tools import mute_logger

from odoo.addons.freeform_quant_delivery.models.stock_quant import (
    _canonical_quant_identity,
)

from .common import FreeformDeliveryCommon


@tagged("-standard", "-at_install", "post_install", "database_breaking")
class TestFreeformDeliveryConcurrency(BaseCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.registry = Registry(get_db_name())
        cls.quant_id = False
        cls.wizard_ids = []
        cls.request_tokens = ()
        cls.cleanup_ids = {}
        cls.addClassCleanup(cls.cleanUpClass)
        with cls.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            fixture = cls._create_committed_fixture(env)
            cls.quant_id = fixture["quant"].id
            cls.wizard_ids = fixture["wizards"].ids
            cls.request_tokens = tuple(fixture["wizards"].mapped("request_token"))
            cls.cleanup_ids = {
                model: records.ids for model, records in fixture["cleanup"].items()
            }

    @classmethod
    def _create_committed_fixture(cls, env):
        company = env.company
        suffix = uuid.uuid4().hex
        customer = env["res.partner"].create(
            {
                "name": f"FFD Concurrency Customer {suffix}",
                "company_id": company.id,
            }
        )
        delivery_address = env["res.partner"].create(
            {
                "name": f"FFD Concurrency Delivery {suffix}",
                "parent_id": customer.id,
                "type": "delivery",
                "company_id": company.id,
            }
        )
        source = env["stock.location"].create(
            {
                "name": f"FFD Concurrency Source {suffix}",
                "usage": "internal",
                "company_id": company.id,
            }
        )
        destination = env["stock.location"].create(
            {
                "name": f"FFD Concurrency Destination {suffix}",
                "usage": "customer",
                "company_id": False,
            }
        )
        picking_type = env["stock.picking.type"].with_company(company).create(
            {
                "name": f"FFD Concurrency Deliveries {suffix}",
                "sequence_code": f"F{suffix[:3].upper()}",
                "code": "outgoing",
                "company_id": company.id,
                "default_location_src_id": source.id,
                "default_location_dest_id": destination.id,
            }
        )

        # Reuse the concrete factories exercised by the transaction-based suite.
        factory = SimpleNamespace(
            env=env,
            company=company,
            picking_type=picking_type,
            customer=customer,
            delivery_address=delivery_address,
        )
        product = FreeformDeliveryCommon._create_storable_product.__func__(
            factory,
            name=f"FFD Concurrency Product {suffix}",
            tracking="lot",
        )
        lot = env["stock.lot"].with_company(company).create(
            {
                "name": f"FFD-CONCURRENCY-LOT-{suffix}",
                "product_id": product.id,
                "company_id": company.id,
            }
        )
        quant = FreeformDeliveryCommon._set_quant_quantity.__func__(
            factory,
            product,
            source,
            10.0,
            lot=lot,
        )
        wizards = env["freeform.delivery.wizard"]
        for _index in range(2):
            wizard = FreeformDeliveryCommon._create_freeform_wizard.__func__(
                factory,
                products=product,
            )
            wizard = wizard.with_company(company)
            wizard.action_next()
            wizard.quant_line_ids.filtered(
                lambda line: line.quant_id == quant
            ).selected_quantity = 6.0
            wizards |= wizard

        return {
            "quant": quant,
            "wizards": wizards[0] | wizards[1],
            "cleanup": {
                # Insertion order is reverse dependency order for unlinking.
                "freeform.delivery.quant.line": wizards.quant_line_ids,
                "freeform.delivery.product.line": wizards.product_line_ids,
                "freeform.delivery.wizard": wizards,
                "stock.quant": quant,
                "stock.lot": lot,
                "product.product": product,
                "stock.picking.type": picking_type,
                "ir.sequence": picking_type.sequence_id,
                "stock.location": source | destination,
                "res.partner": delivery_address | customer,
            },
        }

    @classmethod
    def _request_tokens(cls, _env):
        return cls.request_tokens

    @classmethod
    def _matching_quants(cls, env):
        quant = env["stock.quant"].browse(cls.quant_id).exists()
        if not quant:
            return env["stock.quant"]
        return env["stock.quant"].sudo().search(
            [
                ("product_id", "=", quant.product_id.id),
                ("company_id", "=", quant.company_id.id),
                ("location_id", "=", quant.location_id.id),
                ("lot_id", "=", quant.lot_id.id),
                ("package_id", "=", quant.package_id.id),
                ("owner_id", "=", quant.owner_id.id),
            ]
        )

    @classmethod
    def _delete_generated_pickings(cls, env):
        pickings = env["stock.picking"].search(
            [("freeform_request_token", "in", cls._request_tokens(env))]
        )
        if pickings:
            pickings.move_ids._action_cancel()
            pickings.unlink()

    @classmethod
    def cleanUpClass(cls):
        if not getattr(cls, "registry", None):
            return
        with cls.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            cls._delete_generated_pickings(env)
            matching_quants = cls._matching_quants(env)
            for model_name, ids in cls.cleanup_ids.items():
                records = env[model_name].browse(ids).exists()
                if records:
                    records.unlink()
            # A failed implementation may have created an untracked duplicate Quant;
            # remove it only during unconditional class cleanup.
            leaked_quants = matching_quants.exists()
            if leaked_quants:
                leaked_quants.unlink()

    def setUp(self):
        super().setUp()
        self._reset_fixture()
        self.addCleanup(self._reset_fixture)

    def _reset_fixture(self):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            self._delete_generated_pickings(env)

        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            wizard_records = env["freeform.delivery.wizard"].browse(self.wizard_ids)
            wizard_records.invalidate_recordset(["picking_id"])
            quant = env["stock.quant"].browse(self.quant_id)
            quant.invalidate_recordset(["reserved_quantity"])
            self.assertFalse(wizard_records.mapped("picking_id"))
            self.assertEqual(quant.reserved_quantity, 0.0)
            self.assertEqual(self._matching_quants(env), quant)

    def _run_confirmations(self, wizard_ids):
        barrier = threading.Barrier(2)

        def run(wizard_id):
            with self.registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                wizard = env["freeform.delivery.wizard"].browse(wizard_id)
                wizard = wizard.with_company(wizard.company_id)
                wizard.request_token
                barrier.wait(timeout=5)
                try:
                    action = wizard.action_confirm_delivery()
                except UserError as error:
                    return ("error", str(error))
                return ("success", action["res_id"])

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run, wizard_id) for wizard_id in wizard_ids]
            return [future.result(timeout=10) for future in futures]

    def test_ordinary_quant_update_waits_for_freeform_identity_lock(self):
        database = get_db_name()
        with db_connect(database).cursor() as holder_cr:
            holder_cr.execute(
                """
                    SELECT quant.product_id,
                           location.company_id,
                           quant.location_id,
                           quant.lot_id,
                           quant.package_id,
                           quant.owner_id
                      FROM stock_quant AS quant
                      JOIN stock_location AS location
                        ON location.id = quant.location_id
                     WHERE quant.id = %s
                """,
                [self.quant_id],
            )
            identity = holder_cr.fetchone()
            self.assertEqual(len(identity), 6)
            holder_cr.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                [_canonical_quant_identity(identity)],
            )

            with mute_logger("odoo.sql_db"), self.assertRaises(
                psycopg2.errors.LockNotAvailable
            ):
                with db_connect(database).cursor() as updater_cr:
                    updater_cr.execute("SET LOCAL lock_timeout = '1s'")
                    updater_env = api.Environment(updater_cr, SUPERUSER_ID, {})
                    quant = updater_env["stock.quant"].browse(self.quant_id)
                    updater_env["stock.quant"]._update_reserved_quantity(
                        quant.product_id,
                        quant.location_id,
                        1.0,
                        lot_id=quant.lot_id,
                        package_id=quant.package_id,
                        owner_id=quant.owner_id,
                    )

            holder_cr.execute(
                "SELECT reserved_quantity FROM stock_quant WHERE id = %s",
                [self.quant_id],
            )
            self.assertEqual(holder_cr.fetchone(), (0.0,))

        with db_connect(database).cursor() as updater_cr:
            updater_env = api.Environment(updater_cr, SUPERUSER_ID, {})
            quant = updater_env["stock.quant"].browse(self.quant_id)
            updater_env["stock.quant"]._update_reserved_quantity(
                quant.product_id,
                quant.location_id,
                1.0,
                lot_id=quant.lot_id,
                package_id=quant.package_id,
                owner_id=quant.owner_id,
            )
            quant.invalidate_recordset(["reserved_quantity"])
            self.assertEqual(self._matching_quants(updater_env), quant)
            self.assertEqual(quant.reserved_quantity, 1.0)
            updater_env["stock.quant"]._update_reserved_quantity(
                quant.product_id,
                quant.location_id,
                -1.0,
                lot_id=quant.lot_id,
                package_id=quant.package_id,
                owner_id=quant.owner_id,
            )
            quant.invalidate_recordset(["reserved_quantity"])
            self.assertEqual(quant.reserved_quantity, 0.0)

    def test_same_quant_competing_wizards_do_not_substitute_or_duplicate(self):
        results = self._run_confirmations(self.wizard_ids)
        successes = [result for result in results if result[0] == "success"]
        errors = [result for result in results if result[0] == "error"]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)
        self.assertRegex(
            errors[0][1],
            "being processed|exceeds the latest available quantity",
        )

        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            quant = env["stock.quant"].browse(self.quant_id)
            pickings = env["stock.picking"].search(
                [("freeform_request_token", "in", self._request_tokens(env))]
            )
            move_line = pickings.move_line_ids.ensure_one()
            self.assertEqual(len(pickings), 1)
            self.assertEqual(self._matching_quants(env), quant)
            self.assertEqual(quant.reserved_quantity, 6.0)
            self.assertEqual(move_line.product_id, quant.product_id)
            self.assertEqual(move_line.company_id, quant.company_id)
            self.assertEqual(move_line.location_id, quant.location_id)
            self.assertEqual(move_line.lot_id, quant.lot_id)
            self.assertEqual(move_line.package_id, quant.package_id)
            self.assertEqual(move_line.owner_id, quant.owner_id)

    def test_same_wizard_concurrent_confirmation_creates_one_picking(self):
        wizard_id = self.wizard_ids[0]
        results = self._run_confirmations([wizard_id, wizard_id])
        successes = [result for result in results if result[0] == "success"]
        errors = [result for result in results if result[0] == "error"]
        self.assertGreaterEqual(len(successes), 1)
        if len(successes) == 2:
            self.assertEqual(successes[0][1], successes[1][1])
        else:
            self.assertEqual(len(errors), 1)
            self.assertRegex(errors[0][1], "already being confirmed")

        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            wizard = env["freeform.delivery.wizard"].browse(wizard_id)
            pickings = env["stock.picking"].search(
                [("freeform_request_token", "=", wizard.request_token)]
            )
            self.assertEqual(len(pickings), 1)
            self.assertEqual(wizard.picking_id, pickings)
            self.assertEqual(
                env["stock.quant"].browse(self.quant_id).reserved_quantity,
                6.0,
            )

    def test_unique_request_token_is_database_backstop(self):
        wizard_id = self.wizard_ids[0]
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            wizard = env["freeform.delivery.wizard"].browse(wizard_id)
            wizard.with_company(wizard.company_id).action_confirm_delivery()

        with mute_logger("odoo.sql_db"), self.assertRaises(
            psycopg2.errors.UniqueViolation
        ):
            with self.registry.cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                wizard = env["freeform.delivery.wizard"].browse(wizard_id)
                picking = env["stock.picking"].search(
                    [("freeform_request_token", "=", wizard.request_token)]
                ).ensure_one()
                env["stock.picking"].with_company(picking.company_id).create(
                    {
                        "picking_type_id": picking.picking_type_id.id,
                        "location_id": picking.location_id.id,
                        "location_dest_id": picking.location_dest_id.id,
                        "company_id": picking.company_id.id,
                        "freeform_request_token": wizard.request_token,
                    }
                )
