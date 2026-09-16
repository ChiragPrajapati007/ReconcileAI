from __future__ import annotations

import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict

from app.models.enums import AnomalyStatus, AnomalyType, AnomalySeverity


class EvidenceOut(BaseModel):
    id: uuid.UUID
    anomaly_id: uuid.UUID
    document_id: uuid.UUID | None = None
    source_type: str
    page_number: int | None = None
    field_path: str | None = None
    source_text: str | None = None
    expected_value: str | None = None
    actual_value: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AnomalyStatusUpdateRequest(BaseModel):
    status: AnomalyStatus


class AnomalyOut(BaseModel):
    id: uuid.UUID
    reconciliation_result_id: uuid.UUID
    type: AnomalyType
    severity: AnomalySeverity
    description: str
    expected_value: float | None = None
    actual_value: float | None = None
    difference_amount: float | None = None
    difference_percent: float | None = None
    financial_impact: float | None = None
    rule_triggered: str | None = None
    status: AnomalyStatus
    created_at: datetime
    evidence: list[EvidenceOut] = []

    model_config = ConfigDict(from_attributes=True)

