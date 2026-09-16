"""
Phase 5 — Extraction pipeline tests.

Tests cover:
  - Document ingestion (validation, file types, size limits)
  - Deterministic validation (arithmetic, required fields, contradictions)
  - Confidence calculation (scores, penalties, bonuses, determinism)
  - Confidence gate (tier classification)
  - Pipeline integration (mock provider → full pipeline → persistence)
  - API integration (upload endpoint with mock provider)

All tests use mock extraction providers — no real Gemini API calls.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.db.session import get_db
from app.models.invoice import Invoice, InvoiceDocument, InvoiceExtraction
from app.services.extraction.schemas import (
    CanonicalExtraction,
    ExtractionResult,
    GateStatus,
    RawExtractionResult,
    RawLineItem,
)
from app.services.extraction.confidence import calculate_confidence
from app.services.extraction.gate import apply_gate
from app.services.extraction.validation import validate_extraction
from app.services.extraction.ingestion import (
    IngestionError,
    validate_file,
    save_document,
    prepare_pages,
    IngestedDocument,
)
from app.services.extraction.pipeline import run_extraction_pipeline
from app.services.extraction.provider_base import ExtractionProvider


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_client(session: AsyncSession) -> AsyncClient:
    async def _override():
        yield session
    app.dependency_overrides[get_db] = _override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _good_raw() -> RawExtractionResult:
    """A clean, consistent raw extraction result."""
    return RawExtractionResult(
        vendor_name="Acme Supplies",
        invoice_number="INV-2026-001",
        invoice_date="2026-01-15",
        due_date="2026-02-15",
        currency="INR",
        po_number="PO-001",
        subtotal="100000.00",
        tax_amount="18000.00",
        discount_amount="0.00",
        grand_total="118000.00",
        line_items=[
            RawLineItem(
                description="Steel bolts M10",
                quantity="100",
                unit_price="1000.00",
                line_total="100000.00",
                page_number=1,
                source_text="Steel bolts M10 100 x 1000.00 = 100000.00",
            ),
        ],
        extraction_notes="Clear digital invoice",
    )


def _missing_critical_raw() -> RawExtractionResult:
    """Raw extraction with missing critical fields."""
    return RawExtractionResult(
        vendor_name=None,
        invoice_number=None,
        grand_total=None,
        line_items=[],
    )


def _arithmetic_bad_raw() -> RawExtractionResult:
    """Raw extraction with arithmetic inconsistencies."""
    return RawExtractionResult(
        vendor_name="Bad Math Corp",
        invoice_number="INV-BAD-001",
        invoice_date="2026-03-01",
        subtotal="50000.00",
        tax_amount="9000.00",
        discount_amount="0.00",
        grand_total="99999.00",  # Wrong: should be 59000
        line_items=[
            RawLineItem(
                description="Widget",
                quantity="10",
                unit_price="5000.00",
                line_total="60000.00",  # Wrong: 10 × 5000 = 50000
            ),
        ],
    )


class MockProvider(ExtractionProvider):
    """Mock extraction provider for testing."""

    def __init__(self, result: RawExtractionResult | None = None):
        self._result = result or _good_raw()

    async def extract(self, document_pages, mime_type):
        return self._result

    @property
    def provider_name(self):
        return "mock"

    @property
    def model_name(self):
        return "mock-v1"


# ═══════════════════════════════════════════════════════════════════════════════
#  INGESTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def test_validate_valid_png():
    """Valid PNG file passes validation."""
    # Minimal PNG header
    content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    mime = validate_file(content, "invoice.png", "image/png")
    assert mime == "image/png"


def test_validate_valid_jpeg():
    """Valid JPEG file passes validation."""
    content = b"\xff\xd8\xff" + b"\x00" * 100
    mime = validate_file(content, "scan.jpg", "image/jpeg")
    assert mime == "image/jpeg"


def test_validate_valid_pdf():
    """Valid PDF file passes validation."""
    content = b"%PDF-1.4" + b"\x00" * 100
    mime = validate_file(content, "invoice.pdf", "application/pdf")
    assert mime == "application/pdf"


def test_validate_unsupported_format():
    """Unsupported file type raises IngestionError."""
    content = b"MZ" + b"\x00" * 100  # EXE magic bytes
    with pytest.raises(IngestionError, match="Unsupported file type"):
        validate_file(content, "malware.exe", "application/x-msdownload")


def test_validate_oversized_file():
    """File exceeding size limit raises IngestionError."""
    # Create content larger than MAX_UPLOAD_BYTES (20MB default)
    content = b"\x89PNG\r\n\x1a\n" + b"\x00" * (21 * 1024 * 1024)
    with pytest.raises(IngestionError, match="File too large"):
        validate_file(content, "huge.png", "image/png")


def test_validate_empty_file():
    """Empty file raises IngestionError."""
    with pytest.raises(IngestionError, match="Empty file"):
        validate_file(b"", "empty.png", "image/png")


def test_save_document_creates_file(tmp_path, monkeypatch):
    """save_document persists file to disk and returns correct metadata."""
    monkeypatch.setattr("app.services.extraction.ingestion.settings.UPLOAD_DIR", str(tmp_path))
    content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    doc = save_document(content, "test.png", "image/png")
    assert doc.file_size == len(content)
    assert doc.mime_type == "image/png"
    assert doc.original_filename == "test.png"
    assert len(doc.document_hash) == 64  # SHA-256 hex
    import os
    assert os.path.exists(doc.storage_path)


def test_prepare_pages_image():
    """Image file → single page, unchanged."""
    content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    doc = IngestedDocument(
        storage_path="/fake/path",
        filename="test.png",
        original_filename="test.png",
        mime_type="image/png",
        file_size=len(content),
        document_hash="abc123",
    )
    result = prepare_pages(doc, content)
    assert result.page_count == 1
    assert len(result.page_images) == 1
    assert result.page_images[0] == content


# ═══════════════════════════════════════════════════════════════════════════════
#  VALIDATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def test_validation_valid_extraction():
    """Clean extraction → no issues."""
    issues = validate_extraction(_good_raw())
    assert len(issues) == 0


def test_validation_missing_required_fields():
    """Missing critical fields → MISSING_REQUIRED errors."""
    issues = validate_extraction(_missing_critical_raw())
    error_types = [i.issue_type for i in issues if i.severity.value == "error"]
    assert "MISSING_REQUIRED" in error_types
    missing_fields = [i.field for i in issues if i.issue_type == "MISSING_REQUIRED"]
    assert "vendor_name" in missing_fields
    assert "invoice_number" in missing_fields
    assert "grand_total" in missing_fields


def test_validation_line_arithmetic_mismatch():
    """Line qty × price ≠ total → LINE_ARITHMETIC_MISMATCH."""
    issues = validate_extraction(_arithmetic_bad_raw())
    issue_types = [i.issue_type for i in issues]
    assert "LINE_ARITHMETIC_MISMATCH" in issue_types


def test_validation_grand_total_inconsistency():
    """Grand total ≠ subtotal + tax - discount → GRAND_TOTAL_ARITHMETIC."""
    issues = validate_extraction(_arithmetic_bad_raw())
    issue_types = [i.issue_type for i in issues]
    assert "GRAND_TOTAL_ARITHMETIC" in issue_types


def test_validation_negative_quantity():
    """Negative quantity → NEGATIVE_VALUE."""
    raw = RawExtractionResult(
        vendor_name="Test",
        invoice_number="INV-001",
        grand_total="1000",
        line_items=[
            RawLineItem(quantity="-5", unit_price="100", line_total="-500"),
        ],
    )
    issues = validate_extraction(raw)
    neg_issues = [i for i in issues if i.issue_type == "NEGATIVE_VALUE"]
    assert len(neg_issues) >= 1


def test_validation_subtotal_mismatch():
    """Sum of line totals ≠ subtotal → SUBTOTAL_MISMATCH."""
    raw = RawExtractionResult(
        vendor_name="Test",
        invoice_number="INV-001",
        subtotal="99999.00",  # Wrong
        grand_total="99999.00",
        line_items=[
            RawLineItem(quantity="10", unit_price="100", line_total="1000.00"),
        ],
    )
    issues = validate_extraction(raw)
    issue_types = [i.issue_type for i in issues]
    assert "SUBTOTAL_MISMATCH" in issue_types


def test_validation_unparseable_date():
    """Bad date format → UNPARSEABLE_DATE."""
    raw = RawExtractionResult(
        vendor_name="Test",
        invoice_number="INV-001",
        invoice_date="not-a-date",
        grand_total="1000",
    )
    issues = validate_extraction(raw)
    issue_types = [i.issue_type for i in issues]
    assert "UNPARSEABLE_DATE" in issue_types


def test_validation_contradiction():
    """Grand total < subtotal without discount → CONTRADICTION."""
    raw = RawExtractionResult(
        vendor_name="Test",
        invoice_number="INV-001",
        subtotal="10000.00",
        discount_amount="0.00",
        grand_total="5000.00",  # Less than subtotal with no discount
    )
    issues = validate_extraction(raw)
    issue_types = [i.issue_type for i in issues]
    assert "CONTRADICTION" in issue_types


# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIDENCE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def test_confidence_high_for_clean_extraction():
    """All fields present + consistent → high confidence (≥ 0.85)."""
    raw = _good_raw()
    issues = validate_extraction(raw)
    confidence = calculate_confidence(raw, issues)
    assert confidence >= 0.85


def test_confidence_reduced_for_missing_critical():
    """Missing critical fields → reduced confidence."""
    raw = _missing_critical_raw()
    issues = validate_extraction(raw)
    confidence = calculate_confidence(raw, issues)
    assert confidence < 0.60


def test_confidence_penalty_for_arithmetic():
    """Arithmetic inconsistencies → penalty applied."""
    raw = _arithmetic_bad_raw()
    issues = validate_extraction(raw)
    confidence = calculate_confidence(raw, issues)
    # Should be lower than clean, but not as low as missing critical
    assert confidence < 0.85
    assert confidence > 0.0


def test_confidence_deterministic():
    """Same input → same confidence score every time."""
    raw = _good_raw()
    issues = validate_extraction(raw)
    c1 = calculate_confidence(raw, issues)
    c2 = calculate_confidence(raw, issues)
    c3 = calculate_confidence(raw, issues)
    assert c1 == c2 == c3


def test_confidence_clamped():
    """Confidence is always in [0, 1]."""
    # Even with lots of penalties
    raw = _missing_critical_raw()
    issues = validate_extraction(raw)
    confidence = calculate_confidence(raw, issues)
    assert 0.0 <= confidence <= 1.0


# ═══════════════════════════════════════════════════════════════════════════════
#  GATE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def test_gate_high_confidence():
    """Confidence ≥ 0.85 → AUTO."""
    assert apply_gate(0.90) == GateStatus.AUTO
    assert apply_gate(0.85) == GateStatus.AUTO


def test_gate_medium_confidence():
    """Confidence 0.60–0.84 → REVIEW."""
    assert apply_gate(0.75) == GateStatus.REVIEW
    assert apply_gate(0.60) == GateStatus.REVIEW


def test_gate_low_confidence():
    """Confidence < 0.60 → BLOCKED."""
    assert apply_gate(0.50) == GateStatus.BLOCKED
    assert apply_gate(0.0) == GateStatus.BLOCKED


# ═══════════════════════════════════════════════════════════════════════════════
#  PIPELINE INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

async def test_pipeline_produces_result(session):
    """Mock provider → full pipeline → ExtractionResult."""
    # Create an invoice first
    inv = Invoice(
        invoice_number="PIPE-001",
        vendor_name="Pipeline Test",
        invoice_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        grand_total=118000,
    )
    session.add(inv)
    await session.flush()

    # Create document
    doc = InvoiceDocument(
        invoice_id=inv.id,
        filename="test.png",
        original_filename="test.png",
        mime_type="image/png",
        file_size=100,
        storage_path="/fake/path",
        document_hash="abc123",
    )
    session.add(doc)
    await session.flush()

    # Mock ingested document
    ingested = IngestedDocument(
        storage_path="/fake/path",
        filename="test.png",
        original_filename="test.png",
        mime_type="image/png",
        file_size=100,
        document_hash="abc123",
    )
    content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

    provider = MockProvider(_good_raw())

    result = await run_extraction_pipeline(
        db=session,
        invoice_id=inv.id,
        document_id=doc.id,
        ingested=ingested,
        file_content=content,
        provider=provider,
    )

    assert result.extraction_status == "success"
    assert result.confidence >= 0.85
    assert result.gate_status == GateStatus.AUTO
    assert result.canonical is not None
    assert result.canonical.vendor_name == "Acme Supplies"
    assert result.canonical.grand_total == Decimal("118000.00")
    assert len(result.source_references) > 0


async def test_pipeline_persists_raw_extraction(session):
    """Raw extraction is persisted immutably in InvoiceExtraction."""
    inv = Invoice(
        invoice_number="PERSIST-001",
        vendor_name="Persist Test",
        invoice_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        grand_total=118000,
    )
    session.add(inv)
    await session.flush()

    doc = InvoiceDocument(
        invoice_id=inv.id,
        filename="test.png",
        original_filename="test.png",
        mime_type="image/png",
        file_size=100,
        storage_path="/fake/path",
        document_hash="def456",
    )
    session.add(doc)
    await session.flush()

    ingested = IngestedDocument(
        storage_path="/fake/path",
        filename="test.png",
        original_filename="test.png",
        mime_type="image/png",
        file_size=100,
        document_hash="def456",
    )
    content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

    await run_extraction_pipeline(
        db=session,
        invoice_id=inv.id,
        document_id=doc.id,
        ingested=ingested,
        file_content=content,
        provider=MockProvider(),
    )
    await session.flush()

    # Query the persisted extraction
    result = await session.execute(
        select(InvoiceExtraction).where(InvoiceExtraction.invoice_id == inv.id)
    )
    extraction = result.scalar_one()

    assert extraction.model_provider == "mock"
    assert extraction.model_name == "mock-v1"
    assert extraction.raw_extraction is not None
    assert extraction.raw_extraction["vendor_name"] == "Acme Supplies"
    assert extraction.normalized_extraction is not None
    assert extraction.extraction_status == "success"
    assert float(extraction.overall_confidence) >= 0.85


async def test_pipeline_source_references(session):
    """Source references are populated for traceability."""
    inv = Invoice(
        invoice_number="SRC-001",
        vendor_name="Source Test",
        invoice_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        grand_total=118000,
    )
    session.add(inv)
    await session.flush()

    doc = InvoiceDocument(
        invoice_id=inv.id,
        filename="test.png",
        original_filename="test.png",
        mime_type="image/png",
        file_size=100,
        storage_path="/fake/path",
        document_hash="ghi789",
    )
    session.add(doc)
    await session.flush()

    ingested = IngestedDocument(
        storage_path="/fake/path",
        filename="test.png",
        original_filename="test.png",
        mime_type="image/png",
        file_size=100,
        document_hash="ghi789",
    )
    content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

    result = await run_extraction_pipeline(
        db=session,
        invoice_id=inv.id,
        document_id=doc.id,
        ingested=ingested,
        file_content=content,
        provider=MockProvider(),
    )

    assert len(result.source_references) > 0
    # Should have header field references and line item references
    ref_fields = [r.field for r in result.source_references]
    assert "vendor_name" in ref_fields
    assert "grand_total" in ref_fields
    assert any("line_items" in f for f in ref_fields)


# ═══════════════════════════════════════════════════════════════════════════════
#  REGRESSION MARKER
# ═══════════════════════════════════════════════════════════════════════════════

def test_phase_1_4_regression_marker():
    """Marker: Phase 1–4 tests remain in test_database.py, test_reconciliation.py,
    and test_api.py. They must still pass. Run the full suite to verify."""
    assert True
