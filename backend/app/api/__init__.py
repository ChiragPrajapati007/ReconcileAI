"""
API package — aggregates all route routers.
"""
from fastapi import APIRouter

from app.api.routes import health, purchase_orders, invoices, reconciliation, audits

api_router = APIRouter()

api_router.include_router(health.router, tags=["Health"])
api_router.include_router(purchase_orders.router, prefix="/api/purchase-orders", tags=["Purchase Orders"])
api_router.include_router(invoices.router, prefix="/api/invoices", tags=["Invoices"])
api_router.include_router(reconciliation.router, prefix="/api/reconciliation", tags=["Reconciliation"])
api_router.include_router(audits.router, prefix="/api", tags=["Audits"])
