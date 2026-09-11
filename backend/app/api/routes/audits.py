"""
Audit endpoints.

GET /api/audits/{audit_id}             — retrieve a single audit by ID
GET /api/invoices/{invoice_id}/audit   — retrieve the latest audit for an invoice
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.audit import Audit, ReconciliationResult, Anomaly
from app.schemas.audit import AuditOut
from app.api.errors import AppError

router = APIRouter()


def _audit_with_result_stmt():
    """Return a selectinload chain for Audit → result → anomalies → evidence."""
    return (
        selectinload(Audit.reconciliation_result)
        .selectinload(ReconciliationResult.anomalies)
        .selectinload(Anomaly.evidence)
    )


@router.get(
    "/audits/{audit_id}",
    response_model=AuditOut,
    summary="Get an Audit by ID",
)
async def get_audit(
    audit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AuditOut:
    """Retrieve a single Audit by its ID, including the reconciliation result."""
    result = await db.execute(
        select(Audit)
        .options(_audit_with_result_stmt())
        .where(Audit.id == audit_id)
    )
    audit = result.scalar_one_or_none()
    if audit is None:
        raise AppError(
            status_code=404,
            code="AUDIT_NOT_FOUND",
            message=f"Audit {audit_id} not found.",
        )
    return AuditOut.model_validate(audit)


@router.get(
    "/invoices/{invoice_id}/audit",
    response_model=AuditOut,
    summary="Get the latest Audit for an Invoice",
)
async def get_invoice_audit(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AuditOut:
    """Retrieve the most recent Audit for a given Invoice.

    Returns 404 if no audit exists for the invoice yet.
    """
    result = await db.execute(
        select(Audit)
        .options(_audit_with_result_stmt())
        .where(Audit.invoice_id == invoice_id)
        .order_by(Audit.created_at.desc())
    )
    audit = result.scalars().first()
    if audit is None:
        raise AppError(
            status_code=404,
            code="AUDIT_NOT_FOUND",
            message=f"No audit found for invoice {invoice_id}.",
        )
    return AuditOut.model_validate(audit)
