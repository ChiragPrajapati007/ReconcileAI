"""
Database-level tests for Phase 2.

Covers:
- PO creation and items
- Invoice creation
- Invoice-to-PO relationships
- Multiple invoices per PO
- Partial invoice ledger
- Overbilling calculation
- Duplicate detection signals
- Evidence and correction history
- Cascade behavior
- Monetary precision (Numeric, never float)
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem
from app.models.invoice import Invoice, InvoiceItem, InvoiceDocument
from app.models.audit import (
    Audit,
    AuditCorrection,
    Anomaly,
    ReconciliationResult,
    Evidence,
    InvoiceLineLedger,
)
from app.models.enums import (
    AnomalySeverity,
    AnomalyStatus,
    AnomalyType,
    AuditStatus,
    EvidenceSourceType,
    InvoiceStatus,
    POStatus,
    ReconciliationStatus,
)


def utc(year, month, day):
    return datetime(year, month, day, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def make_po(session, po_number="PO-TEST-001", vendor="Test Vendor"):
    po = PurchaseOrder(
        po_number=po_number,
        vendor_name=vendor,
        po_date=utc(2026, 1, 1),
        currency="INR",
        subtotal=Decimal("100000.00"),
        tax_amount=Decimal("18000.00"),
        discount_amount=Decimal("0.00"),
        grand_total=Decimal("118000.00"),
        status=POStatus.OPEN,
    )
    session.add(po)
    return po


def make_po_item(session, po_id, line=1, qty=Decimal("10"), price=Decimal("10000.00")):
    item = PurchaseOrderItem(
        purchase_order_id=po_id,
        line_number=line,
        description="Test Product",
        normalized_description="test product",
        quantity=qty,
        unit_price=price,
        tax_rate_percent=Decimal("18.000"),
        discount=Decimal("0.00"),
        line_total=qty * price,
    )
    session.add(item)
    return item


def make_invoice(session, invoice_number="INV-TEST-001", vendor="Test Vendor", po_id=None, total=Decimal("118000.00")):
    inv = Invoice(
        invoice_number=invoice_number,
        vendor_name=vendor,
        invoice_date=utc(2026, 1, 15),
        po_number="PO-TEST-001",
        purchase_order_id=po_id,
        currency="INR",
        subtotal=Decimal("100000.00"),
        tax_amount=Decimal("18000.00"),
        discount_amount=Decimal("0.00"),
        grand_total=total,
        status=InvoiceStatus.DRAFT,
    )
    session.add(inv)
    return inv


# ---------------------------------------------------------------------------
# 1. PO creation
# ---------------------------------------------------------------------------
async def test_create_purchase_order(session: AsyncSession):
    po = make_po(session)
    await session.flush()

    result = await session.execute(select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-TEST-001"))
    fetched = result.scalar_one()
    assert fetched.vendor_name == "Test Vendor"
    assert fetched.currency == "INR"
    assert fetched.grand_total == Decimal("118000.00")
    assert fetched.status == POStatus.OPEN


async def test_po_items_relationship(session: AsyncSession):
    po = make_po(session)
    await session.flush()
    item = make_po_item(session, po.id, qty=Decimal("5"), price=Decimal("20000.00"))
    await session.flush()

    result = await session.execute(
        select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po.id)
    )
    items = result.scalars().all()
    assert len(items) == 1
    assert items[0].quantity == Decimal("5")
    assert items[0].line_total == Decimal("100000.00")


# ---------------------------------------------------------------------------
# 2. Invoice creation and PO linkage
# ---------------------------------------------------------------------------
async def test_create_invoice_with_po(session: AsyncSession):
    po = make_po(session)
    await session.flush()
    inv = make_invoice(session, po_id=po.id)
    await session.flush()

    result = await session.execute(select(Invoice).where(Invoice.invoice_number == "INV-TEST-001"))
    fetched = result.scalar_one()
    assert fetched.purchase_order_id == po.id
    assert fetched.grand_total == Decimal("118000.00")


async def test_invoice_without_po_is_valid(session: AsyncSession):
    """An invoice with no PO linkage is valid — will be flagged as PO_MISMATCH later."""
    inv = Invoice(
        invoice_number="INV-NO-PO",
        vendor_name="Unknown Vendor",
        invoice_date=utc(2026, 1, 1),
        po_number=None,
        purchase_order_id=None,
        currency="INR",
        subtotal=Decimal("50000.00"),
        tax_amount=Decimal("9000.00"),
        discount_amount=Decimal("0.00"),
        grand_total=Decimal("59000.00"),
        status=InvoiceStatus.PENDING_REVIEW,
    )
    session.add(inv)
    await session.flush()

    result = await session.execute(select(Invoice).where(Invoice.invoice_number == "INV-NO-PO"))
    fetched = result.scalar_one()
    assert fetched.purchase_order_id is None
    assert fetched.po_number is None


# ---------------------------------------------------------------------------
# 3. Multiple invoices per PO
# ---------------------------------------------------------------------------
async def test_multiple_invoices_per_po(session: AsyncSession):
    po = make_po(session)
    await session.flush()

    inv1 = make_invoice(session, "INV-M-001", po_id=po.id)
    inv2 = make_invoice(session, "INV-M-002", po_id=po.id)
    await session.flush()

    result = await session.execute(
        select(Invoice).where(Invoice.purchase_order_id == po.id)
    )
    invoices = result.scalars().all()
    assert len(invoices) == 2


# ---------------------------------------------------------------------------
# 4. Invoice line ledger — partial invoice tracking
# ---------------------------------------------------------------------------
async def test_ledger_partial_invoice_valid(session: AsyncSession):
    """
    PO qty = 100, prev invoiced = 40, current = 30 → remaining = 60 → VALID
    """
    po = make_po(session)
    await session.flush()
    po_item = make_po_item(session, po.id, qty=Decimal("100"), price=Decimal("1000.00"))
    await session.flush()

    inv_prev = make_invoice(session, "INV-LEDGER-PREV", po_id=po.id)
    await session.flush()
    session.add(InvoiceLineLedger(
        purchase_order_item_id=po_item.id,
        invoice_id=inv_prev.id,
        quantity_invoiced=Decimal("40"),
        unit_price=Decimal("1000.00"),
        invoiced_at=utc(2026, 1, 10),
    ))

    inv_curr = make_invoice(session, "INV-LEDGER-CURR", po_id=po.id)
    await session.flush()
    session.add(InvoiceLineLedger(
        purchase_order_item_id=po_item.id,
        invoice_id=inv_curr.id,
        quantity_invoiced=Decimal("30"),
        unit_price=Decimal("1000.00"),
        invoiced_at=utc(2026, 1, 20),
    ))
    await session.flush()

    # Calculate previously invoiced for inv_curr
    result = await session.execute(
        select(func.sum(InvoiceLineLedger.quantity_invoiced))
        .where(InvoiceLineLedger.purchase_order_item_id == po_item.id)
        .where(InvoiceLineLedger.invoice_id != inv_curr.id)
    )
    previously_invoiced = result.scalar() or Decimal("0")
    remaining = po_item.quantity - previously_invoiced

    current_qty = Decimal("30")
    assert previously_invoiced == Decimal("40")
    assert remaining == Decimal("60")
    assert current_qty <= remaining, "Should NOT be flagged as overbilling"


async def test_ledger_overbilling_detected(session: AsyncSession):
    """
    PO qty = 100, prev invoiced = 80, current = 30 → remaining = 20 → OVERBILLED by 10
    """
    po = make_po(session, "PO-OB-001")
    await session.flush()
    po_item = make_po_item(session, po.id, qty=Decimal("100"), price=Decimal("1000.00"))
    await session.flush()

    inv_prev = make_invoice(session, "INV-OB-PREV", po_id=po.id)
    await session.flush()
    session.add(InvoiceLineLedger(
        purchase_order_item_id=po_item.id,
        invoice_id=inv_prev.id,
        quantity_invoiced=Decimal("80"),
        unit_price=Decimal("1000.00"),
        invoiced_at=utc(2026, 1, 10),
    ))

    inv_curr = make_invoice(session, "INV-OB-CURR", po_id=po.id)
    await session.flush()
    session.add(InvoiceLineLedger(
        purchase_order_item_id=po_item.id,
        invoice_id=inv_curr.id,
        quantity_invoiced=Decimal("30"),
        unit_price=Decimal("1000.00"),
        invoiced_at=utc(2026, 1, 20),
    ))
    await session.flush()

    result = await session.execute(
        select(func.sum(InvoiceLineLedger.quantity_invoiced))
        .where(InvoiceLineLedger.purchase_order_item_id == po_item.id)
        .where(InvoiceLineLedger.invoice_id != inv_curr.id)
    )
    previously_invoiced = result.scalar() or Decimal("0")
    remaining = po_item.quantity - previously_invoiced
    current_qty = Decimal("30")
    overbilled = max(Decimal("0"), current_qty - remaining)

    assert previously_invoiced == Decimal("80")
    assert remaining == Decimal("20")
    assert current_qty > remaining, "Should be flagged as overbilling"
    assert overbilled == Decimal("10")


# ---------------------------------------------------------------------------
# 5. Monetary precision
# ---------------------------------------------------------------------------
async def test_monetary_precision_numeric(session: AsyncSession):
    """Financial values must round-trip through PostgreSQL NUMERIC without drift."""
    po = make_po(session, "PO-PREC-001")
    await session.flush()
    make_po_item(session, po.id, qty=Decimal("3"), price=Decimal("33333.33"))
    await session.flush()

    result = await session.execute(
        select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po.id)
    )
    item = result.scalar_one()
    # Decimal arithmetic — no floating-point drift
    assert item.unit_price == Decimal("33333.33")
    assert item.quantity == Decimal("3")


# ---------------------------------------------------------------------------
# 6. Audit and reconciliation result
# ---------------------------------------------------------------------------
async def test_audit_reconciliation_result(session: AsyncSession):
    po = make_po(session, "PO-AUD-001")
    await session.flush()
    inv = make_invoice(session, "INV-AUD-001", po_id=po.id)
    await session.flush()

    audit = Audit(
        invoice_id=inv.id,
        purchase_order_id=po.id,
        status=AuditStatus.COMPLETED,
        started_at=utc(2026, 1, 15),
        completed_at=utc(2026, 1, 15),
    )
    session.add(audit)
    await session.flush()

    result = ReconciliationResult(
        audit_id=audit.id,
        invoice_subtotal=Decimal("100000.00"),
        po_subtotal=Decimal("100000.00"),
        invoice_tax=Decimal("18000.00"),
        po_tax=Decimal("18000.00"),
        invoice_discount=Decimal("0.00"),
        po_discount=Decimal("0.00"),
        invoice_total=Decimal("118000.00"),
        po_total=Decimal("118000.00"),
        difference_amount=Decimal("0.00"),
        difference_percent=Decimal("0.0000"),
        overall_status=ReconciliationStatus.MATCHED,
    )
    session.add(result)
    await session.flush()

    fetched = await session.execute(
        select(ReconciliationResult).where(ReconciliationResult.audit_id == audit.id)
    )
    rr = fetched.scalar_one()
    assert rr.overall_status == ReconciliationStatus.MATCHED
    assert rr.difference_amount == Decimal("0.00")


# ---------------------------------------------------------------------------
# 7. Anomaly and evidence
# ---------------------------------------------------------------------------
async def test_anomaly_with_evidence(session: AsyncSession):
    po = make_po(session, "PO-ANO-001")
    await session.flush()
    inv = make_invoice(session, "INV-ANO-001", po_id=po.id, total=Decimal("131000.00"))
    await session.flush()

    audit = Audit(
        invoice_id=inv.id, purchase_order_id=po.id,
        status=AuditStatus.COMPLETED,
        started_at=utc(2026, 1, 15), completed_at=utc(2026, 1, 15),
    )
    session.add(audit)
    await session.flush()

    rr = ReconciliationResult(
        audit_id=audit.id,
        invoice_subtotal=Decimal("111111.11"), po_subtotal=Decimal("100000.00"),
        invoice_tax=Decimal("20000.00"), po_tax=Decimal("18000.00"),
        invoice_discount=Decimal("0.00"), po_discount=Decimal("0.00"),
        invoice_total=Decimal("131111.11"), po_total=Decimal("118000.00"),
        difference_amount=Decimal("13111.11"), difference_percent=Decimal("11.1111"),
        overall_status=ReconciliationStatus.ANOMALY,
    )
    session.add(rr)
    await session.flush()

    anomaly = Anomaly(
        reconciliation_result_id=rr.id,
        type=AnomalyType.PRICE_MISMATCH,
        severity=AnomalySeverity.HIGH,
        description="Unit price mismatch",
        expected_value=Decimal("100000.00"),
        actual_value=Decimal("111111.11"),
        difference_amount=Decimal("11111.11"),
        difference_percent=Decimal("11.1111"),
        financial_impact=Decimal("11111.11"),
        rule_triggered="UNIT_PRICE_EXCEEDS_PO_PRICE",
        status=AnomalyStatus.OPEN,
    )
    session.add(anomaly)
    await session.flush()

    evidence = Evidence(
        anomaly_id=anomaly.id,
        source_type=EvidenceSourceType.INVOICE_FIELD,
        page_number=2,
        field_path="items[0].unit_price",
        source_text="Product A × 1 @ 111,111.11",
        expected_value="100000.00",
        actual_value="111111.11",
    )
    session.add(evidence)
    await session.flush()

    ev_result = await session.execute(
        select(Evidence).where(Evidence.anomaly_id == anomaly.id)
    )
    ev = ev_result.scalar_one()
    assert ev.page_number == 2
    assert ev.field_path == "items[0].unit_price"
    assert ev.source_type == EvidenceSourceType.INVOICE_FIELD


# ---------------------------------------------------------------------------
# 8. Correction history — original value preserved
# ---------------------------------------------------------------------------
async def test_correction_preserves_original_value(session: AsyncSession):
    po = make_po(session, "PO-COR-001")
    await session.flush()
    inv = make_invoice(session, "INV-COR-001", po_id=po.id)
    await session.flush()

    audit = Audit(
        invoice_id=inv.id, purchase_order_id=po.id,
        status=AuditStatus.REVIEW_REQUIRED,
        started_at=utc(2026, 1, 15), completed_at=None,
    )
    session.add(audit)
    await session.flush()

    correction = AuditCorrection(
        audit_id=audit.id,
        invoice_id=inv.id,
        field_path="items[0].unit_price",
        original_value="45000.00",
        corrected_value="40000.00",
        reason="Invoice scan misread handwritten value",
        corrected_by="finance_user@example.com",
    )
    session.add(correction)
    await session.flush()

    result = await session.execute(
        select(AuditCorrection).where(AuditCorrection.audit_id == audit.id)
    )
    c = result.scalar_one()
    assert c.original_value == "45000.00"
    assert c.corrected_value == "40000.00"
    assert c.field_path == "items[0].unit_price"


# ---------------------------------------------------------------------------
# 9. Cascade delete — deleting a PO cascades to its items
# ---------------------------------------------------------------------------
async def test_cascade_delete_po_items(session: AsyncSession):
    po = make_po(session, "PO-CASCADE-001")
    await session.flush()
    make_po_item(session, po.id, line=1)
    make_po_item(session, po.id, line=2)
    await session.flush()

    await session.delete(po)
    await session.flush()

    result = await session.execute(
        select(PurchaseOrderItem).where(PurchaseOrderItem.purchase_order_id == po.id)
    )
    assert result.scalars().all() == []


# ---------------------------------------------------------------------------
# 10. Duplicate detection index signals
# ---------------------------------------------------------------------------
async def test_duplicate_detection_signals(session: AsyncSession):
    """
    Two invoices from the same vendor, same date, same grand_total
    should be identifiable via the dup-detection index columns.
    """
    inv_date = utc(2026, 5, 10)
    vendor = "Dup Detection Vendor"
    amount = Decimal("236000.00")

    inv1 = Invoice(
        invoice_number="INV-DUP-001",
        vendor_name=vendor,
        invoice_date=inv_date,
        currency="INR",
        subtotal=Decimal("200000.00"),
        tax_amount=Decimal("36000.00"),
        discount_amount=Decimal("0.00"),
        grand_total=amount,
        status=InvoiceStatus.APPROVED,
    )
    inv2 = Invoice(
        invoice_number="INV-DUP-002",
        vendor_name=vendor,
        invoice_date=inv_date,  # Same date
        currency="INR",
        subtotal=Decimal("200000.00"),
        tax_amount=Decimal("36000.00"),
        discount_amount=Decimal("0.00"),
        grand_total=amount,    # Same amount
        status=InvoiceStatus.PENDING_REVIEW,
    )
    session.add_all([inv1, inv2])
    await session.flush()

    result = await session.execute(
        select(Invoice).where(
            Invoice.vendor_name == vendor,
            Invoice.invoice_date == inv_date,
            Invoice.grand_total == amount,
        )
    )
    duplicates = result.scalars().all()
    assert len(duplicates) == 2, "Both invoices should be found by duplicate detection query"


# ---------------------------------------------------------------------------
# 11. InvoiceDocument and evidence linkage
# ---------------------------------------------------------------------------
async def test_invoice_document_and_evidence(session: AsyncSession):
    po = make_po(session, "PO-DOC-001")
    await session.flush()
    inv = make_invoice(session, "INV-DOC-001", po_id=po.id)
    await session.flush()

    doc = InvoiceDocument(
        invoice_id=inv.id,
        filename="invoice_scan_001.pdf",
        original_filename="Invoice_March_2026.pdf",
        mime_type="application/pdf",
        file_size=204800,
        storage_path="/uploads/2026/03/invoice_scan_001.pdf",
        page_count=2,
        document_hash="a" * 64,  # Placeholder SHA-256
    )
    session.add(doc)
    await session.flush()

    result = await session.execute(
        select(InvoiceDocument).where(InvoiceDocument.invoice_id == inv.id)
    )
    fetched = result.scalar_one()
    assert fetched.page_count == 2
    assert len(fetched.document_hash) == 64
