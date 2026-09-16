"""
Pydantic API schemas for the extraction endpoints.

These are the request/response models exposed by the API.
They serialize the internal ExtractionResult for HTTP responses.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RawLineItemOut(BaseModel):
    description: str | None = None
    quantity: str | None = None
    unit_price: str | None = None
    line_total: str | None = None
    tax_rate_percent: str | None = None
    discount: str | None = None
    page_number: int | None = None
    source_text: str | None = None


class RawExtractionOut(BaseModel):
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
    line_items: list[RawLineItemOut] = Field(default_factory=list)
    extraction_notes: str | None = None


class CanonicalLineItemOut(BaseModel):
    line_number: int
    description: str
    quantity: str     # Decimal serialized as string
    unit_price: str
    line_total: str
    tax_rate_percent: str = "0"
    discount: str = "0"
    page_number: int | None = None
    source_text: str | None = None

    model_config = {"from_attributes": True}


class CanonicalExtractionOut(BaseModel):
    vendor_name: str
    invoice_number: str
    invoice_date: str | None = None
    due_date: str | None = None
    currency: str = "INR"
    po_number: str | None = None
    subtotal: str
    tax_amount: str
    discount_amount: str
    grand_total: str
    line_items: list[CanonicalLineItemOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ValidationIssueOut(BaseModel):
    field: str
    issue_type: str
    message: str
    severity: str


class SourceReferenceOut(BaseModel):
    field: str
    page_number: int | None = None
    source_text: str | None = None
    extraction_method: str = "multimodal_ai"


class ExtractionUploadResponse(BaseModel):
    """Response from POST /api/extraction/{invoice_id}/upload"""
    document_id: str
    extraction_id: str | None = None
    extraction_status: str
    confidence: float
    gate_status: str
    raw_extraction: RawExtractionOut
    canonical_extraction: CanonicalExtractionOut | None = None
    validation_issues: list[ValidationIssueOut] = Field(default_factory=list)
    source_references: list[SourceReferenceOut] = Field(default_factory=list)
    provider_name: str
    model_name: str


class ExtractionDetailOut(BaseModel):
    """Response from GET /api/extraction/{invoice_id}"""
    extraction_id: str
    document_id: str | None = None
    extraction_status: str
    confidence: float | None = None
    provider_name: str
    model_name: str
    raw_extraction: dict | None = None
    normalized_extraction: dict | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — Integration schemas
# ─────────────────────────────────────────────────────────────────────────────

class ReconcileFromExtractionRequest(BaseModel):
    """Optional request body for POST /api/extraction/{invoice_id}/reconcile."""
    allow_review: bool = Field(
        False,
        description=(
            "Set to true to allow reconciliation of REVIEW-gated extractions. "
            "Caller must have applied human corrections before setting this flag."
        ),
    )


class PopulationResultOut(BaseModel):
    """Describes what the populator wrote before running reconciliation."""
    extraction_id: str
    gate_status: str
    eligible: bool
    block_reason: str | None = None
    po_found: bool = False
    po_id: str | None = None
    line_items_written: int = 0


class ReconcileFromExtractionResponse(BaseModel):
    """Response from POST /api/extraction/{invoice_id}/reconcile."""
    invoice_id: str
    population: PopulationResultOut
    audit_id: str | None = None
    overall_status: str | None = None
    anomaly_count: int = 0


class CorrectionRequest(BaseModel):
    """Request body for POST /api/extraction/{invoice_id}/corrections."""
    audit_id: str = Field(..., description="UUID of the Audit to attach the correction to.")
    field_path: str = Field(..., description='Dot-path to the field, e.g. "grand_total" or "line_items[0].unit_price".')
    corrected_value: str = Field(..., description="The human-supplied corrected value.")
    original_value: str | None = Field(None, description="Original AI-extracted value (for audit trail).")
    reason: str | None = None
    corrected_by: str | None = None


class CorrectionOut(BaseModel):
    """A single AuditCorrection record."""
    id: str
    audit_id: str
    invoice_id: str
    field_path: str
    original_value: str | None = None
    corrected_value: str
    reason: str | None = None
    corrected_by: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
