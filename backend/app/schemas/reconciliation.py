"""
Reconciliation response schemas.

Exposes enough detail for the frontend to render:
  - Overall status + financial summary
  - Anomaly list with severity, type, expected/actual values
  - Evidence references
  - Line-level quantity and price analysis
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class EvidenceOut(BaseModel):
    """Evidence record linking an anomaly to a source field."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_type: str
    field_path: Optional[str]
    source_text: Optional[str]
    expected_value: Optional[str]
    actual_value: Optional[str]
    page_number: Optional[int]


class AnomalyOut(BaseModel):
    """A single detected anomaly with financial impact and evidence."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    severity: str
    description: str
    rule_triggered: Optional[str]
    expected_value: Optional[Decimal]
    actual_value: Optional[Decimal]
    difference_amount: Optional[Decimal]
    difference_percent: Optional[Decimal]
    financial_impact: Optional[Decimal]
    status: str
    evidence: list[EvidenceOut]
    created_at: datetime


class ReconciliationResultOut(BaseModel):
    """Reconciliation result — the output of one engine run."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    audit_id: uuid.UUID
    overall_status: str

    # Financial comparison
    invoice_subtotal: Decimal
    po_subtotal: Optional[Decimal]
    invoice_tax: Decimal
    po_tax: Optional[Decimal]
    invoice_discount: Decimal
    po_discount: Optional[Decimal]
    invoice_total: Decimal
    po_total: Optional[Decimal]
    difference_amount: Optional[Decimal]
    difference_percent: Optional[Decimal]

    anomalies: list[AnomalyOut]
    created_at: datetime


class ReconcileResponse(BaseModel):
    """Top-level response from POST /api/reconciliation/{invoice_id}."""

    audit_id: uuid.UUID
    invoice_id: uuid.UUID
    overall_status: str
    result: ReconciliationResultOut
