"""
Reconciliation endpoints.

POST /api/reconciliation/{invoice_id}  — run Phase 3 engine on the invoice
GET  /api/reconciliation/{invoice_id}  — retrieve the most recent result
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.audit import Audit, ReconciliationResult, Anomaly, Evidence
from app.models.invoice import Invoice
from app.schemas.reconciliation import ReconcileResponse, ReconciliationResultOut
from app.services.reconciliation.engine import reconcile_invoice, ReconciliationError
from app.api.errors import AppError

router = APIRouter()


def _load_result_stmt(invoice_id: uuid.UUID):
    """Build a query that loads the most recent audit + result + anomalies + evidence."""
    return (
        select(Audit)
        .where(Audit.invoice_id == invoice_id)
        .options(
            selectinload(Audit.reconciliation_result).selectinload(
                ReconciliationResult.anomalies
            ).selectinload(Anomaly.evidence)
        )
        .order_by(Audit.created_at.desc())
    )


@router.post(
    "/{invoice_id}",
    response_model=ReconcileResponse,
    summary="Run reconciliation for an Invoice",
)
async def run_reconciliation(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ReconcileResponse:
    """Execute the Phase 3 deterministic reconciliation engine for an invoice.

    - Loads the invoice and its linked PO.
    - Runs the deterministic engine (zero LLM calls).
    - Persists audit, result, anomalies, evidence, and ledger entries atomically.
    - Commits the transaction. On any error, the transaction is rolled back.

    Returns the full reconciliation result.
    Returns 404 if the invoice does not exist.
    """
    # Verify invoice exists before calling the engine
    inv_check = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    if inv_check.scalar_one_or_none() is None:
        raise AppError(
            status_code=404,
            code="INVOICE_NOT_FOUND",
            message=f"Invoice {invoice_id} not found.",
        )

    try:
        # Engine owns all DB writes; we commit after it returns.
        report = await reconcile_invoice(db, invoice_id)
        await db.commit()
    except ReconciliationError as exc:
        await db.rollback()
        raise AppError(
            status_code=404,
            code="RECONCILIATION_ERROR",
            message=str(exc),
        ) from exc
    except Exception:
        await db.rollback()
        raise

    # Reload persisted result for the response
    audit_result = await db.execute(_load_result_stmt(invoice_id))
    audit = audit_result.scalars().first()
    if audit is None or audit.reconciliation_result is None:
        raise AppError(
            status_code=500,
            code="INTERNAL_ERROR",
            message="Reconciliation completed but result could not be retrieved.",
        )

    return ReconcileResponse(
        audit_id=audit.id,
        invoice_id=invoice_id,
        overall_status=audit.reconciliation_result.overall_status,
        result=ReconciliationResultOut.model_validate(audit.reconciliation_result),
    )


@router.get(
    "/{invoice_id}",
    response_model=ReconcileResponse,
    summary="Get the most recent reconciliation result for an Invoice",
)
async def get_reconciliation(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ReconcileResponse:
    """Retrieve the most recent reconciliation result for an invoice.

    Returns 404 if no reconciliation has been run yet.
    """
    audit_result = await db.execute(_load_result_stmt(invoice_id))
    audit = audit_result.scalars().first()

    if audit is None:
        raise AppError(
            status_code=404,
            code="RECONCILIATION_NOT_FOUND",
            message=f"No reconciliation result found for invoice {invoice_id}.",
        )

    if audit.reconciliation_result is None:
        raise AppError(
            status_code=404,
            code="RECONCILIATION_RESULT_MISSING",
            message=f"Audit exists but has no reconciliation result for invoice {invoice_id}.",
        )

    return ReconcileResponse(
        audit_id=audit.id,
        invoice_id=invoice_id,
        overall_status=audit.reconciliation_result.overall_status,
        result=ReconciliationResultOut.model_validate(audit.reconciliation_result),
    )
