"""
Invoice request and response schemas.

Monetary values are Decimal, serialized as strings in JSON output.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Item schemas ─────────────────────────────────────────────────────────────

class InvoiceItemIn(BaseModel):
    """Input schema for a single invoice line item."""

    line_number: int = Field(..., ge=1)
    description: str = Field(..., min_length=1, max_length=500)
    quantity: Decimal = Field(..., gt=Decimal("0"))
    unit_price: Decimal = Field(..., ge=Decimal("0"))
    tax_rate_percent: Decimal = Field(Decimal("0"), ge=Decimal("0"))
    discount: Decimal = Field(Decimal("0"), ge=Decimal("0"))
    line_total: Decimal = Field(..., ge=Decimal("0"))


class InvoiceItemOut(BaseModel):
    """Response schema for an invoice line item."""

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


# ── Invoice schemas ───────────────────────────────────────────────────────────

class InvoiceCreate(BaseModel):
    """Input schema for creating an Invoice."""

    invoice_number: str = Field(..., min_length=1, max_length=100)
    vendor_name: str = Field(..., min_length=1, max_length=255)
    vendor_tax_id: Optional[str] = Field(None, max_length=100)
    invoice_date: datetime
    # PO linkage — optional; absence triggers REVIEW_REQUIRED on reconciliation
    po_number: Optional[str] = Field(None, max_length=100)
    purchase_order_id: Optional[uuid.UUID] = None
    currency: str = Field("INR", min_length=3, max_length=3)
    subtotal: Decimal = Field(..., ge=Decimal("0"))
    tax_amount: Decimal = Field(Decimal("0"), ge=Decimal("0"))
    discount_amount: Decimal = Field(Decimal("0"), ge=Decimal("0"))
    grand_total: Decimal = Field(..., ge=Decimal("0"))
    items: list[InvoiceItemIn] = Field(default_factory=list)


class InvoiceOut(BaseModel):
    """Response schema for an Invoice."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_number: str
    vendor_name: str
    vendor_tax_id: Optional[str]
    invoice_date: datetime
    po_number: Optional[str]
    purchase_order_id: Optional[uuid.UUID]
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    grand_total: Decimal
    status: str
    items: list[InvoiceItemOut]
    created_at: datetime
    updated_at: datetime
