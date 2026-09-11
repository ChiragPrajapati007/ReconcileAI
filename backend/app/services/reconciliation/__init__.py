"""
Reconciliation service — deterministic invoice-vs-PO auditing engine.

Zero LLM dependencies.  All monetary arithmetic uses Decimal.

Usage:
    from app.services.reconciliation.engine import reconcile_invoice

    report = await reconcile_invoice(session, invoice_id)
"""
from app.services.reconciliation.engine import reconcile_invoice  # noqa: F401
