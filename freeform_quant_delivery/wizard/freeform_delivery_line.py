from odoo import _, fields, models
from odoo.exceptions import UserError


class FreeformDeliveryProductLine(models.TransientModel):
    _name = "freeform.delivery.product.line"
    _description = "Free-form Delivery Product Line"
    _check_company_auto = True

    wizard_id = fields.Many2one(
        "freeform.delivery.wizard",
        required=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        "res.company",
        related="wizard_id.company_id",
        readonly=True,
    )
    product_id = fields.Many2one(
        "product.product",
        required=True,
        check_company=True,
        domain="[('id', 'in', parent.eligible_product_ids)]",
    )
    product_uom_id = fields.Many2one(
        "uom.uom",
        related="product_id.uom_id",
        readonly=True,
    )

    _sql_constraints = [
        (
            "wizard_product_unique",
            "unique(wizard_id, product_id)",
            "Each product can be selected only once.",
        ),
    ]


class FreeformDeliveryQuantLine(models.TransientModel):
    _name = "freeform.delivery.quant.line"
    _description = "Free-form Delivery Quant Line"
    _check_company_auto = True

    wizard_id = fields.Many2one(
        "freeform.delivery.wizard",
        required=True,
        ondelete="cascade",
    )
    quant_id = fields.Many2one(
        "stock.quant",
        required=True,
        ondelete="cascade",
    )
    selected_quantity = fields.Float(string="Selected Quantity")
    product_uom_id = fields.Many2one("uom.uom", readonly=True, required=True)

    product_id = fields.Many2one(
        "product.product", readonly=True, required=True, check_company=True
    )
    location_id = fields.Many2one(
        "stock.location", readonly=True, required=True, check_company=True
    )
    lot_id = fields.Many2one("stock.lot", readonly=True, check_company=True)
    package_id = fields.Many2one(
        "stock.quant.package", readonly=True, check_company=True
    )
    owner_id = fields.Many2one("res.partner", readonly=True, check_company=True)
    company_id = fields.Many2one("res.company", readonly=True, required=True)

    in_date = fields.Datetime(readonly=True)
    on_hand_quantity = fields.Float(
        string="On Hand Quantity", readonly=True, digits="Product Unit of Measure"
    )
    reserved_quantity = fields.Float(
        readonly=True, digits="Product Unit of Measure"
    )
    available_quantity = fields.Float(
        readonly=True, digits="Product Unit of Measure"
    )
    lot_quantity = fields.Float(readonly=True, digits=(16, 2))
    lot_unit_name = fields.Selection(
        [
            ("kg", "Kilogram (kg)"),
            ("roll", "Roll"),
            ("barrel", "Barrel"),
            ("box", "Box"),
            ("bag", "Bag"),
            ("sqm", "Square Meter (m²)"),
            ("piece", "Piece"),
            ("custom", "Custom"),
        ],
        readonly=True,
    )
    lot_unit_name_custom = fields.Char(readonly=True)
    contract_no = fields.Char(readonly=True)
    calculated_length_m = fields.Float(readonly=True, digits=(16, 2))
    actual_length_m = fields.Float(readonly=True, digits=(16, 2))
    actual_area_sqm = fields.Float(readonly=True, digits=(16, 2))
    is_roll_product = fields.Boolean(readonly=True)

    product_width = fields.Integer(readonly=True)
    product_thickness = fields.Integer(readonly=True)
    product_length = fields.Float(readonly=True, digits=(12, 2))
    weight_per_sqm = fields.Float(readonly=True, digits=(12, 2))

    def write(self, values):
        if set(values) - {"selected_quantity"}:
            raise UserError(
                _(
                    "Only the selected quantity can be changed on generated stock rows."
                )
            )
        return super().write(values)

    def _snapshot_identity(self):
        self.ensure_one()
        return (
            self.product_id.id,
            self.location_id.id,
            self.lot_id.id,
            self.package_id.id,
            self.owner_id.id,
            self.company_id.id,
        )

    _sql_constraints = [
        (
            "wizard_quant_unique",
            "unique(wizard_id, quant_id)",
            "Each stock Quant can be selected only once.",
        ),
    ]
