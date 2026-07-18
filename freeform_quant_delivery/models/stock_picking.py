from odoo import fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    is_freeform_quant_delivery = fields.Boolean(index=True, copy=False)
    freeform_customer_id = fields.Many2one(
        "res.partner",
        string="Free-form Customer",
        check_company=True,
        copy=False,
    )
    freeform_request_token = fields.Char(index=True, copy=False)

    _sql_constraints = [
        (
            "freeform_request_token_unique",
            "unique(freeform_request_token)",
            "A free-form delivery request can create only one transfer.",
        ),
    ]
