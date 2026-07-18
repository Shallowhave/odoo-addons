from psycopg2 import IntegrityError

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFreeformPickingMetadata(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.picking_type = cls.env["stock.picking.type"].search(
            [("code", "=", "outgoing"), ("company_id", "=", cls.env.company.id)],
            limit=1,
        )

    def _picking_values(self, **extra_values):
        return {
            "picking_type_id": self.picking_type.id,
            "location_id": self.picking_type.default_location_src_id.id,
            "location_dest_id": self.picking_type.default_location_dest_id.id,
            "is_freeform_quant_delivery": True,
            **extra_values,
        }

    def test_request_token_is_unique(self):
        values = self._picking_values(
            freeform_request_token="freeform-test-token",
        )
        self.env["stock.picking"].create(values)
        with self.assertRaises(IntegrityError), self.cr.savepoint():
            self.env["stock.picking"].create(values)

    def test_empty_request_tokens_are_allowed(self):
        pickings = self.env["stock.picking"].create(
            [self._picking_values(), self._picking_values()]
        )
        self.assertEqual(len(pickings), 2)
