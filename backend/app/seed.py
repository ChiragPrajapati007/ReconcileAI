"""
Seed module — creates realistic demo data for all 7 reconciliation scenarios.

Run with:
    python -m app.seed

Idempotent: checks for existing PO numbers before inserting.
Uses Decimal throughout — never float for financial calculations.
"""
import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.enums import (
    AnomalySeverity,
    AnomalyStatus,
    AnomalyType,
    AuditStatus,
    InvoiceStatus,
    POStatus,
    ReconciliationStatus,
    EvidenceSourceType,
)
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem
from app.models.invoice import Invoice, InvoiceItem
from app.models.audit import (
    Audit,
    AuditCorrection,
    Anomaly,
    ReconciliationResult,
    Evidence,
    InvoiceLineLedger,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


async def seed(session: AsyncSession) -> None:
    # -----------------------------------------------------------------------
    # SCENARIO A — Clean match
    # PO-2026-001 / INV-2026-001
    # Everything matches → MATCHED
    # -----------------------------------------------------------------------
    existing = await session.execute(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-2026-001")
    )
    if not existing.scalar_one_or_none():
        po_a = PurchaseOrder(
            po_number="PO-2026-001",
            vendor_name="Sharma Technologies Pvt. Ltd.",
            vendor_tax_id="27AABCS1429B1Z1",
            po_date=dt(2026, 1, 15),
            currency="INR",
            subtotal=Decimal("100000.00"),
            tax_amount=Decimal("18000.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("118000.00"),
            status=POStatus.FULLY_INVOICED,
        )
        session.add(po_a)
        await session.flush()

        poi_a1 = PurchaseOrderItem(
            purchase_order_id=po_a.id,
            line_number=1,
            description="Dell Laptop XPS 15",
            normalized_description="dell laptop xps 15",
            quantity=Decimal("2"),
            unit_price=Decimal("50000.00"),
            tax_rate_percent=Decimal("18.000"),
            discount=Decimal("0.00"),
            line_total=Decimal("100000.00"),
        )
        session.add(poi_a1)
        await session.flush()

        inv_a = Invoice(
            invoice_number="INV-2026-001",
            vendor_name="Sharma Technologies Pvt. Ltd.",
            vendor_tax_id="27AABCS1429B1Z1",
            invoice_date=dt(2026, 1, 20),
            po_number="PO-2026-001",
            purchase_order_id=po_a.id,
            currency="INR",
            subtotal=Decimal("100000.00"),
            tax_amount=Decimal("18000.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("118000.00"),
            status=InvoiceStatus.APPROVED,
        )
        session.add(inv_a)
        await session.flush()

        inv_a_item = InvoiceItem(
            invoice_id=inv_a.id,
            line_number=1,
            description="Dell Laptop XPS 15",
            normalized_description="dell laptop xps 15",
            quantity=Decimal("2"),
            unit_price=Decimal("50000.00"),
            tax_rate_percent=Decimal("18.000"),
            discount=Decimal("0.00"),
            line_total=Decimal("100000.00"),
        )
        session.add(inv_a_item)

        audit_a = Audit(
            invoice_id=inv_a.id,
            purchase_order_id=po_a.id,
            status=AuditStatus.COMPLETED,
            started_at=utcnow(),
            completed_at=utcnow(),
        )
        session.add(audit_a)
        await session.flush()

        result_a = ReconciliationResult(
            audit_id=audit_a.id,
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
        session.add(result_a)

        ledger_a = InvoiceLineLedger(
            purchase_order_item_id=poi_a1.id,
            invoice_id=inv_a.id,
            quantity_invoiced=Decimal("2"),
            unit_price=Decimal("50000.00"),
            invoiced_at=inv_a.invoice_date,
        )
        session.add(ledger_a)

        print("  [A] Clean match: PO-2026-001 / INV-2026-001 — MATCHED")

    # -----------------------------------------------------------------------
    # SCENARIO B — Price mismatch
    # PO: 10 × HP Laptop @ ₹40,000 = ₹4,00,000
    # Invoice: 10 × HP Laptop @ ₹45,000 = ₹4,50,000
    # → PRICE_MISMATCH  Δ₹5,000/unit (+12.5%)
    # -----------------------------------------------------------------------
    existing = await session.execute(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-2026-002")
    )
    if not existing.scalar_one_or_none():
        po_b = PurchaseOrder(
            po_number="PO-2026-002",
            vendor_name="Mehta Computers Pvt. Ltd.",
            vendor_tax_id="29AAJCM5243R1ZH",
            po_date=dt(2026, 2, 1),
            currency="INR",
            subtotal=Decimal("400000.00"),
            tax_amount=Decimal("72000.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("472000.00"),
            status=POStatus.OPEN,
        )
        session.add(po_b)
        await session.flush()

        poi_b1 = PurchaseOrderItem(
            purchase_order_id=po_b.id,
            line_number=1,
            description="HP Laptop 15s",
            normalized_description="hp laptop 15s",
            quantity=Decimal("10"),
            unit_price=Decimal("40000.00"),
            tax_rate_percent=Decimal("18.000"),
            discount=Decimal("0.00"),
            line_total=Decimal("400000.00"),
        )
        session.add(poi_b1)
        await session.flush()

        inv_b = Invoice(
            invoice_number="INV-2026-002",
            vendor_name="Mehta Computers Pvt. Ltd.",
            vendor_tax_id="29AAJCM5243R1ZH",
            invoice_date=dt(2026, 2, 10),
            po_number="PO-2026-002",
            purchase_order_id=po_b.id,
            currency="INR",
            subtotal=Decimal("450000.00"),
            tax_amount=Decimal("81000.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("531000.00"),
            status=InvoiceStatus.UNDER_AUDIT,
        )
        session.add(inv_b)
        await session.flush()

        inv_b_item = InvoiceItem(
            invoice_id=inv_b.id,
            line_number=1,
            description="HP Laptop 15s",
            normalized_description="hp laptop 15s",
            quantity=Decimal("10"),
            unit_price=Decimal("45000.00"),  # ← Price discrepancy
            tax_rate_percent=Decimal("18.000"),
            discount=Decimal("0.00"),
            line_total=Decimal("450000.00"),
        )
        session.add(inv_b_item)

        audit_b = Audit(
            invoice_id=inv_b.id,
            purchase_order_id=po_b.id,
            status=AuditStatus.COMPLETED,
            started_at=utcnow(),
            completed_at=utcnow(),
        )
        session.add(audit_b)
        await session.flush()

        # Difference: ₹5,000/unit × 10 = ₹50,000; 50000/400000 = 12.5%
        result_b = ReconciliationResult(
            audit_id=audit_b.id,
            invoice_subtotal=Decimal("450000.00"),
            po_subtotal=Decimal("400000.00"),
            invoice_tax=Decimal("81000.00"),
            po_tax=Decimal("72000.00"),
            invoice_discount=Decimal("0.00"),
            po_discount=Decimal("0.00"),
            invoice_total=Decimal("531000.00"),
            po_total=Decimal("472000.00"),
            difference_amount=Decimal("59000.00"),
            difference_percent=Decimal("12.5000"),
            overall_status=ReconciliationStatus.ANOMALY,
        )
        session.add(result_b)
        await session.flush()

        anomaly_b = Anomaly(
            reconciliation_result_id=result_b.id,
            type=AnomalyType.PRICE_MISMATCH,
            severity=AnomalySeverity.HIGH,
            description="Unit price on invoice (₹45,000) exceeds PO unit price (₹40,000) by ₹5,000 (+12.5%) for HP Laptop 15s.",
            expected_value=Decimal("40000.00"),
            actual_value=Decimal("45000.00"),
            difference_amount=Decimal("5000.00"),
            difference_percent=Decimal("12.5000"),
            financial_impact=Decimal("50000.00"),
            rule_triggered="UNIT_PRICE_EXCEEDS_PO_PRICE",
            status=AnomalyStatus.OPEN,
        )
        session.add(anomaly_b)
        await session.flush()

        evidence_b = Evidence(
            anomaly_id=anomaly_b.id,
            source_type=EvidenceSourceType.INVOICE_FIELD,
            page_number=1,
            field_path="items[0].unit_price",
            source_text="HP Laptop 15s × 10 @ 45,000",
            expected_value="40000.00",
            actual_value="45000.00",
        )
        session.add(evidence_b)

        print("  [B] Price mismatch: PO-2026-002 / INV-2026-002 — PRICE_MISMATCH")

    # -----------------------------------------------------------------------
    # SCENARIO C — Valid partial invoice
    # PO: 100 units.  Prev invoiced: 40.  Current: 30.  Remaining: 60 → VALID
    # -----------------------------------------------------------------------
    existing = await session.execute(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-2026-003")
    )
    if not existing.scalar_one_or_none():
        po_c = PurchaseOrder(
            po_number="PO-2026-003",
            vendor_name="Gupta Office Supplies",
            vendor_tax_id="07AABCG1234D1ZK",
            po_date=dt(2026, 3, 1),
            currency="INR",
            subtotal=Decimal("500000.00"),
            tax_amount=Decimal("90000.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("590000.00"),
            status=POStatus.PARTIALLY_INVOICED,
        )
        session.add(po_c)
        await session.flush()

        poi_c1 = PurchaseOrderItem(
            purchase_order_id=po_c.id,
            line_number=1,
            description="A4 Copier Paper (Ream)",
            normalized_description="a4 copier paper ream",
            quantity=Decimal("100"),
            unit_price=Decimal("5000.00"),
            tax_rate_percent=Decimal("18.000"),
            discount=Decimal("0.00"),
            line_total=Decimal("500000.00"),
        )
        session.add(poi_c1)
        await session.flush()

        # Previous invoice — 40 units
        inv_c_prev = Invoice(
            invoice_number="INV-2026-003A",
            vendor_name="Gupta Office Supplies",
            vendor_tax_id="07AABCG1234D1ZK",
            invoice_date=dt(2026, 3, 10),
            po_number="PO-2026-003",
            purchase_order_id=po_c.id,
            currency="INR",
            subtotal=Decimal("200000.00"),
            tax_amount=Decimal("36000.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("236000.00"),
            status=InvoiceStatus.APPROVED,
        )
        session.add(inv_c_prev)
        await session.flush()

        session.add(InvoiceItem(
            invoice_id=inv_c_prev.id, line_number=1,
            description="A4 Copier Paper (Ream)", normalized_description="a4 copier paper ream",
            quantity=Decimal("40"), unit_price=Decimal("5000.00"),
            tax_rate_percent=Decimal("18.000"), discount=Decimal("0.00"),
            line_total=Decimal("200000.00"),
        ))
        session.add(InvoiceLineLedger(
            purchase_order_item_id=poi_c1.id, invoice_id=inv_c_prev.id,
            quantity_invoiced=Decimal("40"), unit_price=Decimal("5000.00"),
            invoiced_at=inv_c_prev.invoice_date,
        ))

        # Current invoice — 30 units (valid; remaining = 60)
        inv_c_curr = Invoice(
            invoice_number="INV-2026-003B",
            vendor_name="Gupta Office Supplies",
            vendor_tax_id="07AABCG1234D1ZK",
            invoice_date=dt(2026, 3, 25),
            po_number="PO-2026-003",
            purchase_order_id=po_c.id,
            currency="INR",
            subtotal=Decimal("150000.00"),
            tax_amount=Decimal("27000.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("177000.00"),
            status=InvoiceStatus.APPROVED,
        )
        session.add(inv_c_curr)
        await session.flush()

        session.add(InvoiceItem(
            invoice_id=inv_c_curr.id, line_number=1,
            description="A4 Copier Paper (Ream)", normalized_description="a4 copier paper ream",
            quantity=Decimal("30"), unit_price=Decimal("5000.00"),
            tax_rate_percent=Decimal("18.000"), discount=Decimal("0.00"),
            line_total=Decimal("150000.00"),
        ))
        session.add(InvoiceLineLedger(
            purchase_order_item_id=poi_c1.id, invoice_id=inv_c_curr.id,
            quantity_invoiced=Decimal("30"), unit_price=Decimal("5000.00"),
            invoiced_at=inv_c_curr.invoice_date,
        ))

        audit_c = Audit(
            invoice_id=inv_c_curr.id, purchase_order_id=po_c.id,
            status=AuditStatus.COMPLETED, started_at=utcnow(), completed_at=utcnow(),
        )
        session.add(audit_c)
        await session.flush()

        session.add(ReconciliationResult(
            audit_id=audit_c.id,
            invoice_subtotal=Decimal("150000.00"), po_subtotal=Decimal("150000.00"),
            invoice_tax=Decimal("27000.00"), po_tax=Decimal("27000.00"),
            invoice_discount=Decimal("0.00"), po_discount=Decimal("0.00"),
            invoice_total=Decimal("177000.00"), po_total=Decimal("177000.00"),
            difference_amount=Decimal("0.00"), difference_percent=Decimal("0.0000"),
            overall_status=ReconciliationStatus.MATCHED,
        ))

        print("  [C] Valid partial invoice: PO qty=100, prev=40, curr=30 — VALID/MATCHED")

    # -----------------------------------------------------------------------
    # SCENARIO D — Quantity overbilling
    # PO: 100 units.  Prev: 80.  Current: 30.  Remaining: 20 → Overbilled by 10
    # -----------------------------------------------------------------------
    existing = await session.execute(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-2026-004")
    )
    if not existing.scalar_one_or_none():
        po_d = PurchaseOrder(
            po_number="PO-2026-004",
            vendor_name="Patel Stationery House",
            vendor_tax_id="24AAJCP9876Z1ZM",
            po_date=dt(2026, 4, 1),
            currency="INR",
            subtotal=Decimal("300000.00"),
            tax_amount=Decimal("54000.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("354000.00"),
            status=POStatus.PARTIALLY_INVOICED,
        )
        session.add(po_d)
        await session.flush()

        poi_d1 = PurchaseOrderItem(
            purchase_order_id=po_d.id,
            line_number=1,
            description="Printer Toner Cartridge",
            normalized_description="printer toner cartridge",
            quantity=Decimal("100"),
            unit_price=Decimal("3000.00"),
            tax_rate_percent=Decimal("18.000"),
            discount=Decimal("0.00"),
            line_total=Decimal("300000.00"),
        )
        session.add(poi_d1)
        await session.flush()

        # Previous invoice — 80 units
        inv_d_prev = Invoice(
            invoice_number="INV-2026-004A",
            vendor_name="Patel Stationery House",
            vendor_tax_id="24AAJCP9876Z1ZM",
            invoice_date=dt(2026, 4, 10),
            po_number="PO-2026-004",
            purchase_order_id=po_d.id,
            currency="INR",
            subtotal=Decimal("240000.00"),
            tax_amount=Decimal("43200.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("283200.00"),
            status=InvoiceStatus.APPROVED,
        )
        session.add(inv_d_prev)
        await session.flush()

        session.add(InvoiceItem(
            invoice_id=inv_d_prev.id, line_number=1,
            description="Printer Toner Cartridge", normalized_description="printer toner cartridge",
            quantity=Decimal("80"), unit_price=Decimal("3000.00"),
            tax_rate_percent=Decimal("18.000"), discount=Decimal("0.00"),
            line_total=Decimal("240000.00"),
        ))
        session.add(InvoiceLineLedger(
            purchase_order_item_id=poi_d1.id, invoice_id=inv_d_prev.id,
            quantity_invoiced=Decimal("80"), unit_price=Decimal("3000.00"),
            invoiced_at=inv_d_prev.invoice_date,
        ))

        # Current invoice — 30 units (overbills by 10; remaining was 20)
        inv_d_curr = Invoice(
            invoice_number="INV-2026-004B",
            vendor_name="Patel Stationery House",
            vendor_tax_id="24AAJCP9876Z1ZM",
            invoice_date=dt(2026, 4, 25),
            po_number="PO-2026-004",
            purchase_order_id=po_d.id,
            currency="INR",
            subtotal=Decimal("90000.00"),
            tax_amount=Decimal("16200.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("106200.00"),
            status=InvoiceStatus.UNDER_AUDIT,
        )
        session.add(inv_d_curr)
        await session.flush()

        session.add(InvoiceItem(
            invoice_id=inv_d_curr.id, line_number=1,
            description="Printer Toner Cartridge", normalized_description="printer toner cartridge",
            quantity=Decimal("30"), unit_price=Decimal("3000.00"),
            tax_rate_percent=Decimal("18.000"), discount=Decimal("0.00"),
            line_total=Decimal("90000.00"),
        ))
        session.add(InvoiceLineLedger(
            purchase_order_item_id=poi_d1.id, invoice_id=inv_d_curr.id,
            quantity_invoiced=Decimal("30"), unit_price=Decimal("3000.00"),
            invoiced_at=inv_d_curr.invoice_date,
        ))

        audit_d = Audit(
            invoice_id=inv_d_curr.id, purchase_order_id=po_d.id,
            status=AuditStatus.COMPLETED, started_at=utcnow(), completed_at=utcnow(),
        )
        session.add(audit_d)
        await session.flush()

        # Overbilled 10 units × ₹3,000 = ₹30,000
        result_d = ReconciliationResult(
            audit_id=audit_d.id,
            invoice_subtotal=Decimal("90000.00"),
            po_subtotal=Decimal("60000.00"),  # Only 20 remaining × 3000
            invoice_tax=Decimal("16200.00"),
            po_tax=Decimal("10800.00"),
            invoice_discount=Decimal("0.00"),
            po_discount=Decimal("0.00"),
            invoice_total=Decimal("106200.00"),
            po_total=Decimal("70800.00"),
            difference_amount=Decimal("35400.00"),
            difference_percent=Decimal("50.0000"),
            overall_status=ReconciliationStatus.ANOMALY,
        )
        session.add(result_d)
        await session.flush()

        anomaly_d = Anomaly(
            reconciliation_result_id=result_d.id,
            type=AnomalyType.QUANTITY_OVERBILLING,
            severity=AnomalySeverity.HIGH,
            description=(
                "Invoice claims 30 units of Printer Toner Cartridge but only 20 remain "
                "on PO-2026-004 (PO qty=100, previously invoiced=80). "
                "Overbilled: 10 units × ₹3,000 = ₹30,000."
            ),
            expected_value=Decimal("20"),
            actual_value=Decimal("30"),
            difference_amount=Decimal("10"),
            difference_percent=Decimal("50.0000"),
            financial_impact=Decimal("30000.00"),
            rule_triggered="QUANTITY_EXCEEDS_REMAINING_PO_QUANTITY",
            status=AnomalyStatus.OPEN,
        )
        session.add(anomaly_d)
        print("  [D] Overbilling: PO-2026-004 prev=80, curr=30, remaining=20 → Overbilled 10")

    # -----------------------------------------------------------------------
    # SCENARIO E — Duplicate invoice
    # Two invoices with matching vendor + amount + date → DUPLICATE_INVOICE
    # -----------------------------------------------------------------------
    existing = await session.execute(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-2026-005")
    )
    if not existing.scalar_one_or_none():
        po_e = PurchaseOrder(
            po_number="PO-2026-005",
            vendor_name="Rajan Electronics",
            vendor_tax_id="33AABCR7654T1ZP",
            po_date=dt(2026, 5, 1),
            currency="INR",
            subtotal=Decimal("200000.00"),
            tax_amount=Decimal("36000.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("236000.00"),
            status=POStatus.FULLY_INVOICED,
        )
        session.add(po_e)
        await session.flush()

        poi_e1 = PurchaseOrderItem(
            purchase_order_id=po_e.id, line_number=1,
            description="Samsung Monitor 27\"", normalized_description="samsung monitor 27",
            quantity=Decimal("4"), unit_price=Decimal("50000.00"),
            tax_rate_percent=Decimal("18.000"), discount=Decimal("0.00"),
            line_total=Decimal("200000.00"),
        )
        session.add(poi_e1)
        await session.flush()

        # First (legitimate) invoice
        inv_e1 = Invoice(
            invoice_number="INV-2026-005",
            vendor_name="Rajan Electronics",
            vendor_tax_id="33AABCR7654T1ZP",
            invoice_date=dt(2026, 5, 10),
            po_number="PO-2026-005",
            purchase_order_id=po_e.id,
            currency="INR",
            subtotal=Decimal("200000.00"),
            tax_amount=Decimal("36000.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("236000.00"),
            status=InvoiceStatus.APPROVED,
        )
        session.add(inv_e1)
        await session.flush()

        session.add(InvoiceItem(
            invoice_id=inv_e1.id, line_number=1,
            description="Samsung Monitor 27\"", normalized_description="samsung monitor 27",
            quantity=Decimal("4"), unit_price=Decimal("50000.00"),
            tax_rate_percent=Decimal("18.000"), discount=Decimal("0.00"),
            line_total=Decimal("200000.00"),
        ))
        session.add(InvoiceLineLedger(
            purchase_order_item_id=poi_e1.id, invoice_id=inv_e1.id,
            quantity_invoiced=Decimal("4"), unit_price=Decimal("50000.00"),
            invoiced_at=inv_e1.invoice_date,
        ))

        # Duplicate — same vendor, same amount, same date, slightly different number
        inv_e2 = Invoice(
            invoice_number="INV-2026-005-DUP",
            vendor_name="Rajan Electronics",
            vendor_tax_id="33AABCR7654T1ZP",
            invoice_date=dt(2026, 5, 10),  # Same date
            po_number="PO-2026-005",
            purchase_order_id=po_e.id,
            currency="INR",
            subtotal=Decimal("200000.00"),   # Same amount
            tax_amount=Decimal("36000.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("236000.00"),
            status=InvoiceStatus.UNDER_AUDIT,
        )
        session.add(inv_e2)
        await session.flush()

        audit_e = Audit(
            invoice_id=inv_e2.id, purchase_order_id=po_e.id,
            status=AuditStatus.COMPLETED, started_at=utcnow(), completed_at=utcnow(),
        )
        session.add(audit_e)
        await session.flush()

        result_e = ReconciliationResult(
            audit_id=audit_e.id,
            invoice_subtotal=Decimal("200000.00"), po_subtotal=Decimal("200000.00"),
            invoice_tax=Decimal("36000.00"), po_tax=Decimal("36000.00"),
            invoice_discount=Decimal("0.00"), po_discount=Decimal("0.00"),
            invoice_total=Decimal("236000.00"), po_total=Decimal("236000.00"),
            difference_amount=Decimal("0.00"), difference_percent=Decimal("0.0000"),
            overall_status=ReconciliationStatus.ANOMALY,
        )
        session.add(result_e)
        await session.flush()

        anomaly_e = Anomaly(
            reconciliation_result_id=result_e.id,
            type=AnomalyType.DUPLICATE_INVOICE,
            severity=AnomalySeverity.HIGH,
            description=(
                "INV-2026-005-DUP matches INV-2026-005 on vendor (Rajan Electronics), "
                "invoice date (2026-05-10), and grand total (₹2,36,000). Possible duplicate."
            ),
            expected_value=None,
            actual_value=None,
            financial_impact=Decimal("236000.00"),
            rule_triggered="DUPLICATE_VENDOR_DATE_AMOUNT",
            status=AnomalyStatus.OPEN,
        )
        session.add(anomaly_e)
        print("  [E] Duplicate invoice: INV-2026-005 / INV-2026-005-DUP — DUPLICATE_INVOICE")

    # -----------------------------------------------------------------------
    # SCENARIO F — Missing item
    # PO: Laptop + Mouse + Keyboard
    # Invoice: Laptop + Mouse (Keyboard missing)
    # -----------------------------------------------------------------------
    existing = await session.execute(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-2026-006")
    )
    if not existing.scalar_one_or_none():
        po_f = PurchaseOrder(
            po_number="PO-2026-006",
            vendor_name="Tiwari IT Solutions",
            vendor_tax_id="09AABCT3344K1ZF",
            po_date=dt(2026, 6, 1),
            currency="INR",
            subtotal=Decimal("95000.00"),
            tax_amount=Decimal("17100.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("112100.00"),
            status=POStatus.PARTIALLY_INVOICED,
        )
        session.add(po_f)
        await session.flush()

        for ln, desc, qty, price, total in [
            (1, "Lenovo ThinkPad E14", Decimal("1"), Decimal("65000.00"), Decimal("65000.00")),
            (2, "Logitech Wireless Mouse", Decimal("1"), Decimal("2500.00"), Decimal("2500.00")),
            (3, "Zebronics Mechanical Keyboard", Decimal("1"), Decimal("4500.00"), Decimal("4500.00")),
        ]:
            session.add(PurchaseOrderItem(
                purchase_order_id=po_f.id, line_number=ln,
                description=desc, normalized_description=desc.lower(),
                quantity=qty, unit_price=price,
                tax_rate_percent=Decimal("18.000"), discount=Decimal("0.00"),
                line_total=total,
            ))
        await session.flush()

        # Invoice: only Laptop + Mouse (no Keyboard)
        inv_f = Invoice(
            invoice_number="INV-2026-006",
            vendor_name="Tiwari IT Solutions",
            vendor_tax_id="09AABCT3344K1ZF",
            invoice_date=dt(2026, 6, 10),
            po_number="PO-2026-006",
            purchase_order_id=po_f.id,
            currency="INR",
            subtotal=Decimal("67500.00"),
            tax_amount=Decimal("12150.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("79650.00"),
            status=InvoiceStatus.UNDER_AUDIT,
        )
        session.add(inv_f)
        await session.flush()

        for ln, desc, qty, price, total in [
            (1, "Lenovo ThinkPad E14", Decimal("1"), Decimal("65000.00"), Decimal("65000.00")),
            (2, "Logitech Wireless Mouse", Decimal("1"), Decimal("2500.00"), Decimal("2500.00")),
        ]:
            session.add(InvoiceItem(
                invoice_id=inv_f.id, line_number=ln,
                description=desc, normalized_description=desc.lower(),
                quantity=qty, unit_price=price,
                tax_rate_percent=Decimal("18.000"), discount=Decimal("0.00"),
                line_total=total,
            ))

        audit_f = Audit(
            invoice_id=inv_f.id, purchase_order_id=po_f.id,
            status=AuditStatus.COMPLETED, started_at=utcnow(), completed_at=utcnow(),
        )
        session.add(audit_f)
        await session.flush()

        result_f = ReconciliationResult(
            audit_id=audit_f.id,
            invoice_subtotal=Decimal("67500.00"), po_subtotal=Decimal("95000.00"),
            invoice_tax=Decimal("12150.00"), po_tax=Decimal("17100.00"),
            invoice_discount=Decimal("0.00"), po_discount=Decimal("0.00"),
            invoice_total=Decimal("79650.00"), po_total=Decimal("112100.00"),
            difference_amount=Decimal("-32450.00"), difference_percent=Decimal("-28.9473"),
            overall_status=ReconciliationStatus.ANOMALY,
        )
        session.add(result_f)
        await session.flush()

        session.add(Anomaly(
            reconciliation_result_id=result_f.id,
            type=AnomalyType.MISSING_ITEM,
            severity=AnomalySeverity.MEDIUM,
            description="PO line 3 (Zebronics Mechanical Keyboard, ₹4,500) is not present in the invoice.",
            financial_impact=Decimal("4500.00"),
            rule_triggered="PO_ITEM_NOT_FOUND_IN_INVOICE",
            status=AnomalyStatus.OPEN,
        ))
        print("  [F] Missing item: PO-2026-006 (Keyboard missing from invoice) — MISSING_ITEM")

    # -----------------------------------------------------------------------
    # SCENARIO G — Extra item
    # PO: Laptop only
    # Invoice: Laptop + Mouse
    # -----------------------------------------------------------------------
    existing = await session.execute(
        select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-2026-007")
    )
    if not existing.scalar_one_or_none():
        po_g = PurchaseOrder(
            po_number="PO-2026-007",
            vendor_name="Kapoor InfoTech",
            vendor_tax_id="19AABCK2211H1ZT",
            po_date=dt(2026, 7, 1),
            currency="INR",
            subtotal=Decimal("75000.00"),
            tax_amount=Decimal("13500.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("88500.00"),
            status=POStatus.OPEN,
        )
        session.add(po_g)
        await session.flush()

        session.add(PurchaseOrderItem(
            purchase_order_id=po_g.id, line_number=1,
            description="Acer Aspire Laptop", normalized_description="acer aspire laptop",
            quantity=Decimal("1"), unit_price=Decimal("75000.00"),
            tax_rate_percent=Decimal("18.000"), discount=Decimal("0.00"),
            line_total=Decimal("75000.00"),
        ))
        await session.flush()

        # Invoice includes Mouse which is NOT on the PO
        inv_g = Invoice(
            invoice_number="INV-2026-007",
            vendor_name="Kapoor InfoTech",
            vendor_tax_id="19AABCK2211H1ZT",
            invoice_date=dt(2026, 7, 10),
            po_number="PO-2026-007",
            purchase_order_id=po_g.id,
            currency="INR",
            subtotal=Decimal("77500.00"),
            tax_amount=Decimal("13950.00"),
            discount_amount=Decimal("0.00"),
            grand_total=Decimal("91450.00"),
            status=InvoiceStatus.UNDER_AUDIT,
        )
        session.add(inv_g)
        await session.flush()

        for ln, desc, qty, price, total in [
            (1, "Acer Aspire Laptop", Decimal("1"), Decimal("75000.00"), Decimal("75000.00")),
            (2, "HP Wireless Mouse", Decimal("1"), Decimal("2500.00"), Decimal("2500.00")),  # Extra
        ]:
            session.add(InvoiceItem(
                invoice_id=inv_g.id, line_number=ln,
                description=desc, normalized_description=desc.lower(),
                quantity=qty, unit_price=price,
                tax_rate_percent=Decimal("18.000"), discount=Decimal("0.00"),
                line_total=total,
            ))

        audit_g = Audit(
            invoice_id=inv_g.id, purchase_order_id=po_g.id,
            status=AuditStatus.COMPLETED, started_at=utcnow(), completed_at=utcnow(),
        )
        session.add(audit_g)
        await session.flush()

        result_g = ReconciliationResult(
            audit_id=audit_g.id,
            invoice_subtotal=Decimal("77500.00"), po_subtotal=Decimal("75000.00"),
            invoice_tax=Decimal("13950.00"), po_tax=Decimal("13500.00"),
            invoice_discount=Decimal("0.00"), po_discount=Decimal("0.00"),
            invoice_total=Decimal("91450.00"), po_total=Decimal("88500.00"),
            difference_amount=Decimal("2950.00"), difference_percent=Decimal("3.3333"),
            overall_status=ReconciliationStatus.ANOMALY,
        )
        session.add(result_g)
        await session.flush()

        session.add(Anomaly(
            reconciliation_result_id=result_g.id,
            type=AnomalyType.EXTRA_ITEM,
            severity=AnomalySeverity.MEDIUM,
            description="Invoice line 2 (HP Wireless Mouse, ₹2,500) has no matching line on PO-2026-007.",
            financial_impact=Decimal("2500.00"),
            rule_triggered="INVOICE_ITEM_NOT_FOUND_IN_PO",
            status=AnomalyStatus.OPEN,
        ))
        print("  [G] Extra item: PO-2026-007 (Mouse not on PO) — EXTRA_ITEM")

    await session.commit()
    print("\nSeed complete.")


async def main() -> None:
    print("Starting seed...")
    async with AsyncSessionLocal() as session:
        await seed(session)


if __name__ == "__main__":
    asyncio.run(main())
