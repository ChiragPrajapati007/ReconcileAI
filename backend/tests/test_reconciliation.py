"""
Phase 3 — Reconciliation engine integration tests.

Every test creates raw PO + invoice data, calls ``reconcile_invoice()``,
and asserts the engine independently derives the correct result.

Test coverage
─────────────
 1. Clean match
 2. Price mismatch (overcharge)
 3. Valid partial invoice (no overbilling)
 4. Quantity overbilling
 5. Multiple partial invoices → full, then overbilling
 6. Missing item (full invoice context)
 7. Extra item
 8. Vendor mismatch
 9. No PO reference
10. Tax mismatch
11. Discount mismatch
12. Total mismatch (grand total PO comparison)
13. Duplicate invoice (vendor + date + total)
14. Same invoice number, different vendor → NOT duplicate
15. Idempotency
16. Decimal precision
17. Seed scenarios A-G through real engine
18. Partial invoice with uninvoiced PO line
19. Negative price difference (undercharge)
20. Duplicate document hash
21. Current invoice excluded from duplicate query
22. Previously invoiced qty > PO qty
23. Line-derived subtotal vs header subtotal
24. Expected grand-total calculation
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
import uuid

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem
from app.models.invoice import Invoice, InvoiceItem, InvoiceDocument
from app.models.audit import (
    Audit, Anomaly, ReconciliationResult, Evidence, InvoiceLineLedger,
)
from app.models.enums import (
    AnomalySeverity, AnomalyStatus, AnomalyType, AuditStatus,
    InvoiceStatus, POStatus, ReconciliationStatus,
)
from app.services.reconciliation.engine import reconcile_invoice, ReconciliationError


DT = datetime(2026, 1, 15, tzinfo=timezone.utc)
D = Decimal


# ── Helpers ───────────────────────────────────────────────────────────────

def make_po(
    session, po_number, vendor="Test Vendor", subtotal=D("100000"),
    tax=D("18000"), discount=D("0"), grand_total=D("118000"),
    status=POStatus.OPEN, items=None,
):
    po = PurchaseOrder(
        po_number=po_number, vendor_name=vendor, po_date=DT,
        currency="INR", subtotal=subtotal, tax_amount=tax,
        discount_amount=discount, grand_total=grand_total, status=status,
    )
    session.add(po)
    return po


def make_po_item(
    session, po, line_number, desc, qty, price, line_total=None, tax_rate=D("18.000"),
):
    if line_total is None:
        line_total = D(str(qty)) * D(str(price))
    item = PurchaseOrderItem(
        purchase_order_id=po.id, line_number=line_number,
        description=desc, normalized_description=desc.lower(),
        quantity=qty, unit_price=price, tax_rate_percent=tax_rate,
        discount=D("0"), line_total=line_total,
    )
    session.add(item)
    return item


def make_invoice(
    session, inv_number, vendor="Test Vendor", po=None,
    subtotal=D("100000"), tax=D("18000"), discount=D("0"),
    grand_total=D("118000"), status=InvoiceStatus.PENDING_REVIEW,
):
    inv = Invoice(
        invoice_number=inv_number, vendor_name=vendor,
        invoice_date=DT, currency="INR",
        po_number=po.po_number if po else None,
        purchase_order_id=po.id if po else None,
        subtotal=subtotal, tax_amount=tax, discount_amount=discount,
        grand_total=grand_total, status=status,
    )
    session.add(inv)
    return inv


def make_inv_item(
    session, inv, line_number, desc, qty, price, line_total=None, tax_rate=D("18.000"),
):
    if line_total is None:
        line_total = D(str(qty)) * D(str(price))
    item = InvoiceItem(
        invoice_id=inv.id, line_number=line_number,
        description=desc, normalized_description=desc.lower(),
        quantity=qty, unit_price=price, tax_rate_percent=tax_rate,
        discount=D("0"), line_total=line_total,
    )
    session.add(item)
    return item


def make_ledger(session, po_item, inv, qty, price):
    entry = InvoiceLineLedger(
        purchase_order_item_id=po_item.id, invoice_id=inv.id,
        quantity_invoiced=qty, unit_price=price, invoiced_at=DT,
    )
    session.add(entry)
    return entry


def find_anomaly(report, anomaly_type):
    """Find the first anomaly of a given type in the report."""
    for a in report.anomalies:
        if a.type == anomaly_type:
            return a
    return None


def find_all_anomalies(report, anomaly_type):
    """Find all anomalies of a given type."""
    return [a for a in report.anomalies if a.type == anomaly_type]


# ══════════════════════════════════════════════════════════════════════════
#  TEST 1: Clean match
# ══════════════════════════════════════════════════════════════════════════

async def test_clean_match(session: AsyncSession):
    """PO and invoice match exactly → MATCHED, zero anomalies."""
    po = make_po(session, "PO-CLEAN-001",
                 subtotal=D("100000"), tax=D("18000"), grand_total=D("118000"))
    await session.flush()
    make_po_item(session, po, 1, "Widget A", D("10"), D("10000"))
    await session.flush()

    inv = make_invoice(session, "INV-CLEAN-001", po=po,
                       subtotal=D("100000"), tax=D("18000"), grand_total=D("118000"))
    await session.flush()
    make_inv_item(session, inv, 1, "Widget A", D("10"), D("10000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    assert report.overall_status == "matched"
    assert len(report.anomalies) == 0
    assert report.audit_id is not None


# ══════════════════════════════════════════════════════════════════════════
#  TEST 2: Price mismatch (overcharge)
# ══════════════════════════════════════════════════════════════════════════

async def test_price_mismatch_overcharge(session: AsyncSession):
    """Invoice unit price > PO → PRICE_MISMATCH with correct financial impact."""
    po = make_po(session, "PO-PRICE-001",
                 subtotal=D("400000"), tax=D("72000"), grand_total=D("472000"))
    await session.flush()
    make_po_item(session, po, 1, "Laptop", D("10"), D("40000"), line_total=D("400000"))
    await session.flush()

    inv = make_invoice(session, "INV-PRICE-001", po=po,
                       subtotal=D("450000"), tax=D("81000"), grand_total=D("531000"))
    await session.flush()
    make_inv_item(session, inv, 1, "Laptop", D("10"), D("45000"), line_total=D("450000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    assert report.overall_status == "anomaly"
    pm = find_anomaly(report, "PRICE_MISMATCH")
    assert pm is not None
    assert pm.expected_value == D("40000")
    assert pm.actual_value == D("45000")
    assert pm.difference_amount == D("5000.00")
    assert pm.difference_percent == D("12.5000")
    assert pm.financial_impact == D("50000.00")  # 5000 × 10


# ══════════════════════════════════════════════════════════════════════════
#  TEST 3: Valid partial invoice (no overbilling)
# ══════════════════════════════════════════════════════════════════════════

async def test_valid_partial_invoice(session: AsyncSession):
    """PO=100, prev=40, curr=30 → remaining=60, overbilled=0 → MATCHED."""
    po = make_po(session, "PO-PARTIAL-001",
                 subtotal=D("1000000"), tax=D("180000"), grand_total=D("1180000"),
                 status=POStatus.PARTIALLY_INVOICED)
    await session.flush()
    po_item = make_po_item(session, po, 1, "Bulk Widget", D("100"), D("10000"),
                           line_total=D("1000000"))
    await session.flush()

    # Previous invoice covering 40 units
    prev_inv = make_invoice(session, "INV-PARTIAL-PREV", po=po,
                            subtotal=D("400000"), tax=D("72000"), grand_total=D("472000"))
    await session.flush()
    make_ledger(session, po_item, prev_inv, D("40"), D("10000"))
    await session.flush()

    # Current invoice covering 30 units
    inv = make_invoice(session, "INV-PARTIAL-001", po=po,
                       subtotal=D("300000"), tax=D("54000"), grand_total=D("354000"))
    await session.flush()
    make_inv_item(session, inv, 1, "Bulk Widget", D("30"), D("10000"),
                  line_total=D("300000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    assert report.overall_status != "review_required"
    overbilling = find_anomaly(report, "QUANTITY_OVERBILLING")
    assert overbilling is None


# ══════════════════════════════════════════════════════════════════════════
#  TEST 4: Quantity overbilling
# ══════════════════════════════════════════════════════════════════════════

async def test_quantity_overbilling(session: AsyncSession):
    """PO=100, prev=80, curr=30 → remaining=20, overbilled=10."""
    po = make_po(session, "PO-OVER-001",
                 subtotal=D("1000000"), tax=D("180000"), grand_total=D("1180000"),
                 status=POStatus.PARTIALLY_INVOICED)
    await session.flush()
    po_item = make_po_item(session, po, 1, "Bulk Widget", D("100"), D("10000"),
                           line_total=D("1000000"))
    await session.flush()

    prev_inv = make_invoice(session, "INV-OVER-PREV", po=po,
                            subtotal=D("800000"), tax=D("144000"), grand_total=D("944000"))
    await session.flush()
    make_ledger(session, po_item, prev_inv, D("80"), D("10000"))
    await session.flush()

    inv = make_invoice(session, "INV-OVER-001", po=po,
                       subtotal=D("300000"), tax=D("54000"), grand_total=D("354000"))
    await session.flush()
    make_inv_item(session, inv, 1, "Bulk Widget", D("30"), D("10000"),
                  line_total=D("300000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    assert report.overall_status == "anomaly"
    ob = find_anomaly(report, "QUANTITY_OVERBILLING")
    assert ob is not None
    assert ob.difference_amount == D("10")  # overbilled qty
    assert ob.expected_value == D("20")  # remaining
    assert ob.actual_value == D("30")    # current qty


# ══════════════════════════════════════════════════════════════════════════
#  TEST 5: Multiple partial invoices → full, then overbilling
# ══════════════════════════════════════════════════════════════════════════

async def test_multiple_partials_then_overbilling(session: AsyncSession):
    """30+30+40=100 (no overbilling), then +1 (overbilling)."""
    po = make_po(session, "PO-MULTI-001",
                 subtotal=D("100000"), tax=D("18000"), grand_total=D("118000"))
    await session.flush()
    po_item = make_po_item(session, po, 1, "Part X", D("100"), D("1000"),
                           line_total=D("100000"))
    await session.flush()

    # Invoices A=30, B=30, C=40
    for label, qty in [("A", 30), ("B", 30), ("C", 40)]:
        prev = make_invoice(session, f"INV-MULTI-{label}", po=po,
                            subtotal=D(str(qty * 1000)), tax=D(str(int(qty * 180))),
                            grand_total=D(str(int(qty * 1180))))
        await session.flush()
        make_ledger(session, po_item, prev, D(str(qty)), D("1000"))
        await session.flush()

    # Invoice D = 1 unit → overbilling
    inv_d = make_invoice(session, "INV-MULTI-D", po=po,
                         subtotal=D("1000"), tax=D("180"), grand_total=D("1180"))
    await session.flush()
    make_inv_item(session, inv_d, 1, "Part X", D("1"), D("1000"), line_total=D("1000"))
    await session.flush()

    report = await reconcile_invoice(session, inv_d.id)

    assert report.overall_status == "anomaly"
    ob = find_anomaly(report, "QUANTITY_OVERBILLING")
    assert ob is not None
    assert ob.difference_amount == D("1")


# ══════════════════════════════════════════════════════════════════════════
#  TEST 6: Missing item (full invoice context)
# ══════════════════════════════════════════════════════════════════════════

async def test_missing_item(session: AsyncSession):
    """PO has 3 items at full qty, invoice only has 2 → MISSING_ITEM."""
    po = make_po(session, "PO-MISS-001",
                 subtotal=D("72000"), tax=D("12960"), grand_total=D("84960"))
    await session.flush()
    make_po_item(session, po, 1, "Laptop", D("1"), D("65000"), line_total=D("65000"))
    make_po_item(session, po, 2, "Mouse", D("1"), D("2500"), line_total=D("2500"))
    make_po_item(session, po, 3, "Keyboard", D("1"), D("4500"), line_total=D("4500"))
    await session.flush()

    inv = make_invoice(session, "INV-MISS-001", po=po,
                       subtotal=D("67500"), tax=D("12150"), grand_total=D("79650"))
    await session.flush()
    make_inv_item(session, inv, 1, "Laptop", D("1"), D("65000"), line_total=D("65000"))
    make_inv_item(session, inv, 2, "Mouse", D("1"), D("2500"), line_total=D("2500"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    missing = find_anomaly(report, "MISSING_ITEM")
    assert missing is not None
    assert "Keyboard" in missing.description


# ══════════════════════════════════════════════════════════════════════════
#  TEST 7: Extra item
# ══════════════════════════════════════════════════════════════════════════

async def test_extra_item(session: AsyncSession):
    """Invoice has an item not on PO → EXTRA_ITEM."""
    po = make_po(session, "PO-EXTRA-001",
                 subtotal=D("65000"), tax=D("11700"), grand_total=D("76700"))
    await session.flush()
    make_po_item(session, po, 1, "Laptop", D("1"), D("65000"), line_total=D("65000"))
    await session.flush()

    inv = make_invoice(session, "INV-EXTRA-001", po=po,
                       subtotal=D("67500"), tax=D("12150"), grand_total=D("79650"))
    await session.flush()
    make_inv_item(session, inv, 1, "Laptop", D("1"), D("65000"), line_total=D("65000"))
    make_inv_item(session, inv, 2, "Mouse", D("1"), D("2500"), line_total=D("2500"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    extra = find_anomaly(report, "EXTRA_ITEM")
    assert extra is not None
    assert "Mouse" in extra.description


# ══════════════════════════════════════════════════════════════════════════
#  TEST 8: Vendor mismatch
# ══════════════════════════════════════════════════════════════════════════

async def test_vendor_mismatch(session: AsyncSession):
    """Invoice vendor ≠ PO vendor → VENDOR_MISMATCH."""
    po = make_po(session, "PO-VEND-001", vendor="Alpha Corp",
                 subtotal=D("10000"), tax=D("1800"), grand_total=D("11800"))
    await session.flush()
    make_po_item(session, po, 1, "Widget", D("1"), D("10000"), line_total=D("10000"))
    await session.flush()

    inv = make_invoice(session, "INV-VEND-001", vendor="Beta Industries", po=po,
                       subtotal=D("10000"), tax=D("1800"), grand_total=D("11800"))
    await session.flush()
    make_inv_item(session, inv, 1, "Widget", D("1"), D("10000"), line_total=D("10000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    vm = find_anomaly(report, "VENDOR_MISMATCH")
    assert vm is not None
    assert report.overall_status == "review_required"


# ══════════════════════════════════════════════════════════════════════════
#  TEST 9: No PO reference
# ══════════════════════════════════════════════════════════════════════════

async def test_no_po_reference(session: AsyncSession):
    """Invoice with no PO → PO_MISMATCH, REVIEW_REQUIRED."""
    inv = make_invoice(session, "INV-NOPO-001", po=None,
                       subtotal=D("50000"), tax=D("9000"), grand_total=D("59000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    assert report.overall_status == "review_required"
    po_m = find_anomaly(report, "PO_MISMATCH")
    assert po_m is not None


# ══════════════════════════════════════════════════════════════════════════
#  TEST 10: Tax mismatch
# ══════════════════════════════════════════════════════════════════════════

async def test_tax_mismatch(session: AsyncSession):
    """Invoice tax ≠ PO tax → TAX_MISMATCH."""
    po = make_po(session, "PO-TAX-001",
                 subtotal=D("100000"), tax=D("18000"), grand_total=D("118000"))
    await session.flush()
    make_po_item(session, po, 1, "Widget", D("10"), D("10000"), line_total=D("100000"))
    await session.flush()

    # Same subtotal, different tax
    inv = make_invoice(session, "INV-TAX-001", po=po,
                       subtotal=D("100000"), tax=D("20000"), grand_total=D("120000"))
    await session.flush()
    make_inv_item(session, inv, 1, "Widget", D("10"), D("10000"), line_total=D("100000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    tax_a = find_anomaly(report, "TAX_MISMATCH")
    assert tax_a is not None
    assert tax_a.difference_amount == D("2000.00")


# ══════════════════════════════════════════════════════════════════════════
#  TEST 11: Discount mismatch
# ══════════════════════════════════════════════════════════════════════════

async def test_discount_mismatch(session: AsyncSession):
    """Invoice discount ≠ PO discount → DISCOUNT_MISMATCH."""
    po = make_po(session, "PO-DISC-001",
                 subtotal=D("100000"), tax=D("18000"), discount=D("5000"),
                 grand_total=D("113000"))
    await session.flush()
    make_po_item(session, po, 1, "Widget", D("10"), D("10000"), line_total=D("100000"))
    await session.flush()

    inv = make_invoice(session, "INV-DISC-001", po=po,
                       subtotal=D("100000"), tax=D("18000"), discount=D("2000"),
                       grand_total=D("116000"))
    await session.flush()
    make_inv_item(session, inv, 1, "Widget", D("10"), D("10000"), line_total=D("100000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    disc = find_anomaly(report, "DISCOUNT_MISMATCH")
    assert disc is not None
    assert disc.difference_amount == D("-3000.00")  # invoice discount less than PO


# ══════════════════════════════════════════════════════════════════════════
#  TEST 12: Total mismatch (grand total PO comparison, no line anomalies)
# ══════════════════════════════════════════════════════════════════════════

async def test_total_mismatch(session: AsyncSession):
    """Grand total differs without line-level anomalies → TOTAL_MISMATCH."""
    po = make_po(session, "PO-TOTAL-001",
                 subtotal=D("100000"), tax=D("18000"), grand_total=D("118000"))
    await session.flush()
    make_po_item(session, po, 1, "Widget", D("10"), D("10000"), line_total=D("100000"))
    await session.flush()

    # Same line items but header grand total is wrong
    inv = make_invoice(session, "INV-TOTAL-001", po=po,
                       subtotal=D("100000"), tax=D("18000"), grand_total=D("120000"))
    await session.flush()
    make_inv_item(session, inv, 1, "Widget", D("10"), D("10000"), line_total=D("100000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    total_a = find_anomaly(report, "TOTAL_MISMATCH")
    assert total_a is not None


# ══════════════════════════════════════════════════════════════════════════
#  TEST 13: Duplicate invoice (vendor + date + total)
# ══════════════════════════════════════════════════════════════════════════

async def test_duplicate_invoice(session: AsyncSession):
    """Same vendor, date, total → DUPLICATE_INVOICE."""
    po = make_po(session, "PO-DUP-001",
                 subtotal=D("100000"), tax=D("18000"), grand_total=D("118000"))
    await session.flush()
    make_po_item(session, po, 1, "Widget", D("10"), D("10000"), line_total=D("100000"))
    await session.flush()

    # First invoice
    inv1 = make_invoice(session, "INV-DUP-001", po=po,
                        subtotal=D("100000"), tax=D("18000"), grand_total=D("118000"))
    await session.flush()
    make_inv_item(session, inv1, 1, "Widget", D("10"), D("10000"), line_total=D("100000"))
    await session.flush()

    # Second invoice — same vendor, date, total but different number
    inv2 = make_invoice(session, "INV-DUP-002", po=po,
                        subtotal=D("100000"), tax=D("18000"), grand_total=D("118000"))
    await session.flush()
    make_inv_item(session, inv2, 1, "Widget", D("10"), D("10000"), line_total=D("100000"))
    await session.flush()

    report = await reconcile_invoice(session, inv2.id)

    dup = find_anomaly(report, "DUPLICATE_INVOICE")
    assert dup is not None


# ══════════════════════════════════════════════════════════════════════════
#  TEST 14: Same invoice number, different vendor → NOT duplicate
# ══════════════════════════════════════════════════════════════════════════

async def test_same_inv_number_different_vendor_not_duplicate(session: AsyncSession):
    """Two vendors using same invoice number must NOT be duplicates."""
    po1 = make_po(session, "PO-NDVND-001", vendor="Vendor Alpha",
                  subtotal=D("50000"), tax=D("9000"), grand_total=D("59000"))
    await session.flush()
    make_po_item(session, po1, 1, "Widget", D("5"), D("10000"), line_total=D("50000"))
    await session.flush()

    po2 = make_po(session, "PO-NDVND-002", vendor="Vendor Beta",
                  subtotal=D("50000"), tax=D("9000"), grand_total=D("59000"))
    await session.flush()
    make_po_item(session, po2, 1, "Widget", D("5"), D("10000"), line_total=D("50000"))
    await session.flush()

    inv1 = make_invoice(session, "INV-001", vendor="Vendor Alpha", po=po1,
                        subtotal=D("50000"), tax=D("9000"), grand_total=D("59000"))
    await session.flush()
    make_inv_item(session, inv1, 1, "Widget", D("5"), D("10000"), line_total=D("50000"))
    await session.flush()

    inv2 = make_invoice(session, "INV-001", vendor="Vendor Beta", po=po2,
                        subtotal=D("50000"), tax=D("9000"), grand_total=D("59000"))
    await session.flush()
    make_inv_item(session, inv2, 1, "Widget", D("5"), D("10000"), line_total=D("50000"))
    await session.flush()

    report = await reconcile_invoice(session, inv2.id)

    dup = find_anomaly(report, "DUPLICATE_INVOICE")
    assert dup is None


# ══════════════════════════════════════════════════════════════════════════
#  TEST 15: Idempotency
# ══════════════════════════════════════════════════════════════════════════

async def test_idempotency(session: AsyncSession):
    """Running reconcile_invoice twice produces same result, no duplicates."""
    po = make_po(session, "PO-IDEMP-001",
                 subtotal=D("100000"), tax=D("18000"), grand_total=D("118000"))
    await session.flush()
    po_item = make_po_item(session, po, 1, "Widget", D("10"), D("10000"),
                           line_total=D("100000"))
    await session.flush()

    inv = make_invoice(session, "INV-IDEMP-001", po=po,
                       subtotal=D("100000"), tax=D("18000"), grand_total=D("118000"))
    await session.flush()
    make_inv_item(session, inv, 1, "Widget", D("10"), D("10000"), line_total=D("100000"))
    await session.flush()

    # Run twice
    report1 = await reconcile_invoice(session, inv.id)
    report2 = await reconcile_invoice(session, inv.id)

    assert report1.overall_status == report2.overall_status == "matched"
    assert len(report1.anomalies) == len(report2.anomalies) == 0

    # Verify exactly TWO audits exist (Phase 7 preserves history)
    audit_count = await session.execute(
        select(func.count()).select_from(Audit).where(Audit.invoice_id == inv.id)
    )
    assert audit_count.scalar() == 2

    # Verify ledger has correct quantity (not doubled)
    ledger_sum = await session.execute(
        select(func.sum(InvoiceLineLedger.quantity_invoiced)).where(
            InvoiceLineLedger.invoice_id == inv.id
        )
    )
    assert Decimal(str(ledger_sum.scalar())) == D("10")


# ══════════════════════════════════════════════════════════════════════════
#  TEST 16: Decimal precision
# ══════════════════════════════════════════════════════════════════════════

async def test_decimal_precision(session: AsyncSession):
    """Verify no floating-point drift in calculations."""
    po = make_po(session, "PO-DEC-001",
                 subtotal=D("99999.99"), tax=D("17999.998"), grand_total=D("117999.99"))
    await session.flush()
    # 3 items with prices that would cause float drift
    make_po_item(session, po, 1, "A", D("3"), D("33333.33"), line_total=D("99999.99"))
    await session.flush()

    inv = make_invoice(session, "INV-DEC-001", po=po,
                       subtotal=D("99999.99"), tax=D("17999.998"),
                       grand_total=D("117999.99"))
    await session.flush()
    make_inv_item(session, inv, 1, "A", D("3"), D("33333.33"), line_total=D("99999.99"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    # No price mismatch — values are identical Decimals
    pm = find_anomaly(report, "PRICE_MISMATCH")
    assert pm is None


# ══════════════════════════════════════════════════════════════════════════
#  TEST 18: Partial invoice with uninvoiced PO line
# ══════════════════════════════════════════════════════════════════════════

async def test_partial_invoice_uninvoiced_po_line(session: AsyncSession):
    """PO: Laptop 100, Mouse 100.  Invoice: Laptop 30.
    Mouse must NOT be MISSING_ITEM — it's just uninvoiced."""
    po = make_po(session, "PO-PARTMISS-001",
                 subtotal=D("1200000"), tax=D("216000"), grand_total=D("1416000"))
    await session.flush()
    make_po_item(session, po, 1, "Laptop", D("100"), D("10000"), line_total=D("1000000"))
    make_po_item(session, po, 2, "Mouse", D("100"), D("2000"), line_total=D("200000"))
    await session.flush()

    inv = make_invoice(session, "INV-PARTMISS-001", po=po,
                       subtotal=D("300000"), tax=D("54000"), grand_total=D("354000"))
    await session.flush()
    make_inv_item(session, inv, 1, "Laptop", D("30"), D("10000"), line_total=D("300000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    missing = find_anomaly(report, "MISSING_ITEM")
    assert missing is None, "Mouse should not be flagged as MISSING_ITEM in a partial invoice"


# ══════════════════════════════════════════════════════════════════════════
#  TEST 19: Negative price difference (undercharge)
# ══════════════════════════════════════════════════════════════════════════

async def test_negative_price_difference(session: AsyncSession):
    """Invoice price < PO price → PRICE_MISMATCH with negative financial impact."""
    po = make_po(session, "PO-NEGP-001",
                 subtotal=D("50000"), tax=D("9000"), grand_total=D("59000"))
    await session.flush()
    make_po_item(session, po, 1, "Widget", D("10"), D("5000"), line_total=D("50000"))
    await session.flush()

    inv = make_invoice(session, "INV-NEGP-001", po=po,
                       subtotal=D("40000"), tax=D("7200"), grand_total=D("47200"))
    await session.flush()
    make_inv_item(session, inv, 1, "Widget", D("10"), D("4000"), line_total=D("40000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    pm = find_anomaly(report, "PRICE_MISMATCH")
    assert pm is not None
    assert pm.difference_amount == D("-1000.00")  # negative = undercharge
    assert pm.financial_impact == D("-10000.00")


# ══════════════════════════════════════════════════════════════════════════
#  TEST 20: Duplicate document hash
# ══════════════════════════════════════════════════════════════════════════

async def test_duplicate_document_hash(session: AsyncSession):
    """Same document hash on two invoices from same vendor → DUPLICATE_INVOICE."""
    po = make_po(session, "PO-HASH-001",
                 subtotal=D("10000"), tax=D("1800"), grand_total=D("11800"))
    await session.flush()
    make_po_item(session, po, 1, "Widget", D("1"), D("10000"), line_total=D("10000"))
    await session.flush()

    inv1 = make_invoice(session, "INV-HASH-001", po=po,
                        subtotal=D("10000"), tax=D("1800"), grand_total=D("11800"))
    await session.flush()
    doc1 = InvoiceDocument(
        invoice_id=inv1.id, filename="inv.pdf", original_filename="inv.pdf",
        mime_type="application/pdf", file_size=1024, storage_path="/uploads/inv.pdf",
        document_hash="abc123deadbeef" * 4,  # 64-char hash
    )
    session.add(doc1)
    await session.flush()

    inv2 = make_invoice(session, "INV-HASH-002", po=po,
                        subtotal=D("10000"), tax=D("1800"), grand_total=D("11800"))
    await session.flush()
    make_inv_item(session, inv2, 1, "Widget", D("1"), D("10000"), line_total=D("10000"))
    doc2 = InvoiceDocument(
        invoice_id=inv2.id, filename="inv_copy.pdf", original_filename="inv_copy.pdf",
        mime_type="application/pdf", file_size=1024, storage_path="/uploads/inv_copy.pdf",
        document_hash="abc123deadbeef" * 4,  # same hash
    )
    session.add(doc2)
    await session.flush()

    report = await reconcile_invoice(session, inv2.id)

    dup = find_anomaly(report, "DUPLICATE_INVOICE")
    assert dup is not None
    assert "hash" in dup.rule_triggered.lower() or "HASH" in dup.rule_triggered


# ══════════════════════════════════════════════════════════════════════════
#  TEST 21: Current invoice excluded from duplicate query
# ══════════════════════════════════════════════════════════════════════════

async def test_current_invoice_not_self_duplicate(session: AsyncSession):
    """An invoice must not be flagged as a duplicate of itself."""
    po = make_po(session, "PO-SELF-001",
                 subtotal=D("10000"), tax=D("1800"), grand_total=D("11800"))
    await session.flush()
    make_po_item(session, po, 1, "Widget", D("1"), D("10000"), line_total=D("10000"))
    await session.flush()

    inv = make_invoice(session, "INV-SELF-001", po=po,
                       subtotal=D("10000"), tax=D("1800"), grand_total=D("11800"))
    await session.flush()
    make_inv_item(session, inv, 1, "Widget", D("1"), D("10000"), line_total=D("10000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    dup = find_anomaly(report, "DUPLICATE_INVOICE")
    assert dup is None, "Invoice must not be a duplicate of itself"


# ══════════════════════════════════════════════════════════════════════════
#  TEST 22: Previously invoiced qty > PO qty
# ══════════════════════════════════════════════════════════════════════════

async def test_previously_over_invoiced(session: AsyncSession):
    """PO=100, previously invoiced=110, current=5 → already over-invoiced."""
    po = make_po(session, "PO-PREVOVER-001",
                 subtotal=D("100000"), tax=D("18000"), grand_total=D("118000"))
    await session.flush()
    po_item = make_po_item(session, po, 1, "Widget", D("100"), D("1000"),
                           line_total=D("100000"))
    await session.flush()

    prev_inv = make_invoice(session, "INV-PREVOVER-PREV", po=po,
                            subtotal=D("110000"), tax=D("19800"),
                            grand_total=D("129800"))
    await session.flush()
    make_ledger(session, po_item, prev_inv, D("110"), D("1000"))
    await session.flush()

    inv = make_invoice(session, "INV-PREVOVER-001", po=po,
                       subtotal=D("5000"), tax=D("900"), grand_total=D("5900"))
    await session.flush()
    make_inv_item(session, inv, 1, "Widget", D("5"), D("1000"), line_total=D("5000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    ob_list = find_all_anomalies(report, "QUANTITY_OVERBILLING")
    assert len(ob_list) >= 1
    # remaining_before should be 0 (clamped), overbilled should be 5
    ob = ob_list[0]
    assert ob.expected_value == D("0")  # remaining was 0
    assert ob.actual_value == D("5")


# ══════════════════════════════════════════════════════════════════════════
#  TEST 23: Line-derived subtotal vs header subtotal
# ══════════════════════════════════════════════════════════════════════════

async def test_line_derived_subtotal_inconsistency(session: AsyncSession):
    """Sum of line items ≠ header subtotal → TOTAL_MISMATCH."""
    po = make_po(session, "PO-LSUB-001",
                 subtotal=D("100000"), tax=D("18000"), grand_total=D("118000"))
    await session.flush()
    make_po_item(session, po, 1, "Widget", D("10"), D("10000"), line_total=D("100000"))
    await session.flush()

    # Invoice header says 100000 subtotal but items sum to 90000
    inv = make_invoice(session, "INV-LSUB-001", po=po,
                       subtotal=D("100000"), tax=D("18000"), grand_total=D("118000"))
    await session.flush()
    make_inv_item(session, inv, 1, "Widget", D("10"), D("9000"), line_total=D("90000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    total_anomalies = find_all_anomalies(report, "TOTAL_MISMATCH")
    # Should detect line-derived subtotal inconsistency
    rules = [a.rule_triggered for a in total_anomalies]
    assert "INTERNAL_SUBTOTAL_INCONSISTENCY" in rules


# ══════════════════════════════════════════════════════════════════════════
#  TEST 24: Expected grand-total calculation
# ══════════════════════════════════════════════════════════════════════════

async def test_expected_grand_total_arithmetic(session: AsyncSession):
    """subtotal + tax - discount ≠ grand_total → TOTAL_MISMATCH."""
    po = make_po(session, "PO-ARITH-001",
                 subtotal=D("100000"), tax=D("18000"), grand_total=D("118000"))
    await session.flush()
    make_po_item(session, po, 1, "Widget", D("10"), D("10000"), line_total=D("100000"))
    await session.flush()

    # subtotal(100000) + tax(18000) - discount(0) = 118000, but grand_total = 115000
    inv = make_invoice(session, "INV-ARITH-001", po=po,
                       subtotal=D("100000"), tax=D("18000"), discount=D("0"),
                       grand_total=D("115000"))
    await session.flush()
    make_inv_item(session, inv, 1, "Widget", D("10"), D("10000"), line_total=D("100000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)

    total_anomalies = find_all_anomalies(report, "TOTAL_MISMATCH")
    rules = [a.rule_triggered for a in total_anomalies]
    assert "INVOICE_ARITHMETIC_ERROR" in rules


# ══════════════════════════════════════════════════════════════════════════
#  TEST 17: Seed scenarios A-G through real engine
# ══════════════════════════════════════════════════════════════════════════

async def test_seed_scenario_a_clean_match(session: AsyncSession):
    """Scenario A: PO-2026-001 / INV-2026-001 → MATCHED."""
    po = make_po(session, "PO-SEED-A", vendor="Rajan Electronics",
                 subtotal=D("200000"), tax=D("36000"), grand_total=D("236000"))
    await session.flush()
    make_po_item(session, po, 1, "HP Laptop 15", D("5"), D("40000"),
                 line_total=D("200000"))
    await session.flush()

    inv = make_invoice(session, "INV-SEED-A", vendor="Rajan Electronics", po=po,
                       subtotal=D("200000"), tax=D("36000"), grand_total=D("236000"))
    await session.flush()
    make_inv_item(session, inv, 1, "HP Laptop 15", D("5"), D("40000"),
                  line_total=D("200000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)
    assert report.overall_status == "matched"
    assert len(report.anomalies) == 0


async def test_seed_scenario_b_price_mismatch(session: AsyncSession):
    """Scenario B: PO ₹40k, Invoice ₹45k → PRICE_MISMATCH."""
    po = make_po(session, "PO-SEED-B", vendor="Rajan Electronics",
                 subtotal=D("200000"), tax=D("36000"), grand_total=D("236000"))
    await session.flush()
    make_po_item(session, po, 1, "HP Laptop 15", D("5"), D("40000"),
                 line_total=D("200000"))
    await session.flush()

    inv = make_invoice(session, "INV-SEED-B", vendor="Rajan Electronics", po=po,
                       subtotal=D("225000"), tax=D("40500"), grand_total=D("265500"))
    await session.flush()
    make_inv_item(session, inv, 1, "HP Laptop 15", D("5"), D("45000"),
                  line_total=D("225000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)
    assert report.overall_status == "anomaly"
    pm = find_anomaly(report, "PRICE_MISMATCH")
    assert pm is not None
    assert pm.difference_percent == D("12.5000")


async def test_seed_scenario_c_valid_partial(session: AsyncSession):
    """Scenario C: PO=100, prev=40, curr=30 → valid partial, no overbilling."""
    po = make_po(session, "PO-SEED-C", vendor="BrightStar Solutions",
                 subtotal=D("1000000"), tax=D("180000"), grand_total=D("1180000"),
                 status=POStatus.PARTIALLY_INVOICED)
    await session.flush()
    po_item = make_po_item(session, po, 1, "Dell Monitor 24-inch", D("100"),
                           D("10000"), line_total=D("1000000"))
    await session.flush()

    prev = make_invoice(session, "INV-SEED-C-PREV", vendor="BrightStar Solutions",
                        po=po, subtotal=D("400000"), tax=D("72000"),
                        grand_total=D("472000"))
    await session.flush()
    make_ledger(session, po_item, prev, D("40"), D("10000"))
    await session.flush()

    inv = make_invoice(session, "INV-SEED-C", vendor="BrightStar Solutions", po=po,
                       subtotal=D("300000"), tax=D("54000"), grand_total=D("354000"))
    await session.flush()
    make_inv_item(session, inv, 1, "Dell Monitor 24-inch", D("30"), D("10000"),
                  line_total=D("300000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)
    ob = find_anomaly(report, "QUANTITY_OVERBILLING")
    assert ob is None


async def test_seed_scenario_d_overbilling(session: AsyncSession):
    """Scenario D: PO=100, prev=80, curr=30 → overbilled=10."""
    po = make_po(session, "PO-SEED-D", vendor="BrightStar Solutions",
                 subtotal=D("1000000"), tax=D("180000"), grand_total=D("1180000"),
                 status=POStatus.PARTIALLY_INVOICED)
    await session.flush()
    po_item = make_po_item(session, po, 1, "Dell Monitor 24-inch", D("100"),
                           D("10000"), line_total=D("1000000"))
    await session.flush()

    prev = make_invoice(session, "INV-SEED-D-PREV", vendor="BrightStar Solutions",
                        po=po, subtotal=D("800000"), tax=D("144000"),
                        grand_total=D("944000"))
    await session.flush()
    make_ledger(session, po_item, prev, D("80"), D("10000"))
    await session.flush()

    inv = make_invoice(session, "INV-SEED-D", vendor="BrightStar Solutions", po=po,
                       subtotal=D("300000"), tax=D("54000"), grand_total=D("354000"))
    await session.flush()
    make_inv_item(session, inv, 1, "Dell Monitor 24-inch", D("30"), D("10000"),
                  line_total=D("300000"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)
    assert report.overall_status == "anomaly"
    ob = find_anomaly(report, "QUANTITY_OVERBILLING")
    assert ob is not None
    assert ob.difference_amount == D("10")


async def test_seed_scenario_e_duplicate(session: AsyncSession):
    """Scenario E: Duplicate invoice → DUPLICATE_INVOICE."""
    po = make_po(session, "PO-SEED-E", vendor="Rajan Electronics",
                 subtotal=D("200000"), tax=D("36000"), grand_total=D("236000"))
    await session.flush()
    make_po_item(session, po, 1, "HP Laptop 15", D("5"), D("40000"),
                 line_total=D("200000"))
    await session.flush()

    inv1 = make_invoice(session, "INV-SEED-E1", vendor="Rajan Electronics", po=po,
                        subtotal=D("200000"), tax=D("36000"), grand_total=D("236000"))
    await session.flush()

    inv2 = make_invoice(session, "INV-SEED-E2", vendor="Rajan Electronics", po=po,
                        subtotal=D("200000"), tax=D("36000"), grand_total=D("236000"))
    await session.flush()
    make_inv_item(session, inv2, 1, "HP Laptop 15", D("5"), D("40000"),
                  line_total=D("200000"))
    await session.flush()

    report = await reconcile_invoice(session, inv2.id)
    dup = find_anomaly(report, "DUPLICATE_INVOICE")
    assert dup is not None


async def test_seed_scenario_f_missing_item(session: AsyncSession):
    """Scenario F: 3 PO items, 2 invoiced at full qty → Keyboard MISSING_ITEM."""
    po = make_po(session, "PO-SEED-F", vendor="TechSupply India",
                 subtotal=D("72000"), tax=D("12960"), grand_total=D("84960"),
                 status=POStatus.PARTIALLY_INVOICED)
    await session.flush()
    make_po_item(session, po, 1, "Lenovo ThinkPad E14", D("1"), D("65000"),
                 line_total=D("65000"))
    make_po_item(session, po, 2, "Logitech Wireless Mouse", D("1"), D("2500"),
                 line_total=D("2500"))
    make_po_item(session, po, 3, "Zebronics Mechanical Keyboard", D("1"), D("4500"),
                 line_total=D("4500"))
    await session.flush()

    inv = make_invoice(session, "INV-SEED-F", vendor="TechSupply India", po=po,
                       subtotal=D("67500"), tax=D("12150"), grand_total=D("79650"))
    await session.flush()
    make_inv_item(session, inv, 1, "Lenovo ThinkPad E14", D("1"), D("65000"),
                  line_total=D("65000"))
    make_inv_item(session, inv, 2, "Logitech Wireless Mouse", D("1"), D("2500"),
                  line_total=D("2500"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)
    missing = find_anomaly(report, "MISSING_ITEM")
    assert missing is not None
    assert "Keyboard" in missing.description


async def test_seed_scenario_g_extra_item(session: AsyncSession):
    """Scenario G: Invoice has Mouse not on PO → EXTRA_ITEM."""
    po = make_po(session, "PO-SEED-G", vendor="GreenTech Peripherals",
                 subtotal=D("65000"), tax=D("11700"), grand_total=D("76700"))
    await session.flush()
    make_po_item(session, po, 1, "Lenovo ThinkPad E14", D("1"), D("65000"),
                 line_total=D("65000"))
    await session.flush()

    inv = make_invoice(session, "INV-SEED-G", vendor="GreenTech Peripherals", po=po,
                       subtotal=D("67500"), tax=D("12150"), grand_total=D("79650"))
    await session.flush()
    make_inv_item(session, inv, 1, "Lenovo ThinkPad E14", D("1"), D("65000"),
                  line_total=D("65000"))
    make_inv_item(session, inv, 2, "Logitech Wireless Mouse", D("1"), D("2500"),
                  line_total=D("2500"))
    await session.flush()

    report = await reconcile_invoice(session, inv.id)
    extra = find_anomaly(report, "EXTRA_ITEM")
    assert extra is not None
    assert "Mouse" in extra.description
