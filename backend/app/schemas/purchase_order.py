"""
Purchase Order request and response schemas.

Monetary values are Decimal, serialized as strings in JSON output.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Item schemas ─────────────────────────────────────────────────────────────

class POItemIn(BaseModel):
    """Input schema for a single PO line item."""

    line_number: int = Field(..., ge=1)
    description: str = Field(..., min_length=1, max_length=500)
    quantity: Decimal = Field(..., gt=Decimal("0"))
    unit_price: Decimal = Field(..., ge=Decimal("0"))
    tax_rate_percent: Decimal = Field(Decimal("0"), ge=Decimal("0"))
    discount: Decimal = Field(Decimal("0"), ge=Decimal("0"))
    line_total: Decimal = Field(..., ge=Decimal("0"))


class POItemOut(BaseModel):
    """Response schema for a PO line item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    line_number: int
    description: str
    normalized_description: Optional[str]
    quantity: Decimal
    unit_price: Decimal
    tax_rate_percent: Decimal
    discount: Decimal
    line_total: Decimal
    created_at: datetime


# ── Purchase Order schemas ────────────────────────────────────────────────────

class POCreate(BaseModel):
    """Input schema for creating a Purchase Order."""

    po_number: str = Field(..., min_length=1, max_length=100)
    vendor_name: str = Field(..., min_length=1, max_length=255)
    vendor_tax_id: Optional[str] = Field(None, max_length=100)
    po_date: datetime
    currency: str = Field("INR", min_length=3, max_length=3)
    subtotal: Decimal = Field(..., ge=Decimal("0"))
    tax_amount: Decimal = Field(Decimal("0"), ge=Decimal("0"))
    discount_amount: Decimal = Field(Decimal("0"), ge=Decimal("0"))
    grand_total: Decimal = Field(..., ge=Decimal("0"))
    items: list[POItemIn] = Field(default_factory=list)


class POOut(BaseModel):
    """Response schema for a Purchase Order."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    po_number: str
    vendor_name: str
    vendor_tax_id: Optional[str]
    po_date: datetime
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    grand_total: Decimal
    status: str
    items: list[POItemOut]
    created_at: datetime
    updated_at: datetime
