"""
Pure data-transfer objects for the reconciliation engine.

Design decisions
────────────────
• All monetary values are ``Decimal`` — **never** ``float``.
• Rounding uses ``ROUND_HALF_UP`` (banker's rounding is inappropriate for
  financial auditing where we want ₹0.005 → ₹0.01, not ₹0.00).
• Two precision levels:
      amounts  →  2 decimal places  (``AMOUNT_SCALE``)
      percents →  4 decimal places  (``PERCENT_SCALE``)

Sign convention
───────────────
``positive`` = overcharge  (invoice > PO)
``negative`` = undercharge (invoice < PO)

This convention is applied consistently to ``difference_amount``,
``difference_percent``, and ``financial_impact`` on every anomaly record.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

# ── Rounding constants ────────────────────────────────────────────────────
AMOUNT_SCALE = Decimal("0.01")
PERCENT_SCALE = Decimal("0.0001")
ROUNDING = ROUND_HALF_UP


def round_amount(value: Decimal) -> Decimal:
    """Round a monetary amount to 2 decimal places (ROUND_HALF_UP)."""
    return value.quantize(AMOUNT_SCALE, rounding=ROUNDING)


def round_percent(value: Decimal) -> Decimal:
    """Round a percentage to 4 decimal places (ROUND_HALF_UP)."""
    return value.quantize(PERCENT_SCALE, rounding=ROUNDING)


# ── Line-matching results ─────────────────────────────────────────────────
@dataclass(frozen=True)
class LineMatch:
    """One invoice line successfully matched to one PO line."""
    invoice_item_id: uuid.UUID
    po_item_id: uuid.UUID
    invoice_description: str
    po_description: str
    match_level: int          # 1 = exact normalized, 2 = relaxed
    confidence: str           # "exact" | "relaxed"


@dataclass(frozen=True)
class UnmatchedPOItem:
    """A PO line that has no corresponding invoice line."""
    po_item_id: uuid.UUID
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class UnmatchedInvoiceItem:
    """An invoice line that has no corresponding PO line."""
    invoice_item_id: uuid.UUID
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


@dataclass
class LineMatchResult:
    """Aggregate result of the line-matching phase."""
    matches: list[LineMatch] = field(default_factory=list)
    unmatched_po_items: list[UnmatchedPOItem] = field(default_factory=list)
    unmatched_invoice_items: list[UnmatchedInvoiceItem] = field(default_factory=list)


# ── Quantity analysis ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class QuantityAnalysis:
    """Quantity reconciliation for one matched PO line."""
    po_item_id: uuid.UUID
    invoice_item_id: uuid.UUID
    po_quantity: Decimal
    previously_invoiced: Decimal
    current_invoice_quantity: Decimal
    remaining_before: Decimal      # max(0, po_qty - previously_invoiced)
    remaining_after: Decimal       # remaining_before - current_qty (may be < 0)
    overbilled: Decimal            # max(0, current_qty - remaining_before)
    is_overbilled: bool
    is_already_over_invoiced: bool  # True when previously_invoiced > po_qty


# ── Price comparison ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class PriceComparison:
    """Price reconciliation for one matched line pair."""
    po_item_id: uuid.UUID
    invoice_item_id: uuid.UUID
    po_unit_price: Decimal
    invoice_unit_price: Decimal
    difference_per_unit: Decimal       # invoice - PO (positive = overcharge)
    difference_percent: Decimal        # ((inv - po) / po) * 100
    financial_impact: Decimal          # difference_per_unit × invoice qty
    is_mismatch: bool


# ── Evidence ──────────────────────────────────────────────────────────────
@dataclass
class EvidenceRecord:
    """Traceability breadcrumb linking an anomaly to source fields."""
    source_type: str                   # EvidenceSourceType value
    field_path: Optional[str] = None
    source_text: Optional[str] = None
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    page_number: Optional[int] = None


# ── Anomaly ───────────────────────────────────────────────────────────────
@dataclass
class AnomalyRecord:
    """A single detected discrepancy, ready for persistence."""
    type: str                          # AnomalyType value
    severity: str                      # AnomalySeverity value
    description: str
    rule_triggered: str
    expected_value: Optional[Decimal] = None
    actual_value: Optional[Decimal] = None
    difference_amount: Optional[Decimal] = None
    difference_percent: Optional[Decimal] = None
    financial_impact: Optional[Decimal] = None
    evidence: list[EvidenceRecord] = field(default_factory=list)


# ── Report ────────────────────────────────────────────────────────────────
@dataclass
class ReconciliationReport:
    """Complete output of one reconciliation run."""
    overall_status: str                # ReconciliationStatus value
    anomalies: list[AnomalyRecord] = field(default_factory=list)

    # Summary totals (persisted to ReconciliationResult)
    invoice_subtotal: Decimal = Decimal("0")
    po_subtotal: Optional[Decimal] = None
    invoice_tax: Decimal = Decimal("0")
    po_tax: Optional[Decimal] = None
    invoice_discount: Decimal = Decimal("0")
    po_discount: Optional[Decimal] = None
    invoice_total: Decimal = Decimal("0")
    po_total: Optional[Decimal] = None
    difference_amount: Optional[Decimal] = None
    difference_percent: Optional[Decimal] = None

    # Carried through for persistence
    line_matches: list[LineMatch] = field(default_factory=list)
    audit_id: Optional[uuid.UUID] = None
