"""
Tests for Phase 9 Autonomous Ingestion Flow.
"""
from __future__ import annotations

import io
import uuid
from unittest.mock import patch, AsyncMock

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models.enums import ExtractionStatus, InvoiceStatus
from app.models.invoice import Invoice, InvoiceExtraction
from app.services.extraction.schemas import CanonicalExtraction, ExtractionResult, RawExtractionResult, GateStatus, RawLineItem
from app.services.extraction.gemini_provider import GeminiProvider
from decimal import Decimal


def _mock_gemini_provider() -> GeminiProvider:
    provider = AsyncMock(spec=GeminiProvider)
    provider.provider_name = "mock_provider"
    provider.model_name = "mock_model"
    
    provider.extract.return_value = RawExtractionResult(
        vendor_name="Test Vendor",
        invoice_number="INV-001",
        invoice_date="2024-01-01",
        due_date="2024-01-31",
        currency="USD",
        po_number="PO-1234",
        subtotal="100.00",
        tax_amount="0.00",
        discount_amount="0.00",
        grand_total="100.00",
        line_items=[
            RawLineItem(
                description="Test Item",
                quantity="1",
                unit_price="100.00",
                line_total="100.00",
                page_number=1,
            )
        ],
    )
    return provider


@pytest.fixture
def mock_provider():
    with patch("app.api.routes.extraction._get_provider", return_value=_mock_gemini_provider()):
        yield


from httpx import ASGITransport, AsyncClient
from app.db.session import get_db

@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncClient:
    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ingest_document_success(
    client: AsyncClient,
    session: AsyncSession,
    mock_provider
):
    """Test that a valid upload orchestrates end-to-end extraction and population."""
    
    file_content = b"dummy pdf content"
    files = {"file": ("test_invoice.pdf", io.BytesIO(file_content), "application/pdf")}
    
    response = await client.post("/api/extraction/ingest", files=files)
    
    assert response.status_code == 200
    data = response.json()
    assert "invoice_id" in data
    assert "document_id" in data
    assert data["gate_status"] == "auto"
    # Will be False if PO isn't found and reconciliation fails, but we mocked high confidence
    
    invoice_id = uuid.UUID(data["invoice_id"])
    
    # Verify invoice was populated
    inv_res = await session.execute(select(Invoice).where(Invoice.id == invoice_id))
    inv = inv_res.scalar_one()
    
    assert inv.vendor_name == "Test Vendor"
    assert inv.invoice_number == "INV-001"
    assert inv.po_number == "PO-1234"
    
    # Should not be DRAFT anymore if population succeeded
    assert inv.status == InvoiceStatus.UNDER_AUDIT


@pytest.mark.asyncio
async def test_ingest_document_blocked(
    client: AsyncClient,
    session: AsyncSession
):
    """Test when extraction confidence is BLOCKED, the gate stops reconciliation."""
    
    blocked_provider = _mock_gemini_provider()
    # Mock return low confidence by returning empty raw result
    blocked_provider.extract.return_value = RawExtractionResult()

    with patch("app.api.routes.extraction._get_provider", return_value=blocked_provider):
        file_content = b"dummy pdf content"
        files = {"file": ("test_invoice_low_conf.pdf", io.BytesIO(file_content), "application/pdf")}
        
        response = await client.post("/api/extraction/ingest", files=files)
        
        assert response.status_code == 200
        data = response.json()
        assert data["gate_status"] == "blocked"
        assert data["reconciled"] is False
        
        invoice_id = uuid.UUID(data["invoice_id"])
        
        # Verify invoice remains provisional
        inv_res = await session.execute(select(Invoice).where(Invoice.id == invoice_id))
        inv = inv_res.scalar_one()
        
        assert inv.vendor_name == "PROVISIONAL INVOICE"
        assert inv.invoice_number.startswith("PROV-")
        assert inv.status == InvoiceStatus.DRAFT
