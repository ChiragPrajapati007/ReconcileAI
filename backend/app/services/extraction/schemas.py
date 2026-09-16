"""
Extraction data schemas.

These are plain Pydantic models used *within* the extraction service.
They are NOT the API response schemas (those live in app/schemas/extraction.py).

Separation:
  RawExtractionResult  — what the AI model returned (string monetary values)
  CanonicalExtraction  — deterministic validated result (Decimal monetary values)
  ValidationIssue      — a problem detected by deterministic validation
  SourceReference      — traceability back to the source document/page
"""
from __future__ import annotations

import enum
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


# ── Raw AI output ────────────────────────────────────────────────────────────

class RawLineItem(BaseModel):
    """A single line item as returned by the AI extraction model."""
    description: str | None = None
    quantity: str | None = None           # String — not yet validated as Decimal
    unit_price: str | None = None
    line_total: str | None = None
    tax_rate_percent: str | None = None
    discount: str | None = None
    page_number: int | None = None
    source_text: str | None = None


class RawExtractionResult(BaseModel):
    """Raw structured output from the AI extraction provider.

    All monetary values are strings. Conversion to Decimal happens
    during deterministic validation (CanonicalExtraction).
    """
    vendor_name: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    currency: str | None = None
    po_number: str | None = None

    subtotal: str | None = None
    tax_amount: str | None = None
    discount_amount: str | None = None
    grand_total: str | None = None

    line_items: list[RawLineItem] = Field(default_factory=list)

    # Optional model-reported confidence (0–1) if the provider supplies one.
    model_confidence: float | None = None

    # Free-text notes from the extraction model (e.g. "handwritten", "blurry")
    extraction_notes: str | None = None


# ── Deterministic validation ─────────────────────────────────────────────────

class IssueSeverity(str, enum.Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationIssue(BaseModel):
    """A problem detected during deterministic post-extraction validation."""
    field: str
    issue_type: str           # e.g. "MISSING_REQUIRED", "ARITHMETIC_MISMATCH"
    message: str
    severity: IssueSeverity


# ── Canonical extraction ─────────────────────────────────────────────────────

class CanonicalLineItem(BaseModel):
    """Validated, Decimal-converted line item."""
    line_number: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal
    tax_rate_percent: Decimal = Decimal("0")
    discount: Decimal = Decimal("0")
    page_number: int | None = None
    source_text: str | None = None


class CanonicalExtraction(BaseModel):
    """Deterministically validated extraction with Decimal monetary values."""
    vendor_name: str
    invoice_number: str
    invoice_date: str | None = None
    due_date: str | None = None
    currency: str = "INR"
    po_number: str | None = None

    subtotal: Decimal = Decimal("0")
    tax_amount: Decimal = Decimal("0")
    discount_amount: Decimal = Decimal("0")
    grand_total: Decimal = Decimal("0")

    line_items: list[CanonicalLineItem] = Field(default_factory=list)


# ── Source traceability ──────────────────────────────────────────────────────

class SourceReference(BaseModel):
    """Traceability record linking an extracted value to the source document."""
    field: str
    page_number: int | None = None
    source_text: str | None = None
    extraction_method: str = "multimodal_ai"


# ── Confidence gate ──────────────────────────────────────────────────────────

class GateStatus(str, enum.Enum):
    AUTO = "auto"               # High confidence → proceed automatically
    REVIEW = "review"           # Medium confidence → human review needed
    BLOCKED = "blocked"         # Low confidence → block automatic reconciliation


# ── Pipeline result ──────────────────────────────────────────────────────────

class ExtractionResult(BaseModel):
    """Complete output of the extraction pipeline."""
    raw: RawExtractionResult
    canonical: CanonicalExtraction | None = None
    confidence: float
    gate_status: GateStatus
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)
    extraction_status: str = "success"     # success | partial | failed
    provider_name: str = ""
    model_name: str = ""
