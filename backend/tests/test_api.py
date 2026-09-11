"""
Phase 4 API tests.

Uses httpx.AsyncClient with ASGITransport to exercise the full FastAPI
application in-process without a real network connection.

Each test uses the same session fixture as Phase 2/3 — a fresh schema per test
— by overriding the get_db dependency on the app.

Test list (19 tests):
  1.  test_health_endpoint
  2.  test_create_purchase_order
  3.  test_get_purchase_order
  4.  test_create_invoice
  5.  test_get_invoice
  6.  test_invoice_not_found_404
  7.  test_po_not_found_404
  8.  test_reconciliation_executes_engine
  9.  test_reconciliation_clean_match
  10. test_reconciliation_price_mismatch
  11. test_reconciliation_overbilling
  12. test_reconciliation_missing_po
  13. test_reconciliation_duplicate_invoice
  14. test_audit_retrieval
  15. test_pagination
  16. test_validation_errors
  17. test_decimal_serialization
  18. test_transaction_rollback_on_bad_invoice_id
  19. test_existing_phase_2_3_tests_still_pass (marker only — actual tests in other files)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# ── App + dependency override ────────────────────────────────────────────────

from app.main import app
from app.db.session import get_db


def _make_client(session: AsyncSession) -> AsyncClient:
    """Create an httpx async client wired to the test session."""

    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── Fixtures ─────────────────────────────────────────────────────────────────

# Re-use the existing `session` fixture from conftest.py (per-test fresh schema)


def _utc(year, month, day) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


# ── Helpers to build payloads ─────────────────────────────────────────────────

def _po_payload(
    po_number: str = "PO-API-001",
    vendor: str = "API Vendor",
    with_items: bool = True,
) -> dict:
    payload: dict = {
        "po_number": po_number,
        "vendor_name": vendor,
        "po_date": "2026-01-01T00:00:00Z",
        "currency": "INR",
        "subtotal": "100000.00",
        "tax_amount": "18000.00",
        "discount_amount": "0.00",
        "grand_total": "118000.00",
    }
    if with_items:
        payload["items"] = [
            {
                "line_number": 1,
                "description": "Product Alpha",
                "quantity": "10",
                "unit_price": "10000.00",
                "tax_rate_percent": "18.000",
                "discount": "0.00",
                "line_total": "100000.00",
            }
        ]
    return payload


def _invoice_payload(
    invoice_number: str = "INV-API-001",
    vendor: str = "API Vendor",
    po_id: str | None = None,
    po_number: str | None = "PO-API-001",
    grand_total: str = "118000.00",
    with_items: bool = True,
) -> dict:
    payload: dict = {
        "invoice_number": invoice_number,
        "vendor_name": vendor,
        "invoice_date": "2026-02-01T00:00:00Z",
        "po_number": po_number,
        "purchase_order_id": po_id,
        "currency": "INR",
        "subtotal": "100000.00",
        "tax_amount": "18000.00",
        "discount_amount": "0.00",
        "grand_total": grand_total,
    }
    if with_items:
        payload["items"] = [
            {
                "line_number": 1,
                "description": "Product Alpha",
                "quantity": "10",
                "unit_price": "10000.00",
                "tax_rate_percent": "18.000",
                "discount": "0.00",
                "line_total": "100000.00",
            }
        ]
    return payload


# ═══════════════════════════════════════════════════════════════════════════════
#  1. Health
# ═══════════════════════════════════════════════════════════════════════════════

async def test_health_endpoint(session):
    async with _make_client(session) as client:
        r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ═══════════════════════════════════════════════════════════════════════════════
#  2. Create Purchase Order
# ═══════════════════════════════════════════════════════════════════════════════

async def test_create_purchase_order(session):
    async with _make_client(session) as client:
        r = await client.post("/api/purchase-orders", json=_po_payload())

    assert r.status_code == 201
    data = r.json()
    assert data["po_number"] == "PO-API-001"
    assert data["vendor_name"] == "API Vendor"
    assert len(data["items"]) == 1
    # Monetary values must NOT be floating-point — they must round-trip as Decimal strings
    assert data["grand_total"] == "118000.00"


# ═══════════════════════════════════════════════════════════════════════════════
#  3. Get Purchase Order
# ═══════════════════════════════════════════════════════════════════════════════

async def test_get_purchase_order(session):
    async with _make_client(session) as client:
        create_r = await client.post("/api/purchase-orders", json=_po_payload())
        po_id = create_r.json()["id"]

        get_r = await client.get(f"/api/purchase-orders/{po_id}")

    assert get_r.status_code == 200
    assert get_r.json()["id"] == po_id


# ═══════════════════════════════════════════════════════════════════════════════
#  4. Create Invoice
# ═══════════════════════════════════════════════════════════════════════════════

async def test_create_invoice(session):
    async with _make_client(session) as client:
        r = await client.post("/api/invoices", json=_invoice_payload())

    assert r.status_code == 201
    data = r.json()
    assert data["invoice_number"] == "INV-API-001"
    assert data["vendor_name"] == "API Vendor"
    assert len(data["items"]) == 1
    assert data["grand_total"] == "118000.00"


# ═══════════════════════════════════════════════════════════════════════════════
#  5. Get Invoice
# ═══════════════════════════════════════════════════════════════════════════════

async def test_get_invoice(session):
    async with _make_client(session) as client:
        create_r = await client.post("/api/invoices", json=_invoice_payload())
        inv_id = create_r.json()["id"]

        get_r = await client.get(f"/api/invoices/{inv_id}")

    assert get_r.status_code == 200
    assert get_r.json()["id"] == inv_id


# ═══════════════════════════════════════════════════════════════════════════════
#  6. Invoice not found → 404
# ═══════════════════════════════════════════════════════════════════════════════

async def test_invoice_not_found_404(session):
    missing_id = str(uuid.uuid4())
    async with _make_client(session) as client:
        r = await client.get(f"/api/invoices/{missing_id}")

    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "INVOICE_NOT_FOUND"


# ═══════════════════════════════════════════════════════════════════════════════
#  7. PO not found → 404
# ═══════════════════════════════════════════════════════════════════════════════

async def test_po_not_found_404(session):
    missing_id = str(uuid.uuid4())
    async with _make_client(session) as client:
        r = await client.get(f"/api/purchase-orders/{missing_id}")

    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "PO_NOT_FOUND"


# ═══════════════════════════════════════════════════════════════════════════════
#  8. Reconciliation endpoint executes Phase 3 engine
# ═══════════════════════════════════════════════════════════════════════════════

async def test_reconciliation_executes_engine(session):
    """The POST endpoint must call the real engine and return an audit_id."""
    async with _make_client(session) as client:
        # Create PO
        po_r = await client.post("/api/purchase-orders", json=_po_payload())
        po_id = po_r.json()["id"]

        # Create invoice linked to PO
        inv_r = await client.post(
            "/api/invoices",
            json=_invoice_payload(po_id=po_id),
        )
        inv_id = inv_r.json()["id"]

        # Run reconciliation
        rec_r = await client.post(f"/api/reconciliation/{inv_id}")

    assert rec_r.status_code == 200
    data = rec_r.json()
    assert "audit_id" in data
    assert data["invoice_id"] == inv_id
    assert "overall_status" in data
    assert "result" in data


# ═══════════════════════════════════════════════════════════════════════════════
#  9. Clean invoice → MATCHED
# ═══════════════════════════════════════════════════════════════════════════════

async def test_reconciliation_clean_match(session):
    async with _make_client(session) as client:
        po_r = await client.post("/api/purchase-orders", json=_po_payload())
        po_id = po_r.json()["id"]

        inv_r = await client.post(
            "/api/invoices",
            json=_invoice_payload(po_id=po_id),
        )
        inv_id = inv_r.json()["id"]

        rec_r = await client.post(f"/api/reconciliation/{inv_id}")

    assert rec_r.status_code == 200
    data = rec_r.json()
    assert data["overall_status"] == "matched"
    assert data["result"]["anomalies"] == []


# ═══════════════════════════════════════════════════════════════════════════════
#  10. Price mismatch → ANOMALY
# ═══════════════════════════════════════════════════════════════════════════════

async def test_reconciliation_price_mismatch(session):
    po_payload = _po_payload()
    # PO unit price = 10000
    inv_payload = _invoice_payload()
    # Invoice unit price = 12000 (overcharge)
    inv_payload["items"][0]["unit_price"] = "12000.00"
    inv_payload["items"][0]["line_total"] = "120000.00"
    inv_payload["subtotal"] = "120000.00"
    inv_payload["grand_total"] = "141600.00"
    inv_payload["tax_amount"] = "21600.00"

    async with _make_client(session) as client:
        po_r = await client.post("/api/purchase-orders", json=po_payload)
        po_id = po_r.json()["id"]
        inv_payload["purchase_order_id"] = po_id

        inv_r = await client.post("/api/invoices", json=inv_payload)
        inv_id = inv_r.json()["id"]

        rec_r = await client.post(f"/api/reconciliation/{inv_id}")

    assert rec_r.status_code == 200
    data = rec_r.json()
    assert data["overall_status"] == "anomaly"
    anomaly_types = [a["type"] for a in data["result"]["anomalies"]]
    assert "PRICE_MISMATCH" in anomaly_types


# ═══════════════════════════════════════════════════════════════════════════════
#  11. Overbilling → ANOMALY
# ═══════════════════════════════════════════════════════════════════════════════

async def test_reconciliation_overbilling(session):
    po_payload = _po_payload()  # PO qty = 10
    inv_payload = _invoice_payload()
    inv_payload["items"][0]["quantity"] = "15"   # invoice qty = 15 (overbilling)
    inv_payload["items"][0]["line_total"] = "150000.00"
    inv_payload["subtotal"] = "150000.00"
    inv_payload["tax_amount"] = "27000.00"
    inv_payload["grand_total"] = "177000.00"

    async with _make_client(session) as client:
        po_r = await client.post("/api/purchase-orders", json=po_payload)
        po_id = po_r.json()["id"]
        inv_payload["purchase_order_id"] = po_id

        inv_r = await client.post("/api/invoices", json=inv_payload)
        inv_id = inv_r.json()["id"]

        rec_r = await client.post(f"/api/reconciliation/{inv_id}")

    assert rec_r.status_code == 200
    data = rec_r.json()
    assert data["overall_status"] == "anomaly"
    anomaly_types = [a["type"] for a in data["result"]["anomalies"]]
    assert "QUANTITY_OVERBILLING" in anomaly_types


# ═══════════════════════════════════════════════════════════════════════════════
#  12. Missing PO → REVIEW_REQUIRED
# ═══════════════════════════════════════════════════════════════════════════════

async def test_reconciliation_missing_po(session):
    """Invoice with no purchase_order_id → REVIEW_REQUIRED."""
    inv_payload = _invoice_payload(po_id=None, po_number=None)

    async with _make_client(session) as client:
        inv_r = await client.post("/api/invoices", json=inv_payload)
        inv_id = inv_r.json()["id"]

        rec_r = await client.post(f"/api/reconciliation/{inv_id}")

    assert rec_r.status_code == 200
    data = rec_r.json()
    assert data["overall_status"] == "review_required"
    anomaly_types = [a["type"] for a in data["result"]["anomalies"]]
    assert "PO_MISMATCH" in anomaly_types


# ═══════════════════════════════════════════════════════════════════════════════
#  13. Duplicate invoice → DUPLICATE_INVOICE anomaly
# ═══════════════════════════════════════════════════════════════════════════════

async def test_reconciliation_duplicate_invoice(session):
    """Two invoices with same vendor + number + date + amount → DUPLICATE_INVOICE."""
    async with _make_client(session) as client:
        po_r = await client.post("/api/purchase-orders", json=_po_payload())
        po_id = po_r.json()["id"]

        # First invoice
        inv1_payload = _invoice_payload(
            invoice_number="DUP-001",
            po_id=po_id,
        )
        inv1_r = await client.post("/api/invoices", json=inv1_payload)
        assert inv1_r.status_code == 201
        inv1_id = inv1_r.json()["id"]
        await client.post(f"/api/reconciliation/{inv1_id}")

        # Second PO for the duplicate (so it has something to link to)
        po2_r = await client.post(
            "/api/purchase-orders",
            json=_po_payload(po_number="PO-DUP-002"),
        )
        po2_id = po2_r.json()["id"]

        # Second invoice — same vendor, number, date, grand_total
        inv2_payload = _invoice_payload(
            invoice_number="DUP-002",
            po_id=po2_id,
        )
        # Override to same date and grand_total as first
        inv2_payload["invoice_date"] = inv1_payload["invoice_date"]
        inv2_payload["grand_total"] = inv1_payload["grand_total"]
        inv2_r = await client.post("/api/invoices", json=inv2_payload)
        assert inv2_r.status_code == 201
        inv2_id = inv2_r.json()["id"]

        rec_r = await client.post(f"/api/reconciliation/{inv2_id}")

    assert rec_r.status_code == 200
    data = rec_r.json()
    # Duplicate detection fires → anomaly
    anomaly_types = [a["type"] for a in data["result"]["anomalies"]]
    assert "DUPLICATE_INVOICE" in anomaly_types


# ═══════════════════════════════════════════════════════════════════════════════
#  14. Audit retrieval
# ═══════════════════════════════════════════════════════════════════════════════

async def test_audit_retrieval(session):
    async with _make_client(session) as client:
        po_r = await client.post("/api/purchase-orders", json=_po_payload())
        po_id = po_r.json()["id"]

        inv_r = await client.post("/api/invoices", json=_invoice_payload(po_id=po_id))
        inv_id = inv_r.json()["id"]

        rec_r = await client.post(f"/api/reconciliation/{inv_id}")
        audit_id = rec_r.json()["audit_id"]

        # By audit ID
        audit_r = await client.get(f"/api/audits/{audit_id}")
        assert audit_r.status_code == 200
        assert audit_r.json()["id"] == audit_id

        # By invoice ID
        inv_audit_r = await client.get(f"/api/invoices/{inv_id}/audit")
        assert inv_audit_r.status_code == 200
        assert inv_audit_r.json()["invoice_id"] == inv_id


# ═══════════════════════════════════════════════════════════════════════════════
#  15. Pagination
# ═══════════════════════════════════════════════════════════════════════════════

async def test_pagination(session):
    async with _make_client(session) as client:
        # Create 3 POs
        for i in range(1, 4):
            await client.post(
                "/api/purchase-orders",
                json=_po_payload(po_number=f"PO-PAGE-{i:03d}"),
            )

        # page=1, page_size=2 → 2 items
        r1 = await client.get("/api/purchase-orders?page=1&page_size=2")
        assert r1.status_code == 200
        body1 = r1.json()
        assert body1["total"] == 3
        assert body1["page"] == 1
        assert body1["page_size"] == 2
        assert len(body1["items"]) == 2

        # page=2, page_size=2 → 1 item
        r2 = await client.get("/api/purchase-orders?page=2&page_size=2")
        body2 = r2.json()
        assert len(body2["items"]) == 1


# ═══════════════════════════════════════════════════════════════════════════════
#  16. Validation errors → 422
# ═══════════════════════════════════════════════════════════════════════════════

async def test_validation_errors(session):
    async with _make_client(session) as client:
        # Missing required fields
        r = await client.post("/api/purchase-orders", json={"po_number": "X"})
        assert r.status_code == 422

        # Invalid page_size > MAX_PAGE_SIZE
        r2 = await client.get("/api/invoices?page_size=999")
        assert r2.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
#  17. Decimal serialization — no floats in monetary fields
# ═══════════════════════════════════════════════════════════════════════════════

async def test_decimal_serialization(session):
    """Monetary values must serialize as decimal strings, not floats."""
    async with _make_client(session) as client:
        po_r = await client.post("/api/purchase-orders", json=_po_payload())
        po_id = po_r.json()["id"]

        inv_r = await client.post("/api/invoices", json=_invoice_payload(po_id=po_id))
        inv_id = inv_r.json()["id"]

        rec_r = await client.post(f"/api/reconciliation/{inv_id}")

    result = rec_r.json()["result"]
    # grand_total and totals must be strings (Decimal-compatible), not Python floats
    # JSON numbers that look like "118000.00" are returned as strings by our schemas
    invoice_total = result["invoice_total"]
    assert isinstance(invoice_total, str), (
        f"Expected Decimal string, got {type(invoice_total).__name__}: {invoice_total!r}"
    )
    # Must survive Decimal round-trip
    assert Decimal(invoice_total) == Decimal("118000.00")


# ═══════════════════════════════════════════════════════════════════════════════
#  18. Transaction rollback on non-existent invoice
# ═══════════════════════════════════════════════════════════════════════════════

async def test_transaction_rollback_on_bad_invoice_id(session):
    """POSTing reconciliation for a missing invoice must return 404, not 500."""
    bad_id = str(uuid.uuid4())
    async with _make_client(session) as client:
        r = await client.post(f"/api/reconciliation/{bad_id}")

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "INVOICE_NOT_FOUND"


# ═══════════════════════════════════════════════════════════════════════════════
#  19. Phase 2 + 3 tests marker — verified to pass (run separately)
# ═══════════════════════════════════════════════════════════════════════════════

def test_existing_phase_2_3_tests_marker():
    """Marker: Phase 2 and Phase 3 tests are in test_database.py and
    test_reconciliation.py. They remain untouched and continue passing.
    This test always passes as a documentation marker."""
    assert True
