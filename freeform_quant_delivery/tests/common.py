from odoo import Command
from odoo.tests import TransactionCase, new_test_user


class FreeformDeliveryCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.stock_manager = cls._create_stock_user(manager=True)
        cls.manager = cls.stock_manager
        cls.stock_user = cls._create_stock_user(manager=False)

        cls.customer = cls.env["res.partner"].create(
            {"name": "Free-form Delivery Customer", "company_id": cls.company.id}
        )
        cls.delivery_address = cls.env["res.partner"].create(
            {
                "name": "Free-form Delivery Address",
                "parent_id": cls.customer.id,
                "type": "delivery",
                "company_id": cls.company.id,
            }
        )
        cls.source_location = cls.env["stock.location"].create(
            {
                "name": "Free-form Source",
                "usage": "internal",
                "company_id": cls.company.id,
            }
        )
        cls.source_child_location = cls.env["stock.location"].create(
            {
                "name": "Free-form Source Child",
                "usage": "internal",
                "location_id": cls.source_location.id,
                "company_id": cls.company.id,
            }
        )
        cls.destination_location = cls.env["stock.location"].create(
            {
                "name": "Free-form Customer Destination",
                "usage": "customer",
                "company_id": False,
            }
        )
        cls.picking_type = cls.env["stock.picking.type"].create(
            {
                "name": "Free-form Deliveries",
                "sequence_code": "FFD",
                "code": "outgoing",
                "company_id": cls.company.id,
                "default_location_src_id": cls.source_location.id,
                "default_location_dest_id": cls.destination_location.id,
                "use_existing_lots": True,
                "use_create_lots": False,
            }
        )

        cls.product = cls._create_storable_product(name="Free-form Product")
        cls.tracked_product = cls._create_storable_product(
            name="Free-form Tracked Product", tracking="lot"
        )
        cls.lot = cls.env["stock.lot"].create(
            {
                "name": "FFD-LOT-001",
                "product_id": cls.tracked_product.id,
                "company_id": cls.company.id,
            }
        )
        cls.package = cls.env["stock.quant.package"].create(
            {"name": "FFD-PACKAGE-001"}
        )
        cls.quant = cls._set_quant_quantity(
            cls.product,
            cls.source_location,
            12.0,
        )
        cls.tracked_quant = cls._set_quant_quantity(
            cls.tracked_product,
            cls.source_child_location,
            7.0,
            lot=cls.lot,
            package=cls.package,
        )

    @classmethod
    def _create_stock_user(cls, *, manager):
        """Return a company-scoped stock manager or stock user."""
        role = "manager" if manager else "user"
        return new_test_user(
            cls.env,
            login=f"freeform_stock_{role}",
            groups=(
                "stock.group_stock_manager"
                if manager
                else "stock.group_stock_user"
            ),
            company_id=cls.company.id,
            company_ids=[Command.link(cls.company.id)],
        )

    @classmethod
    def _create_storable_product(cls, *, name, tracking="none", uom=None):
        """Return a current-company product.product with is_storable=True."""
        values = {
            "name": name,
            "is_storable": True,
            "tracking": tracking,
            "company_id": cls.company.id,
        }
        if uom:
            values.update({"uom_id": uom.id, "uom_po_id": uom.id})
        return cls.env["product.product"].create(values)

    @classmethod
    def _set_quant_quantity(
        cls, product, location, quantity, *, lot=None, package=None
    ):
        """Use stock.quant._update_available_quantity and return the exact Quant."""
        cls.env["stock.quant"]._update_available_quantity(
            product,
            location,
            quantity,
            lot_id=lot,
            package_id=package,
        )
        return cls.env["stock.quant"]._gather(
            product,
            location,
            lot_id=lot,
            package_id=package,
            owner_id=False,
            strict=True,
        ).ensure_one()

    @classmethod
    def _create_freeform_wizard(cls, *, products, user=None):
        """Return a valid Step-1 wizard for the common outgoing type/customer."""
        Wizard = cls.env["freeform.delivery.wizard"]
        if user:
            Wizard = Wizard.with_user(user)
        return Wizard.create(
            {
                "company_id": cls.company.id,
                "picking_type_id": cls.picking_type.id,
                "customer_id": cls.customer.id,
                "delivery_address_id": cls.delivery_address.id,
                "product_line_ids": [
                    Command.create({"product_id": product.id}) for product in products
                ],
            }
        )
