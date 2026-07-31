from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_round


FILM_DENSITY_G_CM3 = 1.4
PRODUCT_AREA_UOM_NAME = "平米"


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
    selected = fields.Boolean(string="Use Stock")
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
    roll_weight_kg = fields.Float(
        string="Single Roll Weight (kg)",
        compute="_compute_roll_weight_kg",
        readonly=True,
        digits=(16, 2),
        help=(
            "Theoretical roll weight calculated from available square meters, "
            "film thickness, and a density of 1.4 g/cm3."
        ),
    )
    selected_weight_kg = fields.Float(
        string="Selected Weight (kg)",
        compute="_compute_selected_weight_kg",
        readonly=True,
        digits=(16, 2),
    )
    show_roll_weight = fields.Boolean(compute="_compute_roll_weight_kg")
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

    @api.depends(
        "actual_area_sqm",
        "available_quantity",
        "is_roll_product",
        "product_thickness",
        "product_uom_id",
        "product_uom_id.factor",
        "product_uom_id.name",
    )
    def _compute_roll_weight_kg(self):
        square_meter = self.env.ref(
            "uom.uom_square_meter",
            raise_if_not_found=False,
        )
        area_uom_by_category = {}
        if square_meter:
            area_uom_by_category[square_meter.category_id.id] = square_meter

        custom_categories = self.product_uom_id.category_id.filtered(
            lambda category: category.id not in area_uom_by_category
        )
        if custom_categories:
            # Quick unit setup creates this explicit UoM in each product category.
            product_area_uoms = (
                self.env["uom.uom"]
                .with_context(lang="zh_CN")
                .search(
                    [
                        ("category_id", "in", custom_categories.ids),
                        ("name", "=", PRODUCT_AREA_UOM_NAME),
                    ],
                    order="category_id, id",
                )
            )
            for area_uom in product_area_uoms:
                area_uom_by_category.setdefault(area_uom.category_id.id, area_uom)

        for line in self:
            area_uom = area_uom_by_category.get(line.product_uom_id.category_id.id)
            if area_uom:
                area_sqm = line.product_uom_id._compute_quantity(
                    line.available_quantity,
                    area_uom,
                    round=False,
                )
            elif line.is_roll_product:
                area_sqm = line.actual_area_sqm
            else:
                area_sqm = 0.0

            line.show_roll_weight = bool(area_sqm > 0 and line.product_thickness > 0)
            if not line.show_roll_weight:
                line.roll_weight_kg = 0.0
                continue
            line.roll_weight_kg = round(
                area_sqm
                * line.product_thickness
                * FILM_DENSITY_G_CM3
                / 1000.0,
                2,
            )

    @api.depends(
        "available_quantity",
        "roll_weight_kg",
        "selected",
        "selected_quantity",
    )
    def _compute_selected_weight_kg(self):
        for line in self:
            if (
                not line.selected
                or line.selected_quantity <= 0
                or line.available_quantity <= 0
            ):
                line.selected_weight_kg = 0.0
                continue
            line.selected_weight_kg = float_round(
                line.roll_weight_kg
                * line.selected_quantity
                / line.available_quantity,
                precision_digits=2,
            )

    def _apply_selection_values(self, values):
        values = dict(values)
        if "selected" in values:
            if values["selected"]:
                values.setdefault(
                    "selected_quantity",
                    self.selected_quantity or self.available_quantity,
                )
            else:
                values["selected_quantity"] = 0.0
        elif "selected_quantity" in values:
            values["selected"] = bool(values["selected_quantity"])
        return values

    def write(self, values):
        if set(values) - {"selected", "selected_quantity"}:
            raise UserError(
                _(
                    "Only the stock selection and selected quantity can be changed "
                    "on generated stock rows."
                )
            )
        for line in self:
            super(FreeformDeliveryQuantLine, line).write(
                line._apply_selection_values(values)
            )
        return True

    @api.onchange("selected")
    def _onchange_selected(self):
        for line in self:
            if line.selected:
                if not line.selected_quantity:
                    line.selected_quantity = line.available_quantity
            else:
                line.selected_quantity = 0.0

    @api.onchange("selected_quantity")
    def _onchange_selected_quantity(self):
        for line in self:
            line.selected = bool(line.selected_quantity)

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
