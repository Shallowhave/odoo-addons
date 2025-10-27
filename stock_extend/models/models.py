# -*- coding: utf-8 -*-

from odoo import models, fields, api
from re import findall as regex_findall

class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    lot_weight = fields.Float(string='重量(kg)')
    lot_barrels = fields.Integer(string='桶数')


class StockMove(models.Model):
    _inherit = 'stock.move'
    lot_weight = fields.Float(string='重量(kg)', compute='_compute_lot_weight', )
    
    def _compute_lot_weight(self):
        for move in self:
            move.lot_weight = sum(move.move_line_ids.mapped('lot_weight'))

    @api.model
    def split_lots(self, lots):
        breaking_char = '\n'
        separation_char = '\t'
        options = False

        if not lots:
            return []  # Skip if the `lot_name` doesn't contain multiple values.

        # Checks the lines and prepares the move lines' values.
        split_lines = lots.split(breaking_char)
        split_lines = list(filter(None, split_lines))
        move_lines_vals = []
        for lot_text in split_lines:
            move_line_vals = {
                'lot_name': lot_text,
                'quantity': 1,
            }
            # Semicolons are also used for separation but for convenience we
            # replace them to work only with tabs.
            lot_text_parts = lot_text.replace(';', separation_char).split(separation_char)
            options = options or self._get_formating_options(lot_text_parts[1:])
            for extra_string in lot_text_parts[1]:
                field_data = self._convert_string_into_field_data(extra_string, options)
                if field_data:
                    lot_text = lot_text_parts[0]
                    lot_weight  = float(lot_text_parts[-1])
                    if field_data == "ignore":
                        # Got an unusable data for this move, updates only the lot_name part.
                        move_line_vals.update(lot_name=lot_text, lot_weight=lot_weight)
                    else:
                        move_line_vals.update(**field_data, lot_name=lot_text,lot_weight=lot_weight)
                else:
                    # At least this part of the string is erronous and can't be converted,
                    # don't try to guess and simply use the full string as the lot name.
                    move_line_vals['lot_name'] = lot_text
                    break
            move_lines_vals.append(move_line_vals)
        return move_lines_vals

