"""
Audit response schemas.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.reconciliation import ReconciliationResultOut


class AuditOut(BaseModel):
    """Response schema for an Audit record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    purchase_order_id: Optional[uuid.UUID]
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    reconciliation_result: Optional[ReconciliationResultOut]
