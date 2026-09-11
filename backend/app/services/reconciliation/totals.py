"""
Header-level total reconciliation: tax, discount, and grand-total checks.

Strategy (avoids redundant anomalies)
──────────────────────────────────────
1. **Line-derived subtotal** — ``SUM(invoice_items.line_total)`` is calculated
   and compared to the invoice header subtotal.  A mismatch here is flagged as
   ``TOTAL_MISMATCH`` with rule ``INTERNAL_SUBTOTAL_INCONSISTENCY``.

2. **Invoice arithmetic** — ``expected_grand = subtotal + tax − discount``.
   If this doesn't equal ``invoice.grand_total`` → ``TOTAL_MISMATCH`` with
   rule ``INVOICE_ARITHMETIC_ERROR``.

3. **Tax comparison** — ``invoice.tax_amount`` vs ``po.tax_amount`` →
   ``TAX_MISMATCH`` if difference > TAX_TOLERANCE_AMOUNT.

4. **Discount comparison** — ``invoice.discount_amount`` vs
   ``po.discount_amount`` → ``DISCOUNT_MISMATCH`` if difference >
   DISCOUNT_TOLERANCE_AMOUNT.

5. **Grand-total PO comparison** — only created when **no** line-level
   anomalies (PRICE_MISMATCH, QUANTITY_OVERBILLING, MISSING_ITEM, EXTRA_ITEM)
   exist.  If line-level anomalies already explain the total difference, a
   separate ``TOTAL_MISMATCH`` would be redundant.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from app.core.config import Settings
from app.services.reconciliation.rules import classify_severity
from app.services.reconciliation.types import (
    AnomalyRecord,
    EvidenceRecord,
    round_amount,
    round_percent,
)


def check_totals(
    invoice,
    po,
    invoice_items: list,
    settings: Settings,
    has_line_anomalies: bool,
) -> list[AnomalyRecord]:
    """Run all header-level total checks and return anomaly records.

    Parameters
    ----------
    invoice            : Invoice ORM object
    po                 : PurchaseOrder ORM object (may be None)
    invoice_items      : list of InvoiceItem ORM objects
    settings           : application settings with tolerance values
    has_line_anomalies : True if any PRICE_MISMATCH / QUANTITY_OVERBILLING /
                         MISSING_ITEM / EXTRA_ITEM has already been detected.
    """
    anomalies: list[AnomalyRecord] = []

    inv_subtotal = Decimal(str(invoice.subtotal))
    inv_tax = Decimal(str(invoice.tax_amount))
    inv_discount = Decimal(str(invoice.discount_amount))
    inv_grand = Decimal(str(invoice.grand_total))

    # ── 1. Line-derived subtotal consistency ──────────────────────────
    if invoice_items:
        line_derived = sum(
            Decimal(str(item.line_total)) for item in invoice_items
        )
        line_diff = abs(round_amount(line_derived - inv_subtotal))

        if line_diff > settings.TOTAL_TOLERANCE_AMOUNT:
            impact = round_amount(line_derived - inv_subtotal)
            anomalies.append(AnomalyRecord(
                type="TOTAL_MISMATCH",
                severity=classify_severity("TOTAL_MISMATCH", impact, settings),
                description=(
                    f"Invoice header subtotal ({inv_subtotal}) does not match "
                    f"sum of line items ({round_amount(line_derived)}). "
                    f"Difference: {impact}"
                ),
                rule_triggered="INTERNAL_SUBTOTAL_INCONSISTENCY",
                expected_value=round_amount(line_derived),
                actual_value=inv_subtotal,
                difference_amount=impact,
                financial_impact=impact,
                evidence=[EvidenceRecord(
                    source_type="calculation",
                    field_path="invoice.subtotal",
                    expected_value=str(round_amount(line_derived)),
                    actual_value=str(inv_subtotal),
                )],
            ))

    # ── 2. Invoice arithmetic consistency ─────────────────────────────
    expected_grand = round_amount(inv_subtotal + inv_tax - inv_discount)
    arith_diff = abs(round_amount(expected_grand - inv_grand))

    if arith_diff > settings.TOTAL_TOLERANCE_AMOUNT:
        impact = round_amount(expected_grand - inv_grand)
        anomalies.append(AnomalyRecord(
            type="TOTAL_MISMATCH",
            severity=classify_severity("TOTAL_MISMATCH", impact, settings),
            description=(
                f"Invoice grand total ({inv_grand}) does not equal "
                f"subtotal + tax − discount ({expected_grand}). "
                f"Arithmetic error: {impact}"
            ),
            rule_triggered="INVOICE_ARITHMETIC_ERROR",
            expected_value=expected_grand,
            actual_value=inv_grand,
            difference_amount=impact,
            financial_impact=impact,
            evidence=[EvidenceRecord(
                source_type="calculation",
                field_path="invoice.grand_total",
                expected_value=str(expected_grand),
                actual_value=str(inv_grand),
            )],
        ))

    if po is None:
        return anomalies

    po_tax = Decimal(str(po.tax_amount))
    po_discount = Decimal(str(po.discount_amount))
    po_grand = Decimal(str(po.grand_total))

    # ── 3. Tax comparison ─────────────────────────────────────────────
    tax_diff_raw = round_amount(inv_tax - po_tax)
    if abs(tax_diff_raw) > settings.TAX_TOLERANCE_AMOUNT:
        pct = _safe_percent(tax_diff_raw, po_tax)
        anomalies.append(AnomalyRecord(
            type="TAX_MISMATCH",
            severity=classify_severity("TAX_MISMATCH", tax_diff_raw, settings),
            description=(
                f"Tax mismatch: invoice tax {inv_tax} vs PO tax {po_tax}. "
                f"Difference: {tax_diff_raw}"
            ),
            rule_triggered="TAX_PO_COMPARISON",
            expected_value=po_tax,
            actual_value=inv_tax,
            difference_amount=tax_diff_raw,
            difference_percent=pct,
            financial_impact=tax_diff_raw,
            evidence=[
                EvidenceRecord(
                    source_type="po_field",
                    field_path="purchase_order.tax_amount",
                    expected_value=str(po_tax),
                    actual_value=str(inv_tax),
                ),
            ],
        ))

    # ── 4. Discount comparison ────────────────────────────────────────
    disc_diff_raw = round_amount(inv_discount - po_discount)
    if abs(disc_diff_raw) > settings.DISCOUNT_TOLERANCE_AMOUNT:
        pct = _safe_percent(disc_diff_raw, po_discount)
        anomalies.append(AnomalyRecord(
            type="DISCOUNT_MISMATCH",
            severity=classify_severity("DISCOUNT_MISMATCH", disc_diff_raw, settings),
            description=(
                f"Discount mismatch: invoice discount {inv_discount} vs "
                f"PO discount {po_discount}. Difference: {disc_diff_raw}"
            ),
            rule_triggered="DISCOUNT_PO_COMPARISON",
            expected_value=po_discount,
            actual_value=inv_discount,
            difference_amount=disc_diff_raw,
            difference_percent=pct,
            financial_impact=disc_diff_raw,
            evidence=[
                EvidenceRecord(
                    source_type="po_field",
                    field_path="purchase_order.discount_amount",
                    expected_value=str(po_discount),
                    actual_value=str(inv_discount),
                ),
            ],
        ))

    # ── 5. Grand-total PO comparison (only when no line-level anomalies) ─
    if not has_line_anomalies:
        gt_diff_raw = round_amount(inv_grand - po_grand)
        if abs(gt_diff_raw) > settings.TOTAL_TOLERANCE_AMOUNT:
            pct = _safe_percent(gt_diff_raw, po_grand)
            anomalies.append(AnomalyRecord(
                type="TOTAL_MISMATCH",
                severity=classify_severity("TOTAL_MISMATCH", gt_diff_raw, settings),
                description=(
                    f"Grand total mismatch: invoice {inv_grand} vs PO {po_grand}. "
                    f"Difference: {gt_diff_raw}"
                ),
                rule_triggered="GRAND_TOTAL_PO_COMPARISON",
                expected_value=po_grand,
                actual_value=inv_grand,
                difference_amount=gt_diff_raw,
                difference_percent=pct,
                financial_impact=gt_diff_raw,
                evidence=[
                    EvidenceRecord(
                        source_type="po_field",
                        field_path="purchase_order.grand_total",
                        expected_value=str(po_grand),
                        actual_value=str(inv_grand),
                    ),
                ],
            ))

    return anomalies


def _safe_percent(diff: Decimal, base: Decimal) -> Optional[Decimal]:
    """Compute percentage; returns None when base is zero."""
    if base == Decimal("0"):
        return None
    return round_percent((diff / base) * Decimal("100"))
