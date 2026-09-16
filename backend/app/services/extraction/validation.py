"""
Deterministic post-extraction validation.

After the AI extraction, this module validates the result using
pure arithmetic and schema checks. No LLM calls.

Checks:
  - Required fields present
  - Numeric validity (parseable as Decimal, non-negative)
  - Line arithmetic: qty × unit_price ≈ line_total
  - Subtotal consistency: sum(line_totals) ≈ subtotal
  - Grand total arithmetic: subtotal + tax − discount ≈ grand_total
  - Contradiction detection
  - Date validity
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.services.extraction.schemas import (
    IssueSeverity,
    RawExtractionResult,
    ValidationIssue,
)

# Tolerance for arithmetic comparisons (in monetary units)
_TOLERANCE = Decimal("1.00")


def validate_extraction(raw: RawExtractionResult) -> list[ValidationIssue]:
    """Run all deterministic validation checks on a raw extraction.

    Returns a list of ValidationIssue objects (may be empty if all checks pass).
    """
    issues: list[ValidationIssue] = []

    issues.extend(_check_required_fields(raw))
    issues.extend(_check_numeric_validity(raw))
    issues.extend(_check_line_arithmetic(raw))
    issues.extend(_check_subtotal_consistency(raw))
    issues.extend(_check_grand_total(raw))
    issues.extend(_check_contradictions(raw))
    issues.extend(_check_dates(raw))

    return issues


# ── Required fields ──────────────────────────────────────────────────────────

def _check_required_fields(raw: RawExtractionResult) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    required = {
        "vendor_name": raw.vendor_name,
        "invoice_number": raw.invoice_number,
        "grand_total": raw.grand_total,
    }
    for fname, fval in required.items():
        if not fval or not str(fval).strip():
            issues.append(ValidationIssue(
                field=fname,
                issue_type="MISSING_REQUIRED",
                message=f"Required field '{fname}' is missing or empty.",
                severity=IssueSeverity.ERROR,
            ))
    return issues


# ── Numeric validity ─────────────────────────────────────────────────────────

def _parse_decimal(value: str | None) -> Decimal | None:
    if not value or not str(value).strip():
        return None
    try:
        cleaned = str(value).strip().replace(",", "").replace("₹", "").replace("$", "")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _check_numeric_validity(raw: RawExtractionResult) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    monetary_fields = {
        "subtotal": raw.subtotal,
        "tax_amount": raw.tax_amount,
        "discount_amount": raw.discount_amount,
        "grand_total": raw.grand_total,
    }
    for fname, fval in monetary_fields.items():
        if fval is None:
            continue
        parsed = _parse_decimal(fval)
        if parsed is None:
            issues.append(ValidationIssue(
                field=fname,
                issue_type="UNPARSEABLE_NUMBER",
                message=f"Cannot parse '{fval}' as a number for field '{fname}'.",
                severity=IssueSeverity.ERROR,
            ))
        elif parsed < 0:
            issues.append(ValidationIssue(
                field=fname,
                issue_type="NEGATIVE_VALUE",
                message=f"Negative value {parsed} for field '{fname}'.",
                severity=IssueSeverity.ERROR,
            ))

    # Check line item numeric fields
    for i, item in enumerate(raw.line_items):
        qty = _parse_decimal(item.quantity)
        if item.quantity is not None and qty is None:
            issues.append(ValidationIssue(
                field=f"line_items[{i}].quantity",
                issue_type="UNPARSEABLE_NUMBER",
                message=f"Cannot parse quantity '{item.quantity}' for line {i}.",
                severity=IssueSeverity.ERROR,
            ))
        elif qty is not None and qty <= 0:
            issues.append(ValidationIssue(
                field=f"line_items[{i}].quantity",
                issue_type="NEGATIVE_VALUE",
                message=f"Non-positive quantity {qty} for line {i}.",
                severity=IssueSeverity.ERROR,
            ))

        price = _parse_decimal(item.unit_price)
        if item.unit_price is not None and price is None:
            issues.append(ValidationIssue(
                field=f"line_items[{i}].unit_price",
                issue_type="UNPARSEABLE_NUMBER",
                message=f"Cannot parse unit_price '{item.unit_price}' for line {i}.",
                severity=IssueSeverity.ERROR,
            ))

    return issues


# ── Line arithmetic ──────────────────────────────────────────────────────────

def _check_line_arithmetic(raw: RawExtractionResult) -> list[ValidationIssue]:
    """Check: quantity × unit_price ≈ line_total for each line."""
    issues: list[ValidationIssue] = []

    for i, item in enumerate(raw.line_items):
        qty = _parse_decimal(item.quantity)
        price = _parse_decimal(item.unit_price)
        total = _parse_decimal(item.line_total)

        if qty is None or price is None or total is None:
            continue  # Already flagged in numeric checks

        expected = qty * price
        if abs(expected - total) > _TOLERANCE:
            issues.append(ValidationIssue(
                field=f"line_items[{i}].line_total",
                issue_type="LINE_ARITHMETIC_MISMATCH",
                message=(
                    f"Line {i}: qty({qty}) × price({price}) = {expected}, "
                    f"but line_total = {total}. Diff = {abs(expected - total)}"
                ),
                severity=IssueSeverity.WARNING,
            ))

    return issues


# ── Subtotal consistency ─────────────────────────────────────────────────────

def _check_subtotal_consistency(raw: RawExtractionResult) -> list[ValidationIssue]:
    """Check: sum(line_totals) ≈ subtotal."""
    issues: list[ValidationIssue] = []
    subtotal = _parse_decimal(raw.subtotal)
    if subtotal is None or len(raw.line_items) == 0:
        return issues

    line_sum = Decimal("0")
    all_parseable = True
    for item in raw.line_items:
        lt = _parse_decimal(item.line_total)
        if lt is None:
            all_parseable = False
            break
        line_sum += lt

    if all_parseable and abs(line_sum - subtotal) > _TOLERANCE:
        issues.append(ValidationIssue(
            field="subtotal",
            issue_type="SUBTOTAL_MISMATCH",
            message=(
                f"Sum of line totals ({line_sum}) does not match "
                f"subtotal ({subtotal}). Diff = {abs(line_sum - subtotal)}"
            ),
            severity=IssueSeverity.WARNING,
        ))

    return issues


# ── Grand total arithmetic ───────────────────────────────────────────────────

def _check_grand_total(raw: RawExtractionResult) -> list[ValidationIssue]:
    """Check: subtotal + tax − discount ≈ grand_total."""
    issues: list[ValidationIssue] = []
    subtotal = _parse_decimal(raw.subtotal)
    tax = _parse_decimal(raw.tax_amount) or Decimal("0")
    discount = _parse_decimal(raw.discount_amount) or Decimal("0")
    grand_total = _parse_decimal(raw.grand_total)

    if subtotal is None or grand_total is None:
        return issues

    expected = subtotal + tax - discount
    if abs(expected - grand_total) > _TOLERANCE:
        issues.append(ValidationIssue(
            field="grand_total",
            issue_type="GRAND_TOTAL_ARITHMETIC",
            message=(
                f"Expected grand_total = subtotal({subtotal}) + tax({tax}) "
                f"- discount({discount}) = {expected}, but got {grand_total}. "
                f"Diff = {abs(expected - grand_total)}"
            ),
            severity=IssueSeverity.WARNING,
        ))

    return issues


# ── Contradiction detection ──────────────────────────────────────────────────

def _check_contradictions(raw: RawExtractionResult) -> list[ValidationIssue]:
    """Detect internally contradictory values."""
    issues: list[ValidationIssue] = []
    subtotal = _parse_decimal(raw.subtotal)
    discount = _parse_decimal(raw.discount_amount) or Decimal("0")
    grand_total = _parse_decimal(raw.grand_total)

    if subtotal is not None and grand_total is not None:
        if grand_total < subtotal and discount == Decimal("0"):
            issues.append(ValidationIssue(
                field="grand_total",
                issue_type="CONTRADICTION",
                message=(
                    f"Grand total ({grand_total}) is less than subtotal ({subtotal}) "
                    f"without any discount applied."
                ),
                severity=IssueSeverity.ERROR,
            ))

    return issues


# ── Date checks ──────────────────────────────────────────────────────────────

def _check_dates(raw: RawExtractionResult) -> list[ValidationIssue]:
    """Check that dates are parseable."""
    issues: list[ValidationIssue] = []
    if raw.invoice_date:
        if not _is_valid_date(raw.invoice_date):
            issues.append(ValidationIssue(
                field="invoice_date",
                issue_type="UNPARSEABLE_DATE",
                message=f"Cannot parse invoice_date '{raw.invoice_date}' as a date.",
                severity=IssueSeverity.WARNING,
            ))
    if raw.due_date:
        if not _is_valid_date(raw.due_date):
            issues.append(ValidationIssue(
                field="due_date",
                issue_type="UNPARSEABLE_DATE",
                message=f"Cannot parse due_date '{raw.due_date}' as a date.",
                severity=IssueSeverity.WARNING,
            ))
    return issues


def _is_valid_date(value: str) -> bool:
    """Try common date formats."""
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            datetime.strptime(value.strip(), fmt)
            return True
        except ValueError:
            continue
    return False
