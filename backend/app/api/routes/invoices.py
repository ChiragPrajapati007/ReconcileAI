"""
Invoice endpoints.

POST /api/invoices       — create an invoice (with items)
GET  /api/invoices       — list invoices (paginated, filterable)
GET  /api/invoices/{id}  — get a single invoice
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.invoice import Invoice, InvoiceItem
from app.schemas.common import PaginatedResponse, MAX_PAGE_SIZE, DEFAULT_PAGE_SIZE
from app.schemas.invoice import InvoiceCreate, InvoiceOut
from app.api.errors import AppError

router = APIRouter()


@router.post(
    "",
    response_model=InvoiceOut,
    status_code=201,
    summary="Create an Invoice",
)
async def create_invoice(
    body: InvoiceCreate,
    db: AsyncSession = Depends(get_db),
) -> InvoiceOut:
    """Create a new Invoice with optional line items.

    Returns 409 if the same vendor_name + invoice_number already exists.
    """
    # Conflict check (unique per vendor + invoice_number)
    existing = await db.execute(
        select(Invoice).where(
            Invoice.vendor_name == body.vendor_name,
            Invoice.invoice_number == body.invoice_number,
        )
    )
    if existing.scalar_one_or_none():
        raise AppError(
            status_code=409,
            code="INVOICE_ALREADY_EXISTS",
            message=f"Invoice '{body.invoice_number}' for vendor '{body.vendor_name}' already exists.",
        )

    invoice = Invoice(
        invoice_number=body.invoice_number,
        vendor_name=body.vendor_name,
        vendor_tax_id=body.vendor_tax_id,
        invoice_date=body.invoice_date,
        po_number=body.po_number,
        purchase_order_id=body.purchase_order_id,
        currency=body.currency,
        subtotal=body.subtotal,
        tax_amount=body.tax_amount,
        discount_amount=body.discount_amount,
        grand_total=body.grand_total,
    )
    db.add(invoice)
    await db.flush()

    for item_in in body.items:
        item = InvoiceItem(
            invoice_id=invoice.id,
            line_number=item_in.line_number,
            description=item_in.description,
            quantity=item_in.quantity,
            unit_price=item_in.unit_price,
            tax_rate_percent=item_in.tax_rate_percent,
            discount=item_in.discount,
            line_total=item_in.line_total,
        )
        db.add(item)

    await db.commit()

    # Reload with items
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.items))
        .where(Invoice.id == invoice.id)
    )
    invoice = result.scalar_one()
    return InvoiceOut.model_validate(invoice)


@router.get(
    "",
    response_model=PaginatedResponse[InvoiceOut],
    summary="List Invoices",
)
async def list_invoices(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    vendor: Optional[str] = Query(None, description="Filter by vendor name (partial match)"),
    status: Optional[str] = Query(None, description="Filter by invoice status"),
    po_number: Optional[str] = Query(None, description="Filter by PO number"),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[InvoiceOut]:
    """List Invoices with optional filtering and pagination."""
    stmt = select(Invoice).options(selectinload(Invoice.items))
    count_stmt = select(func.count()).select_from(Invoice)

    if vendor:
        stmt = stmt.where(Invoice.vendor_name.ilike(f"%{vendor}%"))
        count_stmt = count_stmt.where(Invoice.vendor_name.ilike(f"%{vendor}%"))
    if status:
        stmt = stmt.where(Invoice.status == status)
        count_stmt = count_stmt.where(Invoice.status == status)
    if po_number:
        stmt = stmt.where(Invoice.po_number == po_number)
        count_stmt = count_stmt.where(Invoice.po_number == po_number)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    stmt = stmt.order_by(Invoice.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    invoices = result.scalars().all()

    return PaginatedResponse(
        items=[InvoiceOut.model_validate(inv) for inv in invoices],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/{invoice_id}",
    response_model=InvoiceOut,
    summary="Get an Invoice by ID",
)
async def get_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> InvoiceOut:
    """Retrieve a single Invoice. Returns 404 if not found."""
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.items))
        .where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if invoice is None:
        raise AppError(
            status_code=404,
            code="INVOICE_NOT_FOUND",
            message=f"Invoice {invoice_id} not found.",
        )
    return InvoiceOut.model_validate(invoice)
