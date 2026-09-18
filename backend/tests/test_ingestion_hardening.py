"""
Tests for Phase 10A Ingestion Hardening.
Verify transaction boundaries, duplicate detection, and pipeline failure handling.
"""
from __future__ import annotations

import io
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.db.session import get_db
from app.models.enums import InvoiceStatus
from app.models.invoice import Invoice
from app.services.extraction.pipeline import ExtractionPipelineError
from app.services.reconciliation.engine import ReconciliationError

# Mock provider setup matching existing tests
from tests.test_ingestion_flow import _mock_gemini_provider


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncClient:
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def mock_provider():
    with patch("app.api.routes.extraction._get_provider", return_value=_mock_gemini_provider()):
        yield


@pytest.mark.asyncio
async def test_ingest_duplicate_hash_rejected(
    client: AsyncClient,
    session: AsyncSession,
    mock_provider
):
    """Rule A: Reject identical document upload hashes immediately."""
    file_content = b"duplicate pdf content"
    
    # First upload should succeed
    files1 = {"file": ("test1.pdf", io.BytesIO(file_content), "application/pdf")}
    resp1 = await client.post("/api/extraction/ingest", files=files1)
    assert resp1.status_code == 200

    # Second upload of the EXACT same content should fail 409
    files2 = {"file": ("test2.pdf", io.BytesIO(file_content), "application/pdf")}
    resp2 = await client.post("/api/extraction/ingest", files=files2)
    
    assert resp2.status_code == 409
    assert resp2.json()["error"]["code"] == "DUPLICATE_DOCUMENT"


@pytest.mark.asyncio
async def test_ingest_pipeline_error_preserves_provisional_invoice(
    client: AsyncClient,
    session: AsyncSession,
    mock_provider
):
    """
    If the extraction pipeline crashes completely (e.g. malformed PDF),
    the endpoint returns 500 but the provisional invoice is preserved
    in the database for recovery.
    """
    file_content = b"malformed pdf content"
    files = {"file": ("malformed.pdf", io.BytesIO(file_content), "application/pdf")}
    
    with patch(
        "app.api.routes.extraction.run_extraction_pipeline",
        side_effect=ExtractionPipelineError("PDF conversion failed")
    ):
        response = await client.post("/api/extraction/ingest", files=files)
        
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "EXTRACTION_FAILED"
    
    # Verify the provisional invoice was still persisted (Commit 1 succeeded)
    inv_res = await session.execute(select(Invoice).where(Invoice.vendor_name == "PROVISIONAL INVOICE"))
    invoices = inv_res.scalars().all()
    # At least one provisional invoice exists (from this test)
    assert len(invoices) >= 1
    
    # Check that it is in DRAFT state
    last_invoice = invoices[-1]
    assert last_invoice.status == InvoiceStatus.DRAFT


@pytest.mark.asyncio
async def test_ingest_reconciliation_error_rolls_back_safely(
    client: AsyncClient,
    session: AsyncSession,
    mock_provider
):
    """
    If populate or reconcile crashes, it rolls back that specific transaction.
    The invoice remains in DRAFT state and no partial anomalies are committed.
    """
    file_content = b"valid pdf that crashes reconcile"
    files = {"file": ("reconcile_crash.pdf", io.BytesIO(file_content), "application/pdf")}
    
    with patch(
        "app.api.routes.extraction.reconcile_invoice",
        side_effect=ReconciliationError("Database connection lost during reconciliation")
    ):
        response = await client.post("/api/extraction/ingest", files=files)
        
    assert response.status_code == 200
    data = response.json()
    assert data["reconciled"] is False
    assert data["gate_status"] == "auto" # Population succeeded, reconcile failed
    
    invoice_id = uuid.UUID(data["invoice_id"])
    
    # Refresh invoice from DB
    inv_res = await session.execute(select(Invoice).where(Invoice.id == invoice_id))
    inv = inv_res.scalar_one()
    
    # Since populate and reconcile share a transaction, the population is rolled back too!
    assert inv.status == InvoiceStatus.DRAFT
    assert inv.vendor_name == "PROVISIONAL INVOICE"
