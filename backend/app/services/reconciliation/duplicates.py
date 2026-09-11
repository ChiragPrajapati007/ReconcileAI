"""
Deterministic duplicate-invoice detection.

Duplicate rules (documented)
────────────────────────────
**Vendor identity is ALWAYS required.**  Two different vendors using the same
invoice number are NEVER duplicates.

The current invoice is ALWAYS excluded from the candidate query.

Rule A — **identical document hash** (STRONG)
    Same ``vendor_name`` + same non-null ``document_hash`` on any linked
    InvoiceDocument.  This is the strongest signal because it means the
    exact same file was uploaded twice.
    → severity: HIGH

Rule B — **vendor + invoice number** (STRONG)
    Same ``vendor_name`` + same ``invoice_number``.
    → severity: HIGH
    Note: the DB has a unique constraint ``(vendor_name, invoice_number)``,
    so this can only fire if the duplicate was ingested with a slightly
    different vendor name that normalizes to the same entity.

Rule C — **vendor + date + total** (MODERATE)
    Same ``vendor_name`` + same ``invoice_date`` + same ``grand_total``.
    → severity: MEDIUM

Rule D — **vendor + PO + date/total** (MODERATE)
    Same ``vendor_name`` + same ``po_number`` +
    (same ``invoice_date`` OR same ``grand_total``).
    → severity: MEDIUM
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, InvoiceDocument
from app.services.reconciliation.rules import classify_severity, normalize_vendor
from app.services.reconciliation.types import AnomalyRecord, EvidenceRecord


async def detect_duplicates(
    session: AsyncSession,
    invoice: Invoice,
    settings,
) -> list[AnomalyRecord]:
    """Check whether `invoice` is a duplicate of any existing invoice.

    The current invoice is always excluded from the search.
    Vendor identity match is always required.

    Returns a list of AnomalyRecords (0 or more).
    """
    anomalies: list[AnomalyRecord] = []
    seen_dup_ids: set = set()   # avoid double-reporting the same duplicate

    inv_vendor_norm = normalize_vendor(invoice.vendor_name)

    # ── Rule A: document-hash duplicates ──────────────────────────────
    # Collect hashes from the current invoice's documents
    current_hashes: list[str] = []
    if hasattr(invoice, "documents") and invoice.documents:
        current_hashes = [
            d.document_hash for d in invoice.documents
            if d.document_hash
        ]

    if current_hashes:
        stmt = (
            select(Invoice)
            .join(InvoiceDocument, InvoiceDocument.invoice_id == Invoice.id)
            .where(
                Invoice.id != invoice.id,
                InvoiceDocument.document_hash.in_(current_hashes),
            )
        )
        result = await session.execute(stmt)
        for dup in result.scalars().unique().all():
            dup_vendor_norm = normalize_vendor(dup.vendor_name)
            if dup_vendor_norm == inv_vendor_norm and dup.id not in seen_dup_ids:
                seen_dup_ids.add(dup.id)
                anomalies.append(_make_dup_anomaly(
                    invoice, dup, "DOCUMENT_HASH_MATCH",
                    "Identical document hash detected (same file uploaded twice)",
                    "HIGH", settings,
                ))

    # ── Rule B: vendor + invoice_number ───────────────────────────────
    stmt_b = select(Invoice).where(
        Invoice.id != invoice.id,
        Invoice.invoice_number == invoice.invoice_number,
    )
    result_b = await session.execute(stmt_b)
    for dup in result_b.scalars().all():
        dup_vendor_norm = normalize_vendor(dup.vendor_name)
        if dup_vendor_norm == inv_vendor_norm and dup.id not in seen_dup_ids:
            seen_dup_ids.add(dup.id)
            anomalies.append(_make_dup_anomaly(
                invoice, dup, "VENDOR_INVOICE_NUMBER_MATCH",
                f"Same vendor and invoice number: {invoice.invoice_number}",
                "HIGH", settings,
            ))

    # ── Rule C: vendor + date + total ─────────────────────────────────
    stmt_c = select(Invoice).where(
        Invoice.id != invoice.id,
        Invoice.invoice_date == invoice.invoice_date,
        Invoice.grand_total == invoice.grand_total,
    )
    result_c = await session.execute(stmt_c)
    for dup in result_c.scalars().all():
        dup_vendor_norm = normalize_vendor(dup.vendor_name)
        if dup_vendor_norm == inv_vendor_norm and dup.id not in seen_dup_ids:
            seen_dup_ids.add(dup.id)
            anomalies.append(_make_dup_anomaly(
                invoice, dup, "VENDOR_DATE_TOTAL_MATCH",
                (f"Same vendor, date ({invoice.invoice_date.date()}), and "
                 f"total ({invoice.grand_total})"),
                "MEDIUM", settings,
            ))

    # ── Rule D: vendor + PO + (date OR total) ─────────────────────────
    if invoice.po_number:
        stmt_d = select(Invoice).where(
            Invoice.id != invoice.id,
            Invoice.po_number == invoice.po_number,
        )
        result_d = await session.execute(stmt_d)
        for dup in result_d.scalars().all():
            dup_vendor_norm = normalize_vendor(dup.vendor_name)
            if dup_vendor_norm != inv_vendor_norm:
                continue
            if dup.id in seen_dup_ids:
                continue
            date_match = dup.invoice_date == invoice.invoice_date
            total_match = Decimal(str(dup.grand_total)) == Decimal(str(invoice.grand_total))
            if date_match or total_match:
                seen_dup_ids.add(dup.id)
                signal = []
                if date_match:
                    signal.append(f"date ({invoice.invoice_date.date()})")
                if total_match:
                    signal.append(f"total ({invoice.grand_total})")
                anomalies.append(_make_dup_anomaly(
                    invoice, dup, "VENDOR_PO_DATE_TOTAL_MATCH",
                    f"Same vendor, PO ({invoice.po_number}), and {' + '.join(signal)}",
                    "MEDIUM", settings,
                ))

    return anomalies


def _make_dup_anomaly(
    invoice: Invoice,
    duplicate: Invoice,
    rule: str,
    description: str,
    severity_override: str,
    settings,
) -> AnomalyRecord:
    """Build a DUPLICATE_INVOICE anomaly record."""
    return AnomalyRecord(
        type="DUPLICATE_INVOICE",
        severity=severity_override,
        description=description,
        rule_triggered=rule,
        financial_impact=Decimal(str(invoice.grand_total)),
        evidence=[
            EvidenceRecord(
                source_type="invoice_field",
                field_path="invoice.invoice_number",
                expected_value="unique invoice",
                actual_value=(
                    f"duplicate of {duplicate.invoice_number} "
                    f"(vendor={duplicate.vendor_name})"
                ),
            ),
        ],
    )
