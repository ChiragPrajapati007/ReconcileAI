"""
ReconcileAI — FastAPI application entry point.

Registers:
  - CORS middleware
  - API routes  (health, purchase-orders, invoices, reconciliation, audits)
  - Centralized error handlers
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api import api_router
from app.api.errors import register_error_handlers

app = FastAPI(
    title=settings.PROJECT_NAME,
    description=(
        "ReconcileAI deterministic invoice-vs-PO reconciliation API. "
        "All financial arithmetic uses Decimal; no LLM calls in the reconciliation engine."
    ),
    version="0.4.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ── Middleware ────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(api_router)

# ── Error handlers ────────────────────────────────────────────────────────────
register_error_handlers(app)
