"""
Deterministic confidence calculator.

Computes a confidence score for an extraction result based on
field completeness, arithmetic consistency, and contradiction detection.

Every bonus and penalty is documented and explainable.
The result is deterministic: same input always produces the same score.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.services.extraction.schemas import (
    RawExtractionResult,
    ValidationIssue,
)


def calculate_confidence(
    raw: RawExtractionResult,
    validation_issues: list[ValidationIssue],
) -> float:
    """Calculate extraction confidence from raw result and validation findings.

    Methodology:
      base = 1.0
      - Apply penalties for missing/problematic fields
      - Apply bonuses for completeness and consistency
      - Clamp to [0.0, 1.0]

    Returns:
        float in [0.0, 1.0]
    """
    score = 1.0

    # ── Missing field penalties ───────────────────────────────────────────

    # Critical fields — large penalty
    critical_fields = {
        "vendor_name": raw.vendor_name,
        "invoice_number": raw.invoice_number,
        "grand_total": raw.grand_total,
    }
    all_critical_present = True
    for fname, fval in critical_fields.items():
        if not fval or not str(fval).strip():
            score -= 0.15
            all_critical_present = False

    # Important fields — moderate penalty
    important_fields = {
        "invoice_date": raw.invoice_date,
        "subtotal": raw.subtotal,
    }
    for fname, fval in important_fields.items():
        if not fval or not str(fval).strip():
            score -= 0.08

    # Optional fields — small penalty
    optional_fields = {
        "due_date": raw.due_date,
        "currency": raw.currency,
        "po_number": raw.po_number,
        "discount_amount": raw.discount_amount,
    }
    for fname, fval in optional_fields.items():
        if not fval or not str(fval).strip():
            score -= 0.02

    # ── Line item penalties ───────────────────────────────────────────────

    if len(raw.line_items) == 0:
        score -= 0.20
    else:
        inconsistent_lines = 0
        for item in raw.line_items:
            if not _line_arithmetic_ok(item.quantity, item.unit_price, item.line_total):
                inconsistent_lines += 1
        score -= 0.05 * inconsistent_lines

    # ── Validation issue penalties ────────────────────────────────────────

    error_count = sum(1 for vi in validation_issues if vi.severity.value == "error")
    warning_count = sum(1 for vi in validation_issues if vi.severity.value == "warning")

    # Specific issue type penalties (on top of general)
    for vi in validation_issues:
        if vi.issue_type == "SUBTOTAL_MISMATCH":
            score -= 0.10
        elif vi.issue_type == "GRAND_TOTAL_ARITHMETIC":
            score -= 0.12
        elif vi.issue_type == "NEGATIVE_VALUE":
            score -= 0.10
        elif vi.issue_type == "UNPARSEABLE_DATE":
            score -= 0.05
        elif vi.issue_type == "MISSING_REQUIRED":
            pass  # Already penalized above

    # ── Bonuses ───────────────────────────────────────────────────────────

    # All line arithmetic consistent
    if len(raw.line_items) > 0 and all(
        _line_arithmetic_ok(it.quantity, it.unit_price, it.line_total)
        for it in raw.line_items
    ):
        score += 0.05

    # Grand total arithmetic consistent
    if _grand_total_consistent(
        raw.subtotal, raw.tax_amount, raw.discount_amount, raw.grand_total
    ):
        score += 0.05

    # All critical fields present
    if all_critical_present:
        score += 0.05

    # ── Clamp ─────────────────────────────────────────────────────────────

    return max(0.0, min(1.0, round(score, 4)))


def _parse_decimal(value: str | None) -> Decimal | None:
    """Try to parse a string as Decimal. Return None on failure."""
    if not value or not str(value).strip():
        return None
    try:
        # Remove commas and currency symbols
        cleaned = str(value).strip().replace(",", "").replace("₹", "").replace("$", "")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _line_arithmetic_ok(
    quantity: str | None,
    unit_price: str | None,
    line_total: str | None,
) -> bool:
    """Check if quantity × unit_price ≈ line_total within tolerance."""
    qty = _parse_decimal(quantity)
    price = _parse_decimal(unit_price)
    total = _parse_decimal(line_total)

    if qty is None or price is None or total is None:
        return False  # Can't verify → not OK

    expected = qty * price
    return abs(expected - total) <= Decimal("1.00")


def _grand_total_consistent(
    subtotal: str | None,
    tax: str | None,
    discount: str | None,
    grand_total: str | None,
) -> bool:
    """Check if subtotal + tax - discount ≈ grand_total."""
    sub = _parse_decimal(subtotal)
    tx = _parse_decimal(tax) or Decimal("0")
    disc = _parse_decimal(discount) or Decimal("0")
    gt = _parse_decimal(grand_total)

    if sub is None or gt is None:
        return False

    expected = sub + tx - disc
    return abs(expected - gt) <= Decimal("1.00")
