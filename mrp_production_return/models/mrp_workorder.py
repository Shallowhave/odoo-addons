# -*- coding: utf-8 -*-

from odoo import models
from odoo.tools import float_compare


class MrpWorkorder(models.Model):
    _inherit = 'mrp.workorder'

    def _should_defer_partial_backorder(self):
        self.ensure_one()
        rounding = self.product_uom_id.rounding
        return (
            self.is_first_started_wo
            and self.is_last_unfinished_wo
            and self.production_id.picking_type_id.create_backorder in ('ask', 'always')
            and float_compare(
                self.qty_producing,
                self.qty_remaining,
                precision_rounding=rounding,
            ) < 0
        )

    def record_production(self):
        if len(self) != 1 or not self._should_defer_partial_backorder():
            return super().record_production()

        # Odoo normally splits here, before the manufacturing-order wizard.
        # Finish the last work order while keeping the original planned quantity,
        # so pre_button_mark_done can ask whether the remainder should be split.
        self.pre_record_production()
        self.button_finish()
        return self.action_back()
