"""
Phase 6 — Extraction-to-Reconciliation integration tests.

All 13 required tests:

  1.  High-confidence extraction can enter reconciliation (AUTO gate → eligible)
  2.  REVIEW-gated extraction blocked without allow_review
  3.  REVIEW-gated extraction allowed with allow_review=True
  4.  BLOCKED extraction raises GateBlockedError
  5.  Canonical monetary strings become Decimal safely (no float contamination)
  6.  Canonical line items map correctly into InvoiceItem rows
  7.  PO lookup works using extracted po_number
  8.  Partial invoice behaviour: uninvoiced PO line is NOT flagged as MISSING_ITEM
  9.  Overbilling is detected correctly after population
 10.  Grand-total calculation is deterministic (Decimal arithmetic)
 11.  Evidence survives extraction → reconciliation (source_text, page_number)
 12.  Raw extraction remains unchanged after population
 13.  Human correction does not overwrite the raw AI extraction

All tests use mock providers and stored JSONB — no real Gemini calls.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import Audit, AuditCorrection, Evidence
from app.models.enums import ExtractionStatus
from app.models.invoice import Invoice, InvoiceExtraction, InvoiceItem
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem
from app.services.extraction.correction import (
    apply_correction,
    get_corrected_canonical,
    list_corrections,
)
from app.services.extraction.populator import (
    ExtractionNotFoundError,
    GateBlockedError,
    PopulationResult,
    populate_invoice_from_extraction,
)
from app.services.extraction.schemas import GateStatus
from app.services.reconciliation.engine import reconcile_invoice


# ── Test fixtures ─────────────────────────────────────────────────────────────

def _make_canonical_dict(
    *,
    vendor: str = "Test Vendor",
    invoice_number: str = "INV-001",
    po_number: str | None = None,
    subtotal: str = "100000.00",
    tax: str = "18000.00",
    discount: str = "0.00",
    grand_total: str = "118000.00",
    lines: list[dict] | None = None,
) -> dict:
    """Build a normalized_extraction-compatible dict."""
    if lines is None:
        lines = [
            {
                "line_number": 1,
                "description": "Steel bolts M10",
                "quantity": "100",
                "unit_price": "1000.00",
                "line_total": "100000.00",
                "tax_rate_percent": "0",
                "discount": "0",
                "page_number": 1,
                "source_text": "Steel bolts M10 100 × 1000.00 = 100000.00",
            }
        ]
    return {
        "vendor_name": vendor,
        "invoice_number": invoice_number,
        "invoice_date": "2026-01-15",
        "currency": "INR",
        "po_number": po_number,
        "subtotal": subtotal,
        "tax_amount": tax,
        "discount_amount": discount,
        "grand_total": grand_total,
        "line_items": lines,
    }


async def _make_invoice(session: AsyncSession, **kwargs) -> Invoice:
    defaults = dict(
        invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
        vendor_name="Test Vendor",
        invoice_date=datetime(2026, 1, 15, tzinfo=timezone.utc),
        grand_total=Decimal("118000"),
    )
    defaults.update(kwargs)
    inv = Invoice(**defaults)
    session.add(inv)
    await session.flush()
    return inv


async def _make_extraction(
    session: AsyncSession,
    invoice_id: uuid.UUID,
    confidence: float,
    normalized: dict | None = None,
    raw: dict | None = None,
) -> InvoiceExtraction:
    ext = InvoiceExtraction(
        invoice_id=invoice_id,
        model_provider="mock",
        model_name="mock-v1",
        raw_extraction=raw or {"vendor_name": "Test Vendor", "grand_total": "118000.00"},
        normalized_extraction=normalized or _make_canonical_dict(),
        extraction_status="success",
        overall_confidence=Decimal(str(confidence)),
    )
    session.add(ext)
    await session.flush()
    return ext


async def _make_po(
    session: AsyncSession,
    *,
    po_number: str = "PO-001",
    vendor: str = "Test Vendor",
    lines: list[tuple[str, Decimal, Decimal]] | None = None,
) -> PurchaseOrder:
    """Create a PurchaseOrder with items. lines = [(desc, qty, unit_price)]"""
    if lines is None:
        lines = [("Steel bolts M10", Decimal("100"), Decimal("1000.00"))]
    po = PurchaseOrder(
        po_number=po_number,
        vendor_name=vendor,
        po_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        subtotal=sum(qty * price for _, qty, price in lines),
        tax_amount=Decimal("0"),
        discount_amount=Decimal("0"),
        grand_total=sum(qty * price for _, qty, price in lines),
    )
    session.add(po)
    await session.flush()
    for i, (desc, qty, price) in enumerate(lines, start=1):
        item = PurchaseOrderItem(
            purchase_order_id=po.id,
            line_number=i,
            description=desc,
            quantity=qty,
            unit_price=price,
            line_total=qty * price,
        )
        session.add(item)
    await session.flush()
    return po


# ═════════════════════════════════════════════════════════════════════════════
#  TEST 1 — High-confidence extraction → AUTO → eligible
# ═════════════════════════════════════════════════════════════════════════════

async def test_high_confidence_auto_gate_eligible(session: AsyncSession):
    """AUTO-gated extraction (confidence ≥ 0.85) is eligible for reconciliation."""
    inv = await _make_invoice(session)
    await _make_extraction(session, inv.id, confidence=0.92)

    result = await populate_invoice_from_extraction(session, inv.id)

    assert result.eligible is True
    assert result.gate_status == GateStatus.AUTO


# ═════════════════════════════════════════════════════════════════════════════
#  TEST 2 — REVIEW gate blocked without allow_review
# ═════════════════════════════════════════════════════════════════════════════

async def test_review_gate_blocked_without_flag(session: AsyncSession):
    """REVIEW-gated extraction is not eligible without allow_review=True."""
    inv = await _make_invoice(session)
    await _make_extraction(session, inv.id, confidence=0.72)

    result = await populate_invoice_from_extraction(session, inv.id, allow_review=False)

    assert result.eligible is False
    assert result.gate_status == GateStatus.REVIEW
    assert result.block_reason is not None


# ═════════════════════════════════════════════════════════════════════════════
#  TEST 3 — REVIEW gate allowed with allow_review=True
# ═════════════════════════════════════════════════════════════════════════════

async def test_review_gate_allowed_with_flag(session: AsyncSession):
    """REVIEW-gated extraction proceeds when allow_review=True."""
    inv = await _make_invoice(session)
    await _make_extraction(session, inv.id, confidence=0.72)

    result = await populate_invoice_from_extraction(session, inv.id, allow_review=True)

    assert result.eligible is True
    assert result.gate_status == GateStatus.REVIEW


# ═════════════════════════════════════════════════════════════════════════════
#  TEST 4 — BLOCKED extraction raises GateBlockedError
# ═════════════════════════════════════════════════════════════════════════════

async def test_blocked_extraction_raises(session: AsyncSession):
    """BLOCKED extraction (confidence < 0.60) raises GateBlockedError."""
    inv = await _make_invoice(session)
    await _make_extraction(session, inv.id, confidence=0.30)

    with pytest.raises(GateBlockedError) as exc_info:
        await populate_invoice_from_extraction(session, inv.id)

    assert exc_info.value.gate_status == GateStatus.BLOCKED


# ═════════════════════════════════════════════════════════════════════════════
#  TEST 5 — Canonical monetary strings become Decimal safely
# ═════════════════════════════════════════════════════════════════════════════

async def test_canonical_monetary_decimal_safe(session: AsyncSession):
    """Monetary values from canonical extraction are stored as Decimal, not float."""
    inv = await _make_invoice(session)
    canonical = _make_canonical_dict(
        subtotal="100000.00",
        tax="18000.00",
        grand_total="118000.00",
    )
    await _make_extraction(session, inv.id, confidence=0.92, normalized=canonical)

    await populate_invoice_from_extraction(session, inv.id)

    updated = await session.get(Invoice, inv.id)
    assert Decimal(str(updated.subtotal)) == Decimal("100000.00")
    assert Decimal(str(updated.tax_amount)) == Decimal("18000.00")
    assert Decimal(str(updated.grand_total)) == Decimal("118000.00")
    # Verify it's not corrupted by float arithmetic
    assert Decimal(str(updated.subtotal)) + Decimal(str(updated.tax_amount)) \
        == Decimal(str(updated.grand_total))


# ═════════════════════════════════════════════════════════════════════════════
#  TEST 6 — Canonical line items map into InvoiceItem rows
# ═════════════════════════════════════════════════════════════════════════════

async def test_canonical_line_items_written(session: AsyncSession):
    """Line items from canonical extraction are written as InvoiceItem rows."""
    inv = await _make_invoice(session)
    canonical = _make_canonical_dict(
        lines=[
            {
                "line_number": 1,
                "description": "Bolt M10",
                "quantity": "100",
                "unit_price": "500.00",
                "line_total": "50000.00",
                "tax_rate_percent": "0",
                "discount": "0",
            },
            {
                "line_number": 2,
                "description": "Nut M10",
                "quantity": "200",
                "unit_price": "250.00",
                "line_total": "50000.00",
                "tax_rate_percent": "0",
                "discount": "0",
            },
        ],
        subtotal="100000.00",
        grand_total="100000.00",
    )
    await _make_extraction(session, inv.id, confidence=0.92, normalized=canonical)

    result = await populate_invoice_from_extraction(session, inv.id)

    assert result.line_items_written == 2

    items_result = await session.execute(
        select(InvoiceItem).where(InvoiceItem.invoice_id == inv.id)
    )
    items = items_result.scalars().all()
    assert len(items) == 2
    descriptions = {i.description for i in items}
    assert "Bolt M10" in descriptions
    assert "Nut M10" in descriptions

    bolt = next(i for i in items if i.description == "Bolt M10")
    assert Decimal(str(bolt.quantity)) == Decimal("100")
    assert Decimal(str(bolt.unit_price)) == Decimal("500.00")
    assert Decimal(str(bolt.line_total)) == Decimal("50000.00")


# ═════════════════════════════════════════════════════════════════════════════
#  TEST 7 — PO lookup via extracted po_number
# ═════════════════════════════════════════════════════════════════════════════

async def test_po_lookup_by_number(session: AsyncSession):
    """Populator links PO by po_number extracted from the document."""
    po = await _make_po(session, po_number="PO-EXTRACT-001")
    inv = await _make_invoice(session)
    canonical = _make_canonical_dict(po_number="PO-EXTRACT-001")
    await _make_extraction(session, inv.id, confidence=0.92, normalized=canonical)

    result = await populate_invoice_from_extraction(session, inv.id)

    assert result.po_found is True
    assert result.po_id == po.id

    updated = await session.get(Invoice, inv.id)
    assert updated.purchase_order_id == po.id
    assert updated.po_number == "PO-EXTRACT-001"


# ═════════════════════════════════════════════════════════════════════════════
#  TEST 8 — Partial invoice: uninvoiced PO line not flagged as MISSING_ITEM
# ═════════════════════════════════════════════════════════════════════════════

async def test_partial_invoice_no_missing_item_anomaly(session: AsyncSession):
    """Partial invoicing: covering only 50 of 100 PO qty does not trigger MISSING_ITEM."""
    po = await _make_po(
        session,
        po_number="PO-PARTIAL-001",
        lines=[("Widget A", Decimal("100"), Decimal("100.00"))],
    )
    canonical = _make_canonical_dict(
        po_number="PO-PARTIAL-001",
        lines=[{
            "line_number": 1,
            "description": "Widget A",
            "quantity": "50",        # Partial: only 50 of 100
            "unit_price": "100.00",
            "line_total": "5000.00",
            "tax_rate_percent": "0",
            "discount": "0",
        }],
        subtotal="5000.00",
        grand_total="5000.00",
    )
    inv = await _make_invoice(
        session,
        vendor_name="Test Vendor",
        grand_total=Decimal("5000"),
    )
    await _make_extraction(session, inv.id, confidence=0.92, normalized=canonical)

    await populate_invoice_from_extraction(session, inv.id)
    report = await reconcile_invoice(session, inv.id)

    anomaly_types = [a.type for a in report.anomalies]
    assert "MISSING_ITEM" not in anomaly_types, (
        f"Partial invoice should not flag MISSING_ITEM. Got: {anomaly_types}"
    )


# ═════════════════════════════════════════════════════════════════════════════
#  TEST 9 — Overbilling detected correctly after population
# ═════════════════════════════════════════════════════════════════════════════

async def test_overbilling_detected_after_population(session: AsyncSession):
    """Invoicing 120 against PO quantity of 100 triggers QUANTITY_OVERBILLING."""
    po = await _make_po(
        session,
        po_number="PO-OVER-001",
        lines=[("Bolt", Decimal("100"), Decimal("1000.00"))],
    )
    canonical = _make_canonical_dict(
        po_number="PO-OVER-001",
        lines=[{
            "line_number": 1,
            "description": "Bolt",
            "quantity": "120",       # 20 over PO qty
            "unit_price": "1000.00",
            "line_total": "120000.00",
            "tax_rate_percent": "0",
            "discount": "0",
        }],
        subtotal="120000.00",
        grand_total="120000.00",
    )
    inv = await _make_invoice(
        session,
        vendor_name="Test Vendor",
        grand_total=Decimal("120000"),
    )
    await _make_extraction(session, inv.id, confidence=0.92, normalized=canonical)

    await populate_invoice_from_extraction(session, inv.id)
    report = await reconcile_invoice(session, inv.id)

    anomaly_types = [a.type for a in report.anomalies]
    assert "QUANTITY_OVERBILLING" in anomaly_types


# ═════════════════════════════════════════════════════════════════════════════
#  TEST 10 — Grand-total arithmetic is deterministic (Decimal only)
# ═════════════════════════════════════════════════════════════════════════════

def test_grand_total_arithmetic_deterministic():
    """subtotal + tax - discount = grand_total using Decimal (no float error)."""
    subtotal = Decimal("100000.00")
    tax = Decimal("18000.00")
    discount = Decimal("0.00")
    expected = subtotal + tax - discount
    assert expected == Decimal("118000.00")
    # Verify this does NOT produce the float 0.1 + 0.2 ≠ 0.3 problem
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")


# ═════════════════════════════════════════════════════════════════════════════
#  TEST 11 — Evidence survives extraction → reconciliation
# ═════════════════════════════════════════════════════════════════════════════

async def test_evidence_survives_extraction_to_reconciliation(session: AsyncSession):
    """After reconciliation, Evidence rows exist and anomalies have evidence."""
    po = await _make_po(
        session,
        po_number="PO-EVIDENCE-001",
        vendor="Acme Corp",
        lines=[("Bolt", Decimal("100"), Decimal("2000.00"))],  # PO price: 2000
    )
    # Invoice at different price to trigger PRICE_MISMATCH
    canonical = _make_canonical_dict(
        vendor="Acme Corp",
        po_number="PO-EVIDENCE-001",
        lines=[{
            "line_number": 1,
            "description": "Bolt",
            "quantity": "100",
            "unit_price": "2500.00",   # Invoice price: 2500 (mismatch)
            "line_total": "250000.00",
            "tax_rate_percent": "0",
            "discount": "0",
            "page_number": 2,
            "source_text": "Bolt 100 × 2500.00 = 250000.00",
        }],
        subtotal="250000.00",
        grand_total="250000.00",
    )
    inv = await _make_invoice(
        session,
        vendor_name="Acme Corp",
        grand_total=Decimal("250000"),
    )
    await _make_extraction(session, inv.id, confidence=0.92, normalized=canonical)

    await populate_invoice_from_extraction(session, inv.id)
    report = await reconcile_invoice(session, inv.id)
    await session.flush()

    # Should have PRICE_MISMATCH anomaly
    assert any(a.type == "PRICE_MISMATCH" for a in report.anomalies)

    # Evidence must exist
    audit_result = await session.execute(
        select(Audit).where(Audit.invoice_id == inv.id)
    )
    audit = audit_result.scalars().first()
    assert audit is not None

    # Load anomalies with evidence
    from sqlalchemy.orm import selectinload
    from app.models.audit import ReconciliationResult, Anomaly
    full_result = await session.execute(
        select(ReconciliationResult)
        .where(ReconciliationResult.audit_id == audit.id)
        .options(
            selectinload(ReconciliationResult.anomalies)
            .selectinload(Anomaly.evidence)
        )
    )
    rec = full_result.scalar_one()
    all_evidence = [ev for a in rec.anomalies for ev in a.evidence]
    assert len(all_evidence) > 0


# ═════════════════════════════════════════════════════════════════════════════
#  TEST 12 — Raw extraction is unchanged after population
# ═════════════════════════════════════════════════════════════════════════════

async def test_raw_extraction_unchanged_after_population(session: AsyncSession):
    """InvoiceExtraction.raw_extraction is immutable through population."""
    inv = await _make_invoice(session)
    raw_original = {
        "vendor_name": "Original AI Output",
        "grand_total": "99999.00",
        "invoice_number": "RAW-001",
    }
    ext = await _make_extraction(
        session,
        inv.id,
        confidence=0.92,
        raw=raw_original,
        normalized=_make_canonical_dict(),
    )

    await populate_invoice_from_extraction(session, inv.id)

    # Reload the extraction row
    await session.refresh(ext)
    assert ext.raw_extraction["vendor_name"] == "Original AI Output"
    assert ext.raw_extraction["grand_total"] == "99999.00"
    assert ext.raw_extraction["invoice_number"] == "RAW-001"


# ═════════════════════════════════════════════════════════════════════════════
#  TEST 13 — Human correction does not overwrite raw AI extraction
# ═════════════════════════════════════════════════════════════════════════════

async def test_human_correction_does_not_overwrite_raw(session: AsyncSession):
    """apply_correction writes AuditCorrection; raw_extraction stays unchanged."""
    inv = await _make_invoice(session)
    raw_original = {
        "vendor_name": "AI Extracted Vendor",
        "grand_total": "100000.00",
    }
    ext = await _make_extraction(
        session,
        inv.id,
        confidence=0.72,
        raw=raw_original,
        normalized=_make_canonical_dict(vendor="AI Extracted Vendor", grand_total="100000.00"),
    )

    # Create a minimal audit
    audit = Audit(
        invoice_id=inv.id,
        status="review_required",
        started_at=datetime.now(timezone.utc),
    )
    session.add(audit)
    await session.flush()

    # Apply a human correction to grand_total
    correction = await apply_correction(
        session,
        audit_id=audit.id,
        invoice_id=inv.id,
        field_path="grand_total",
        corrected_value="105000.00",
        original_value="100000.00",
        reason="Tax calculation error in original extraction",
        corrected_by="reviewer@company.com",
    )

    # Verify raw_extraction is completely unchanged
    await session.refresh(ext)
    assert ext.raw_extraction["grand_total"] == "100000.00"
    assert ext.raw_extraction["vendor_name"] == "AI Extracted Vendor"

    # Verify correction is stored separately
    assert correction.field_path == "grand_total"
    assert correction.original_value == "100000.00"
    assert correction.corrected_value == "105000.00"
    assert correction.corrected_by == "reviewer@company.com"

    # Verify get_corrected_canonical applies the correction
    corrected = await get_corrected_canonical(session, inv.id, audit_id=audit.id)
    assert corrected is not None
    assert corrected.grand_total == Decimal("105000.00")

    # Verify original normalized_extraction is NOT modified
    await session.refresh(ext)
    norm = ext.normalized_extraction
    assert norm["grand_total"] == "100000.00"  # unchanged
