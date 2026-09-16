"""
Extraction pipeline orchestrator.

Ties together all extraction stages:
  1. Preprocess (PDF → images if needed)
  2. Call extraction provider
  3. Validate deterministically
  4. Calculate confidence
  5. Apply confidence gate
  6. Build canonical extraction (Decimal conversion)
  7. Build source references
  8. Persist InvoiceExtraction row
  9. Return ExtractionResult
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import InvoiceExtraction
from app.models.enums import ExtractionStatus
from app.services.extraction.confidence import calculate_confidence
from app.services.extraction.gate import apply_gate
from app.services.extraction.ingestion import prepare_pages, IngestedDocument
from app.services.extraction.provider_base import ExtractionProvider
from app.services.extraction.schemas import (
    CanonicalExtraction,
    CanonicalLineItem,
    ExtractionResult,
    GateStatus,
    RawExtractionResult,
    SourceReference,
)
from app.services.extraction.validation import validate_extraction

logger = logging.getLogger(__name__)


class ExtractionPipelineError(Exception):
    """Raised when the extraction pipeline fails."""
    pass


async def run_extraction_pipeline(
    db: AsyncSession,
    invoice_id: uuid.UUID,
    document_id: uuid.UUID,
    ingested: IngestedDocument,
    file_content: bytes,
    provider: ExtractionProvider,
) -> ExtractionResult:
    """Execute the full extraction pipeline.

    Args:
        db: Async database session.
        invoice_id: ID of the Invoice this document belongs to.
        document_id: ID of the persisted InvoiceDocument.
        ingested: IngestedDocument metadata from ingestion.
        file_content: Raw file bytes (for PDF page conversion).
        provider: AI extraction provider to use.

    Returns:
        ExtractionResult with raw, canonical, confidence, gate, and issues.
    """
    # 1. Prepare page images
    ingested = prepare_pages(ingested, file_content)

    # 2. Call AI provider
    try:
        raw = await provider.extract(
            document_pages=ingested.page_images,
            mime_type=ingested.page_image_mime,
        )
    except Exception as exc:
        logger.error("Extraction provider failed: %s", exc)
        failed_result = ExtractionResult(
            raw=RawExtractionResult(),
            canonical=None,
            confidence=0.0,
            gate_status=GateStatus.BLOCKED,
            extraction_status="failed",
            provider_name=provider.provider_name,
            model_name=provider.model_name,
        )
        await _persist_extraction(
            db, invoice_id, document_id, failed_result, provider
        )
        return failed_result

    # 3. Validate deterministically
    validation_issues = validate_extraction(raw)

    # 4. Calculate confidence
    confidence = calculate_confidence(raw, validation_issues)

    # 5. Apply gate
    gate_status = apply_gate(confidence)

    # 6. Build canonical extraction
    canonical = _build_canonical(raw)

    # 7. Build source references
    source_refs = _build_source_references(raw, provider)

    # 8. Determine status
    has_errors = any(vi.severity.value == "error" for vi in validation_issues)
    if canonical is None:
        extraction_status = "failed"
    elif has_errors:
        extraction_status = "partial"
    else:
        extraction_status = "success"

    result = ExtractionResult(
        raw=raw,
        canonical=canonical,
        confidence=confidence,
        gate_status=gate_status,
        validation_issues=validation_issues,
        source_references=source_refs,
        extraction_status=extraction_status,
        provider_name=provider.provider_name,
        model_name=provider.model_name,
    )

    # 9. Persist
    await _persist_extraction(db, invoice_id, document_id, result, provider)

    return result


def _parse_decimal_safe(value: str | None) -> Decimal:
    """Parse a string to Decimal, returning 0 on failure."""
    if not value or not str(value).strip():
        return Decimal("0")
    try:
        cleaned = str(value).strip().replace(",", "").replace("₹", "").replace("$", "")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _build_canonical(raw: RawExtractionResult) -> CanonicalExtraction | None:
    """Convert raw string values to Decimal canonical form.

    Returns None if critical fields are missing.
    """
    if not raw.vendor_name or not raw.invoice_number:
        return None

    line_items: list[CanonicalLineItem] = []
    for i, item in enumerate(raw.line_items):
        desc = item.description or f"Line {i + 1}"
        qty = _parse_decimal_safe(item.quantity)
        price = _parse_decimal_safe(item.unit_price)
        total = _parse_decimal_safe(item.line_total)
        tax_rate = _parse_decimal_safe(item.tax_rate_percent)
        discount = _parse_decimal_safe(item.discount)

        # Skip lines with zero quantity (likely parsing failure)
        if qty <= 0:
            qty = Decimal("1")  # Default to 1 rather than skipping

        line_items.append(CanonicalLineItem(
            line_number=i + 1,
            description=desc,
            quantity=qty,
            unit_price=price,
            line_total=total if total > 0 else qty * price,
            tax_rate_percent=tax_rate,
            discount=discount,
            page_number=item.page_number,
            source_text=item.source_text,
        ))

    return CanonicalExtraction(
        vendor_name=raw.vendor_name,
        invoice_number=raw.invoice_number,
        invoice_date=raw.invoice_date,
        due_date=raw.due_date,
        currency=raw.currency or "INR",
        po_number=raw.po_number,
        subtotal=_parse_decimal_safe(raw.subtotal),
        tax_amount=_parse_decimal_safe(raw.tax_amount),
        discount_amount=_parse_decimal_safe(raw.discount_amount),
        grand_total=_parse_decimal_safe(raw.grand_total),
        line_items=line_items,
    )


def _build_source_references(
    raw: RawExtractionResult,
    provider: ExtractionProvider,
) -> list[SourceReference]:
    """Build traceability references from the raw extraction."""
    method = f"{provider.provider_name}/{provider.model_name}"
    refs: list[SourceReference] = []

    # Header fields
    for field_name in (
        "vendor_name", "invoice_number", "invoice_date", "grand_total",
        "subtotal", "tax_amount", "po_number",
    ):
        val = getattr(raw, field_name, None)
        if val:
            refs.append(SourceReference(
                field=field_name,
                page_number=1,  # Header fields typically on page 1
                source_text=str(val),
                extraction_method=method,
            ))

    # Line items
    for i, item in enumerate(raw.line_items):
        refs.append(SourceReference(
            field=f"line_items[{i}]",
            page_number=item.page_number,
            source_text=item.source_text,
            extraction_method=method,
        ))

    return refs


async def _persist_extraction(
    db: AsyncSession,
    invoice_id: uuid.UUID,
    document_id: uuid.UUID,
    result: ExtractionResult,
    provider: ExtractionProvider,
) -> None:
    """Write the extraction result to the InvoiceExtraction table."""
    # Map extraction_status string to the enum
    status_map = {
        "success": ExtractionStatus.SUCCESS.value,
        "partial": ExtractionStatus.PARTIAL.value,
        "failed": ExtractionStatus.FAILED.value,
    }

    extraction = InvoiceExtraction(
        invoice_id=invoice_id,
        document_id=document_id,
        model_provider=provider.provider_name,
        model_name=provider.model_name,
        raw_extraction=result.raw.model_dump(mode="json"),
        normalized_extraction=(
            result.canonical.model_dump(mode="json") if result.canonical else None
        ),
        extraction_status=status_map.get(result.extraction_status, "failed"),
        overall_confidence=result.confidence,
    )
    db.add(extraction)
    # Caller is responsible for commit
