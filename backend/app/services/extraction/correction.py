"""
Human correction service.

Provides the backend contract for human review workflow:

  1. Human reviews extracted data
  2. Applies corrections for specific fields
  3. Corrections are stored in AuditCorrection (original value PRESERVED)
  4. get_corrected_canonical() merges original canonical + corrections
     into a corrected CanonicalExtraction for downstream use

Invariant:
  InvoiceExtraction.raw_extraction      — NEVER modified
  InvoiceExtraction.normalized_extraction — NEVER modified
  AuditCorrection                         — append-only correction log
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import Audit, AuditCorrection
from app.models.invoice import InvoiceExtraction
from app.services.extraction.schemas import CanonicalExtraction, CanonicalLineItem

logger = logging.getLogger(__name__)


# ── Apply a single field correction ──────────────────────────────────────────

async def apply_correction(
    db: AsyncSession,
    *,
    audit_id: uuid.UUID,
    invoice_id: uuid.UUID,
    field_path: str,
    corrected_value: str,
    original_value: str | None = None,
    reason: str | None = None,
    corrected_by: str | None = None,
) -> AuditCorrection:
    """Record a human correction without touching InvoiceExtraction.

    Args:
        db:              Async session (caller owns transaction).
        audit_id:        The audit this correction belongs to.
        invoice_id:      The invoice being corrected.
        field_path:      Dot-path to the corrected field, e.g. "grand_total"
                         or "line_items[0].unit_price".
        corrected_value: Human-supplied corrected value (stored as string).
        original_value:  Original AI-extracted value (stored for audit trail).
        reason:          Optional free-text reason for the correction.
        corrected_by:    Optional identifier of the human reviewer.

    Returns:
        The persisted AuditCorrection row.
    """
    correction = AuditCorrection(
        audit_id=audit_id,
        invoice_id=invoice_id,
        field_path=field_path,
        original_value=original_value,
        corrected_value=corrected_value,
        reason=reason,
        corrected_by=corrected_by,
    )
    db.add(correction)
    await db.flush()
    logger.info(
        "Correction applied: invoice=%s field=%s %r → %r",
        invoice_id,
        field_path,
        original_value,
        corrected_value,
    )
    return correction


# ── Build corrected canonical ─────────────────────────────────────────────────

async def get_corrected_canonical(
    db: AsyncSession,
    invoice_id: uuid.UUID,
    audit_id: uuid.UUID | None = None,
) -> CanonicalExtraction | None:
    """Merge original canonical extraction with any AuditCorrections.

    Applies corrections in creation order (oldest first).
    Original InvoiceExtraction is never modified.

    Returns None if no extraction exists for the invoice.
    """
    # Load latest normalized extraction
    result = await db.execute(
        select(InvoiceExtraction)
        .where(
            InvoiceExtraction.invoice_id == invoice_id,
            InvoiceExtraction.normalized_extraction.isnot(None),
        )
        .order_by(InvoiceExtraction.created_at.desc())
    )
    extraction = result.scalars().first()
    if extraction is None or not extraction.normalized_extraction:
        return None

    # Reconstruct base canonical
    try:
        canonical_data = dict(extraction.normalized_extraction)
        canonical = CanonicalExtraction.model_validate(canonical_data)
    except Exception as exc:
        logger.warning("Cannot parse normalized_extraction: %s", exc)
        return None

    # Load corrections for this invoice (optionally scoped to one audit)
    stmt = (
        select(AuditCorrection)
        .where(AuditCorrection.invoice_id == invoice_id)
        .order_by(AuditCorrection.created_at.asc())
    )
    if audit_id is not None:
        stmt = stmt.where(AuditCorrection.audit_id == audit_id)
    corrections_result = await db.execute(stmt)
    corrections = corrections_result.scalars().all()

    if not corrections:
        return canonical

    # Apply each correction to a mutable dict, then re-validate
    data = canonical.model_dump(mode="python")
    for corr in corrections:
        _apply_correction_to_dict(data, corr.field_path, corr.corrected_value)

    try:
        return CanonicalExtraction.model_validate(data)
    except Exception as exc:
        logger.error("Corrected canonical failed validation: %s", exc)
        return canonical  # Return uncorrected canonical if merged form is invalid


# ── Correction helpers ────────────────────────────────────────────────────────

def _apply_correction_to_dict(
    data: dict,
    field_path: str,
    corrected_value: str,
) -> None:
    """Apply a single correction to a mutable canonical dict.

    Supports simple field names ("grand_total") and indexed line items
    ("line_items[0].unit_price").

    All monetary values are parsed as strings (Pydantic will convert to Decimal
    when re-validating the CanonicalExtraction).
    """
    # Simple top-level field: e.g. "grand_total", "vendor_name"
    if "[" not in field_path and "." not in field_path:
        if field_path in data:
            # Store as string; CanonicalExtraction will coerce to Decimal
            data[field_path] = corrected_value
        return

    # Line item field: e.g. "line_items[2].unit_price"
    if field_path.startswith("line_items["):
        try:
            bracket_end = field_path.index("]")
            idx = int(field_path[len("line_items["):bracket_end])
            remainder = field_path[bracket_end + 2:]  # skip "]."
            if "line_items" in data and idx < len(data["line_items"]):
                data["line_items"][idx][remainder] = corrected_value
        except (ValueError, IndexError, KeyError) as exc:
            logger.warning("Cannot apply correction %r: %s", field_path, exc)
        return

    logger.warning("Unrecognised field_path pattern: %r — skipping", field_path)


# ── Convenience: list all corrections for an invoice ─────────────────────────

async def list_corrections(
    db: AsyncSession,
    invoice_id: uuid.UUID,
) -> list[AuditCorrection]:
    """Return all corrections for an invoice, oldest first."""
    result = await db.execute(
        select(AuditCorrection)
        .where(AuditCorrection.invoice_id == invoice_id)
        .order_by(AuditCorrection.created_at.asc())
    )
    return list(result.scalars().all())
