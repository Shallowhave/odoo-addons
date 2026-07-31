from odoo import models
from odoo.tools.float_utils import float_compare


class StockMove(models.Model):
    _inherit = "stock.move"

    def _sync_freeform_demand_from_move_lines(self):
        """Align planned demand after the operation editor replaces its lines.

        Direct move-line writes are intentionally excluded: barcode quantity
        updates are execution against the plan and must preserve backorders.
        """
        moves = self.filtered(
            lambda move: move.picking_id.is_freeform_quant_delivery
            and move.state not in ("done", "cancel")
        )
        changed_moves = self.env["stock.move"]
        for move in moves:
            demand = sum(
                line.product_uom_id._compute_quantity(
                    line.quantity,
                    move.product_uom,
                    round=False,
                )
                for line in move.move_line_ids
            )
            if float_compare(
                demand,
                move.product_uom_qty,
                precision_rounding=move.product_uom.rounding,
            ) == 0:
                continue
            move.with_context(
                do_not_unreserve=True,
                skip_freeform_demand_sync=True,
            ).write({"product_uom_qty": demand})
            changed_moves |= move
        changed_moves._recompute_state()

    def write(self, values):
        result = super().write(values)
        if (
            "move_line_ids" in values
            and not self.env.context.get("skip_freeform_demand_sync")
        ):
            self._sync_freeform_demand_from_move_lines()
        return result
