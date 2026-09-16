"""
Canonical-to-Invoice population service.

This is the Phase 6 bridge between Phase 5 extraction and Phase 3 reconciliation.

Responsibilities:
  - Load the latest high-confidence InvoiceExtraction for an invoice
  - Enforce the confidence gate (AUTO / REVIEW / BLOCKED)
  - Write Invoice header fields from canonical extraction data
  - Write InvoiceItem rows from canonical line items (replace-on-repopulate)
  - Resolve PO by extracted po_number and link purchase_order_id
  - Update Invoice.status to UNDER_AUDIT when eligible

The Phase 3 reconciliation engine (reconcile_invoice) is NOT called here.
The caller is responsible for calling reconcile_invoice after population.

Decimal safety:
  All monetary values stay as Decimal through this module.
  SQLAlchemy Numeric columns accept Decimal natively.

Idempotency:
  Re-running population on the same invoice replaces existing InvoiceItems
  and updates Invoice header fields.  It is safe to call multiple times.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import InvoiceStatus
from app.models.invoice import Invoice, InvoiceItem, InvoiceExtraction
from app.models.purchase_order import PurchaseOrder
from app.services.extraction.schemas import (
    CanonicalExtraction,
    CanonicalLineItem,
    GateStatus,
)
from app.services.extraction.correction import get_corrected_canonical

logger = logging.getLogger(__name__)


# ── Public result type ────────────────────────────────────────────────────────

@dataclass
class PopulationResult:
    """Result of a canonical-to-Invoice population run."""
    invoice_id: uuid.UUID
    extraction_id: uuid.UUID
    gate_status: GateStatus
    eligible: bool                   # True → population ran; False → blocked/review
    block_reason: str | None = None  # Non-None when eligible=False
    po_found: bool = False
    po_id: uuid.UUID | None = None
    line_items_written: int = 0


class GateBlockedError(Exception):
    """Raised when the confidence gate prevents population."""
    def __init__(self, gate_status: GateStatus, reason: str) -> None:
        super().__init__(reason)
        self.gate_status = gate_status
        self.reason = reason


class ExtractionNotFoundError(Exception):
    """Raised when no extraction exists for the invoice."""


# ── Main entry point ──────────────────────────────────────────────────────────

async def populate_invoice_from_extraction(
    db: AsyncSession,
    invoice_id: uuid.UUID,
    *,
    allow_review: bool = False,
) -> PopulationResult:
    """Populate Invoice + InvoiceItem rows from the latest canonical extraction.

    Args:
        db:            Async SQLAlchemy session (caller owns transaction).
        invoice_id:    Invoice to populate.
        allow_review:  If True, REVIEW-gated extractions are allowed to proceed
                       (caller must ensure corrections have been applied first).

    Returns:
        PopulationResult with gate_status, eligibility, and PO linkage info.

    Raises:
        ExtractionNotFoundError  — no InvoiceExtraction row found.
        GateBlockedError         — gate is BLOCKED (never proceeds).
    """
    # 1. Load the latest successful or partial extraction
    extraction = await _load_best_extraction(db, invoice_id)
    if extraction is None:
        raise ExtractionNotFoundError(
            f"No extraction found for invoice {invoice_id}. "
            "Upload a document first."
        )

    # 2. Reconstruct canonical (merged with human corrections if any)
    canonical = await get_corrected_canonical(db, invoice_id)
    if canonical is None:
        raise ExtractionNotFoundError(
            f"Extraction {extraction.id} has no normalized_extraction data."
        )

    # 3. Determine gate status from stored confidence
    confidence = float(extraction.overall_confidence or 0.0)
    gate_status = _infer_gate(confidence)

    # 4. Enforce gate
    if gate_status == GateStatus.BLOCKED:
        raise GateBlockedError(
            GateStatus.BLOCKED,
            f"Extraction confidence {confidence:.2%} is below the BLOCKED "
            "threshold. Human review and correction required before reconciliation.",
        )

    if gate_status == GateStatus.REVIEW and not allow_review:
        return PopulationResult(
            invoice_id=invoice_id,
            extraction_id=extraction.id,
            gate_status=gate_status,
            eligible=False,
            block_reason=(
                f"Extraction confidence {confidence:.2%} requires human review "
                "(REVIEW gate). Apply corrections and retry with allow_review=True."
            ),
        )

    # 5. Load the Invoice row
    invoice = await _load_invoice(db, invoice_id)
    if invoice is None:
        raise ExtractionNotFoundError(f"Invoice {invoice_id} not found in DB.")

    # 6. Resolve PO by po_number
    po_id: uuid.UUID | None = None
    po_found = False
    if canonical.po_number:
        po = await _find_po_by_number(db, canonical.po_number)
        if po is not None:
            po_id = po.id
            po_found = True

    # 7. Update Invoice header
    _apply_canonical_to_invoice(invoice, canonical, po_id)

    # 8. Replace InvoiceItems
    await db.execute(
        delete(InvoiceItem).where(InvoiceItem.invoice_id == invoice_id)
    )
    await db.flush()

    for item in canonical.line_items:
        inv_item = _canonical_line_to_invoice_item(invoice_id, item)
        db.add(inv_item)

    await db.flush()

    logger.info(
        "Populated invoice %s from extraction %s: %d items, po_found=%s, gate=%s",
        invoice_id,
        extraction.id,
        len(canonical.line_items),
        po_found,
        gate_status.value,
    )

    return PopulationResult(
        invoice_id=invoice_id,
        extraction_id=extraction.id,
        gate_status=gate_status,
        eligible=True,
        po_found=po_found,
        po_id=po_id,
        line_items_written=len(canonical.line_items),
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _load_best_extraction(
    db: AsyncSession,
    invoice_id: uuid.UUID,
) -> InvoiceExtraction | None:
    """Load the most recent extraction with normalized_extraction data."""
    result = await db.execute(
        select(InvoiceExtraction)
        .where(
            InvoiceExtraction.invoice_id == invoice_id,
            InvoiceExtraction.normalized_extraction.isnot(None),
        )
        .order_by(InvoiceExtraction.created_at.desc())
    )
    return result.scalars().first()


async def _load_invoice(
    db: AsyncSession,
    invoice_id: uuid.UUID,
) -> Invoice | None:
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    )
    return result.scalar_one_or_none()


async def _find_po_by_number(
    db: AsyncSession,
    po_number: str,
) -> PurchaseOrder | None:
    """Look up a PurchaseOrder by its po_number string."""
    result = await db.execute(
        select(PurchaseOrder).where(PurchaseOrder.po_number == po_number)
    )
    return result.scalar_one_or_none()


def _infer_gate(confidence: float) -> GateStatus:
    """Re-derive gate status from stored confidence.
    Thresholds mirror gate.py (0.85 / 0.60).
    """
    from app.core.config import settings
    high = float(settings.CONFIDENCE_HIGH_THRESHOLD)
    medium = float(settings.CONFIDENCE_MEDIUM_THRESHOLD)
    if confidence >= high:
        return GateStatus.AUTO
    elif confidence >= medium:
        return GateStatus.REVIEW
    else:
        return GateStatus.BLOCKED


def _canonical_from_jsonb(data: dict | None) -> CanonicalExtraction | None:
    """Reconstruct a CanonicalExtraction from stored JSONB dict."""
    if not data:
        return None
    try:
        return CanonicalExtraction.model_validate(data)
    except Exception as exc:
        logger.warning("Failed to parse normalized_extraction: %s", exc)
        return None


def _apply_canonical_to_invoice(
    invoice: Invoice,
    canonical: CanonicalExtraction,
    po_id: uuid.UUID | None,
) -> None:
    """Write canonical fields onto the Invoice ORM object."""
    invoice.vendor_name = canonical.vendor_name
    invoice.invoice_number = canonical.invoice_number
    invoice.currency = canonical.currency

    # Parse invoice_date
    if canonical.invoice_date:
        parsed = _parse_date(canonical.invoice_date)
        if parsed:
            invoice.invoice_date = parsed

    # Monetary — Decimal accepted by SQLAlchemy Numeric columns
    invoice.subtotal = canonical.subtotal
    invoice.tax_amount = canonical.tax_amount
    invoice.discount_amount = canonical.discount_amount
    invoice.grand_total = canonical.grand_total

    # PO linkage
    invoice.po_number = canonical.po_number
    invoice.purchase_order_id = po_id

    # Advance status for reconciliation
    invoice.status = InvoiceStatus.UNDER_AUDIT


def _parse_date(value: str) -> datetime | None:
    """Parse a date string to a timezone-aware datetime."""
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(value.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    logger.warning("Could not parse invoice_date: %r", value)
    return None


def _canonical_line_to_invoice_item(
    invoice_id: uuid.UUID,
    item: CanonicalLineItem,
) -> InvoiceItem:
    """Convert a CanonicalLineItem to an InvoiceItem ORM object."""
    return InvoiceItem(
        invoice_id=invoice_id,
        line_number=item.line_number,
        description=item.description,
        quantity=item.quantity,
        unit_price=item.unit_price,
        line_total=item.line_total,
        tax_rate_percent=item.tax_rate_percent,
        discount=item.discount,
        page_number=item.page_number,
        source_text=item.source_text,
    )
