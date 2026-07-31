from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMrpFinishedMoveLineQuantity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env["product.product"].create(
            {
                "name": "Lot-tracked Film Roll",
                "is_storable": True,
                "tracking": "lot",
            }
        )
        cls.production = cls.env["mrp.production"].create(
            {
                "product_id": cls.product.id,
                "product_qty": 2000.0,
                "product_uom_id": cls.product.uom_id.id,
            }
        )
        cls.production.action_confirm()
        cls.finished_move = cls.production.move_finished_ids.filtered(
            lambda move: move.product_id == cls.product
        ).ensure_one()
        cls.lot = cls.env["stock.lot"].create(
            {
                "name": "FILM-ROLL-OVERPRODUCTION",
                "product_id": cls.product.id,
                "company_id": cls.production.company_id.id,
            }
        )

    def _get_or_create_finished_move_line(self, production, lot, quantity, uom):
        move = production.move_finished_ids.filtered(
            lambda finished_move: finished_move.product_id == production.product_id
        ).ensure_one()
        move_line = move.move_line_ids[:1]
        if not move_line:
            move_line = self.env["stock.move.line"].create(
                {
                    "move_id": move.id,
                    "product_id": production.product_id.id,
                    "product_uom_id": uom.id,
                    "location_id": move.location_id.id,
                    "location_dest_id": move.location_dest_id.id,
                }
            )
        move_line.write(
            {
                "product_uom_id": uom.id,
                "lot_id": lot.id,
                "quantity": quantity,
            }
        )
        return move, move_line

    def test_overproduction_stays_on_single_lot_move_line(self):
        finished_move, move_line = self._get_or_create_finished_move_line(
            self.production,
            self.lot,
            2000.0,
            self.product.uom_id,
        )
        self.production.with_context(
            skip_mrp_auto_lot_selectable_check=True
        ).write(
            {
                "lot_producing_id": self.lot.id,
                "qty_producing": 2020.0,
            }
        )

        self.production._post_inventory(cancel_backorder=True)

        self.assertEqual(finished_move.state, "done")
        self.assertEqual(finished_move.move_line_ids, move_line)
        self.assertEqual(move_line.lot_id, self.lot)
        self.assertEqual(move_line.quantity, 2020.0)

    def test_overproduction_converts_to_the_existing_line_uom(self):
        dozen = self.env.ref("uom.product_uom_dozen")
        production = self.env["mrp.production"].create(
            {
                "product_id": self.product.id,
                "product_qty": 24.0,
                "product_uom_id": self.product.uom_id.id,
            }
        )
        production.action_confirm()
        lot = self.env["stock.lot"].create(
            {
                "name": "FILM-ROLL-DOZEN-UOM",
                "product_id": self.product.id,
                "company_id": production.company_id.id,
            }
        )
        finished_move, move_line = self._get_or_create_finished_move_line(
            production,
            lot,
            2.0,
            dozen,
        )
        production.with_context(
            skip_mrp_auto_lot_selectable_check=True
        ).write(
            {
                "lot_producing_id": lot.id,
                "qty_producing": 30.0,
            }
        )

        production._post_inventory(cancel_backorder=True)

        self.assertEqual(finished_move.move_line_ids, move_line)
        self.assertEqual(move_line.quantity, 2.5)

    def test_non_mrp_move_keeps_native_overproduction_split(self):
        move = self.env["stock.move"].create(
            {
                "name": "Regular lot move",
                "product_id": self.product.id,
                "product_uom_qty": 2000.0,
                "product_uom": self.product.uom_id.id,
                "location_id": self.finished_move.location_id.id,
                "location_dest_id": self.finished_move.location_dest_id.id,
                "company_id": self.production.company_id.id,
            }
        )
        self.env["stock.move.line"].create(
            {
                "move_id": move.id,
                "product_id": self.product.id,
                "product_uom_id": self.product.uom_id.id,
                "location_id": move.location_id.id,
                "location_dest_id": move.location_dest_id.id,
                "lot_id": self.lot.id,
                "quantity": 2000.0,
            }
        )

        move.quantity = 2020.0

        self.assertEqual(len(move.move_line_ids), 2)
        self.assertEqual(sum(move.move_line_ids.mapped("quantity")), 2020.0)
