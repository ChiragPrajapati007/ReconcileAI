"""
Quantity reconciliation for matched invoice-vs-PO lines.

Formula
───────
  remaining_before = max(0, po_qty - previously_invoiced_qty)
  overbilled       = max(0, current_qty - remaining_before)

The ``max(0, …)`` on remaining_before prevents confusing negative values
when the PO line is already over-invoiced before the current invoice.  In
that case ``is_already_over_invoiced`` is set to True so the engine can
report it correctly.

Ledger interaction
──────────────────
The engine loads all InvoiceLineLedger entries for a given PO item,
**excluding** the current invoice (for idempotency — if we are re-running,
the old ledger entries will have been deleted already).
"""
from __future__ import annotations

from decimal import Decimal

from app.services.reconciliation.types import QuantityAnalysis


def analyze_quantity(
    po_item_id,
    invoice_item_id,
    po_quantity: Decimal,
    current_invoice_quantity: Decimal,
    previously_invoiced: Decimal,
    quantity_tolerance: Decimal,
) -> QuantityAnalysis:
    """Analyse whether the current invoice quantity causes overbilling.

    Parameters
    ----------
    po_item_id            : UUID of the PO line item
    invoice_item_id       : UUID of the invoice line item
    po_quantity           : total ordered quantity on the PO line
    current_invoice_quantity : quantity on the current invoice line
    previously_invoiced   : SUM(quantity_invoiced) from ledger for *other* invoices
    quantity_tolerance     : allowable tolerance from settings

    Returns
    -------
    QuantityAnalysis dataclass.
    """
    is_already_over_invoiced = previously_invoiced > po_quantity

    # Clamp remaining to 0 when already over-invoiced
    remaining_before = max(Decimal("0"), po_quantity - previously_invoiced)
    remaining_after = remaining_before - current_invoice_quantity

    raw_overbilled = current_invoice_quantity - remaining_before
    overbilled = max(Decimal("0"), raw_overbilled)

    is_overbilled = overbilled > quantity_tolerance

    return QuantityAnalysis(
        po_item_id=po_item_id,
        invoice_item_id=invoice_item_id,
        po_quantity=po_quantity,
        previously_invoiced=previously_invoiced,
        current_invoice_quantity=current_invoice_quantity,
        remaining_before=remaining_before,
        remaining_after=remaining_after,
        overbilled=overbilled,
        is_overbilled=is_overbilled,
        is_already_over_invoiced=is_already_over_invoiced,
    )
