"""
Unit-price reconciliation for matched invoice-vs-PO lines.

Tolerance
─────────
A price mismatch is flagged when:

    abs(difference_percent) > PRICE_TOLERANCE_PERCENT

This handles both overcharging (invoice > PO) and undercharging (invoice < PO).
The sign convention follows the project standard: positive = overcharge.
"""
from __future__ import annotations

from decimal import Decimal

from app.services.reconciliation.types import (
    PriceComparison,
    round_amount,
    round_percent,
)


def compare_price(
    po_item_id,
    invoice_item_id,
    po_unit_price: Decimal,
    invoice_unit_price: Decimal,
    invoice_quantity: Decimal,
    price_tolerance_percent: Decimal,
) -> PriceComparison:
    """Compare unit prices and compute financial impact.

    Parameters
    ----------
    po_item_id             : UUID of the PO line
    invoice_item_id        : UUID of the invoice line
    po_unit_price          : expected unit price from PO
    invoice_unit_price     : actual unit price from invoice
    invoice_quantity       : quantity on the invoice line
    price_tolerance_percent: allowable % difference (from settings)

    Returns
    -------
    PriceComparison dataclass.
    """
    difference_per_unit = round_amount(invoice_unit_price - po_unit_price)

    if po_unit_price != Decimal("0"):
        difference_percent = round_percent(
            (difference_per_unit / po_unit_price) * Decimal("100")
        )
    else:
        # PO price is zero — any non-zero invoice price is an anomaly
        difference_percent = (
            Decimal("100.0000") if invoice_unit_price > Decimal("0")
            else Decimal("0")
        )

    financial_impact = round_amount(difference_per_unit * invoice_quantity)

    is_mismatch = abs(difference_percent) > price_tolerance_percent

    return PriceComparison(
        po_item_id=po_item_id,
        invoice_item_id=invoice_item_id,
        po_unit_price=po_unit_price,
        invoice_unit_price=invoice_unit_price,
        difference_per_unit=difference_per_unit,
        difference_percent=difference_percent,
        financial_impact=financial_impact,
        is_mismatch=is_mismatch,
    )
