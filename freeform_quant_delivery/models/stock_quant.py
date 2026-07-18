from odoo import api, models


_IDENTITY_LABELS = (
    "product",
    "company",
    "location",
    "lot",
    "package",
    "owner",
)


def _record_id(record):
    return record.id if record else None


def _complete_quant_identity(
    product_id,
    location_id,
    lot_id=None,
    package_id=None,
    owner_id=None,
):
    """Return the complete Quant identity, deriving company from location."""
    return (
        _record_id(product_id),
        _record_id(location_id.company_id),
        _record_id(location_id),
        _record_id(lot_id),
        _record_id(package_id),
        _record_id(owner_id),
    )


def _quant_record_identity(quant):
    return _complete_quant_identity(
        quant.product_id,
        quant.location_id,
        lot_id=quant.lot_id,
        package_id=quant.package_id,
        owner_id=quant.owner_id,
    )


def _canonical_quant_identity(identity):
    """Return stable text for PostgreSQL's hashtextextended advisory key."""
    if len(identity) != len(_IDENTITY_LABELS):
        raise ValueError("A complete stock Quant identity has six components.")
    return "stock.quant|" + "|".join(
        f"{label}={value if value is not None else '<null>'}"
        for label, value in zip(_IDENTITY_LABELS, identity)
    )


class StockQuant(models.Model):
    _inherit = "stock.quant"

    @api.model
    def _try_advisory_lock_quant_identities(self, identities):
        """Try transaction locks in deterministic order; return False on contention."""
        canonical_identities = sorted(
            {_canonical_quant_identity(identity) for identity in identities}
        )
        for canonical_identity in canonical_identities:
            self.env.cr.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                [canonical_identity],
            )
            if not self.env.cr.fetchone()[0]:
                return False
        return True

    @api.model
    def _acquire_quant_identity_advisory_lock(self, identity):
        """Block until this complete Quant identity is locked for the transaction."""
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [_canonical_quant_identity(identity)],
        )

    @api.model
    def _update_available_quantity(
        self,
        product_id,
        location_id,
        quantity=False,
        reserved_quantity=False,
        lot_id=None,
        package_id=None,
        owner_id=None,
        in_date=None,
    ):
        identity = _complete_quant_identity(
            product_id,
            location_id,
            lot_id=lot_id,
            package_id=package_id,
            owner_id=owner_id,
        )
        self._acquire_quant_identity_advisory_lock(identity)
        return super()._update_available_quantity(
            product_id,
            location_id,
            quantity=quantity,
            reserved_quantity=reserved_quantity,
            lot_id=lot_id,
            package_id=package_id,
            owner_id=owner_id,
            in_date=in_date,
        )
