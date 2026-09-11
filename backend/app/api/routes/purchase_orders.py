"""
Purchase Order endpoints.

POST /api/purchase-orders       — create a PO (with items)
GET  /api/purchase-orders       — list POs (paginated, filterable)
GET  /api/purchase-orders/{id}  — get a single PO
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem
from app.schemas.common import PaginatedResponse, MAX_PAGE_SIZE, DEFAULT_PAGE_SIZE
from app.schemas.purchase_order import POCreate, POOut
from app.api.errors import AppError

router = APIRouter()


@router.post(
    "",
    response_model=POOut,
    status_code=201,
    summary="Create a Purchase Order",
)
async def create_purchase_order(
    body: POCreate,
    db: AsyncSession = Depends(get_db),
) -> POOut:
    """Create a new Purchase Order with optional line items.

    Returns 409 if the same po_number + vendor_name already exists.
    """
    # Conflict check
    existing = await db.execute(
        select(PurchaseOrder).where(
            PurchaseOrder.po_number == body.po_number,
            PurchaseOrder.vendor_name == body.vendor_name,
        )
    )
    if existing.scalar_one_or_none():
        raise AppError(
            status_code=409,
            code="PO_ALREADY_EXISTS",
            message=f"Purchase Order '{body.po_number}' for vendor '{body.vendor_name}' already exists.",
        )

    po = PurchaseOrder(
        po_number=body.po_number,
        vendor_name=body.vendor_name,
        vendor_tax_id=body.vendor_tax_id,
        po_date=body.po_date,
        currency=body.currency,
        subtotal=body.subtotal,
        tax_amount=body.tax_amount,
        discount_amount=body.discount_amount,
        grand_total=body.grand_total,
    )
    db.add(po)
    await db.flush()

    for item_in in body.items:
        item = PurchaseOrderItem(
            purchase_order_id=po.id,
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
    await db.refresh(po)

    # Reload with items
    result = await db.execute(
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.items))
        .where(PurchaseOrder.id == po.id)
    )
    po = result.scalar_one()
    return POOut.model_validate(po)


@router.get(
    "",
    response_model=PaginatedResponse[POOut],
    summary="List Purchase Orders",
)
async def list_purchase_orders(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    vendor: Optional[str] = Query(None, description="Filter by vendor name (partial match)"),
    status: Optional[str] = Query(None, description="Filter by PO status"),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[POOut]:
    """List Purchase Orders with optional filtering and pagination."""
    stmt = select(PurchaseOrder).options(selectinload(PurchaseOrder.items))
    count_stmt = select(func.count()).select_from(PurchaseOrder)

    if vendor:
        stmt = stmt.where(PurchaseOrder.vendor_name.ilike(f"%{vendor}%"))
        count_stmt = count_stmt.where(PurchaseOrder.vendor_name.ilike(f"%{vendor}%"))
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
        count_stmt = count_stmt.where(PurchaseOrder.status == status)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    stmt = stmt.order_by(PurchaseOrder.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    pos = result.scalars().all()

    return PaginatedResponse(
        items=[POOut.model_validate(po) for po in pos],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/{po_id}",
    response_model=POOut,
    summary="Get a Purchase Order by ID",
)
async def get_purchase_order(
    po_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> POOut:
    """Retrieve a single Purchase Order. Returns 404 if not found."""
    result = await db.execute(
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.items))
        .where(PurchaseOrder.id == po_id)
    )
    po = result.scalar_one_or_none()
    if po is None:
        raise AppError(
            status_code=404,
            code="PO_NOT_FOUND",
            message=f"Purchase Order {po_id} not found.",
        )
    return POOut.model_validate(po)
