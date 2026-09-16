"""
Phase 7 tests for Evidence-Backed Human Review Workflow.
"""
import uuid
from decimal import Decimal
from datetime import datetime, timezone
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.db.session import get_db
from app.models.invoice import Invoice, InvoiceItem, InvoiceDocument, InvoiceExtraction
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem
from app.models.audit import Audit, ReconciliationResult, Anomaly, Evidence, AuditCorrection


def _make_client(session) -> AsyncClient:
    async def _override_get_db():
        yield session
    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def seed_data(session):
    # Setup PO
    po_id = uuid.uuid4()
    po = PurchaseOrder(
        id=po_id,
        po_number="PO-100",
        vendor_name="Acme Corp",
        po_date=datetime.now(timezone.utc),
        subtotal=1000.0,
        tax_amount=100.0,
        discount_amount=0.0,
        grand_total=1100.0,
    )
    po_item = PurchaseOrderItem(
        purchase_order_id=po_id,
        line_number=1,
        description="Widget A",
        quantity=10.0,
        unit_price=100.0,
        line_total=1000.0,
    )
    session.add(po)
    session.add(po_item)
    
    # Setup Invoice (with a discrepancy in unit_price)
    inv_id = uuid.uuid4()
    inv = Invoice(
        id=inv_id,
        invoice_number="INV-100",
        vendor_name="Acme Corp",
        po_number="PO-100",
        purchase_order_id=po_id,
        invoice_date=datetime.now(timezone.utc),
        subtotal=1100.0,
        tax_amount=110.0,
        discount_amount=0.0,
        grand_total=1210.0,
    )
    inv_item = InvoiceItem(
        invoice_id=inv_id,
        line_number=1,
        description="Widget A",
        quantity=10.0,
        unit_price=110.0,
        line_total=1100.0,
        page_number=1,
        source_text="Widget A 10 $110 $1100",
    )
    
    # Setup Document
    doc_id = uuid.uuid4()
    doc = InvoiceDocument(
        id=doc_id,
        invoice_id=inv_id,
        filename="test.pdf",
        original_filename="test.pdf",
        mime_type="application/pdf",
        file_size=1024,
        storage_path="/tmp/test.pdf"
    )
    
    # Setup Extraction (to simulate a previous successful extraction)
    extraction_id = uuid.uuid4()
    ext = InvoiceExtraction(
        id=extraction_id,
        invoice_id=inv_id,
        document_id=doc_id,
        model_provider="gemini",
        model_name="gemini-1.5-pro",
        extraction_status="success",
        overall_confidence=0.9,
        raw_extraction={"vendor_name": "Acme Corp", "line_items": [{"unit_price": 110.0}]},
        normalized_extraction={
            "vendor_name": "Acme Corp",
            "invoice_number": "INV-100",
            "po_number": "PO-100",
            "currency": "USD",
            "subtotal": 1100.0,
            "tax_amount": 110.0,
            "discount_amount": 0.0,
            "grand_total": 1210.0,
            "line_items": [
                {
                    "line_number": 1,
                    "description": "Widget A",
                    "quantity": 10.0,
                    "unit_price": 110.0,
                    "line_total": 1100.0,
                    "page_number": 1,
                    "source_text": "Widget A 10 $110 $1100"
                }
            ]
        }
    )
    
    session.add(inv)
    session.add(inv_item)
    session.add(doc)
    session.add(ext)
    await session.commit()
    
    return {"po_id": str(po_id), "inv_id": str(inv_id), "doc_id": str(doc_id), "extraction_id": str(extraction_id)}


@pytest.mark.asyncio
async def test_review_workflow_full(session, seed_data):
    client = _make_client(session)
    inv_id = seed_data["inv_id"]
    doc_id = seed_data["doc_id"]
    
    # 1. Reconcile from extraction to generate initial Anomaly + Evidence
    resp = await client.post(f"/api/extraction/{inv_id}/reconcile", json={"allow_review": True})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["overall_status"] == "anomaly"
    audit_id_1 = data["audit_id"]
    
    # Get anomalies
    resp = await client.get(f"/api/reconciliation/{inv_id}")
    assert resp.status_code == 200
    result_data = resp.json()["result"]
    anomalies = result_data["anomalies"]
    assert len(anomalies) > 0
    price_anomaly = [a for a in anomalies if a["type"] == "PRICE_MISMATCH"][0]
    anomaly_id = price_anomaly["id"]
    
    # Verify initial anomaly state
    assert price_anomaly["status"] == "open"
    assert price_anomaly["expected_value"] == "100.00"
    assert price_anomaly["actual_value"] == "110.00"
    
    # 2. Get evidence for anomaly
    resp = await client.get(f"/api/reconciliation/anomalies/{anomaly_id}/evidence")
    assert resp.status_code == 200
    evidence = resp.json()
    assert len(evidence) > 0
    
    # Check document linking & correctness
    doc_ev = [e for e in evidence if e["source_type"] == "invoice_field"]
    assert len(doc_ev) > 0
    assert doc_ev[0]["document_id"] == doc_id
    assert doc_ev[0]["expected_value"] == "100.00"
    assert doc_ev[0]["actual_value"] == "110.00"
    
    # Test A & B: page_number and source_text propagation
    assert doc_ev[0]["page_number"] == 1
    assert doc_ev[0]["source_text"] == "Widget A 10 $110 $1100"
    
    # 3. Transition anomaly status OPEN -> REVIEWED
    resp = await client.patch(f"/api/reconciliation/anomalies/{anomaly_id}", json={"status": "reviewed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "reviewed"
    
    # Invalid transition REVIEWED -> OPEN
    resp = await client.patch(f"/api/reconciliation/anomalies/{anomaly_id}", json={"status": "open"})
    assert resp.status_code == 400
    
    # Check extra fields are ignored in PATCH
    resp = await client.patch(f"/api/reconciliation/anomalies/{anomaly_id}", json={
        "status": "reviewed",
        "expected_value": "9999",
        "actual_value": "9999",
        "financial_impact": "9999"
    })
    # Schema validation might fail or it might be ignored. If ignored, the expected_value remains unchanged.
    if resp.status_code == 200:
        assert float(resp.json()["expected_value"]) == 100.0

    # 4. Submit Correction
    correction_payload = {
        "audit_id": audit_id_1,
        "field_path": "line_items[0].unit_price",
        "corrected_value": "100.0",
        "original_value": "110.0",
        "reason": "OCR read 110, but receipt shows 100",
        "corrected_by": "human"
    }
    resp = await client.post(f"/api/extraction/{inv_id}/corrections", json=correction_payload)
    assert resp.status_code == 201
    
    await client.post(f"/api/extraction/{inv_id}/corrections", json={
        "audit_id": audit_id_1, "field_path": "line_items[0].line_total", "corrected_value": "1000.0"
    })
    await client.post(f"/api/extraction/{inv_id}/corrections", json={
        "audit_id": audit_id_1, "field_path": "subtotal", "corrected_value": "1000.0"
    })
    await client.post(f"/api/extraction/{inv_id}/corrections", json={
        "audit_id": audit_id_1, "field_path": "tax_amount", "corrected_value": "100.0"
    })
    await client.post(f"/api/extraction/{inv_id}/corrections", json={
        "audit_id": audit_id_1, "field_path": "grand_total", "corrected_value": "1100.0"
    })

    # Test: Correction does NOT auto-resolve
    resp = await client.get(f"/api/reconciliation/{inv_id}")
    anomalies = resp.json()["result"]["anomalies"]
    price_anomaly = [a for a in anomalies if a["id"] == anomaly_id][0]
    assert price_anomaly["status"] == "reviewed"  # Still REVIEWED!
    
    # Test: Raw extraction immutability
    ext_row = await session.execute(select(InvoiceExtraction).where(InvoiceExtraction.invoice_id == inv_id))
    ext = ext_row.scalar_one()
    assert ext.raw_extraction["vendor_name"] == "Acme Corp"
    assert ext.raw_extraction["line_items"][0]["unit_price"] == 110.0
    assert ext.normalized_extraction["line_items"][0]["unit_price"] == 110.0
    
    # 5. Rerun reconciliation
    resp = await client.post(f"/api/extraction/{inv_id}/reconcile", json={"allow_review": True})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["overall_status"] == "matched"
    audit_id_2 = data["audit_id"]
    
    assert audit_id_1 != audit_id_2
    
    # The anomaly from audit_id_1 is still REVIEWED. Let's patch it to resolved manually to verify workflow.
    resp = await client.patch(f"/api/reconciliation/anomalies/{anomaly_id}", json={"status": "resolved"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"
    
    # Verify Audit History: Run 1 and Run 2 exist
    audits = (await session.execute(select(Audit).where(Audit.invoice_id == inv_id).order_by(Audit.created_at.asc()))).scalars().all()
    assert len(audits) == 2
    assert str(audits[0].id) == audit_id_1
    assert str(audits[1].id) == audit_id_2

    # Verify Historical ReconciliationResult, Anomaly, and Evidence for Run 1 still exist
    result_1_row = await session.execute(select(ReconciliationResult).where(ReconciliationResult.audit_id == audit_id_1))
    result_1 = result_1_row.scalar_one_or_none()
    assert result_1 is not None

    anomaly_1_row = await session.execute(select(Anomaly).where(Anomaly.id == anomaly_id))
    anomaly_1 = anomaly_1_row.scalar_one_or_none()
    assert anomaly_1 is not None
    assert getattr(anomaly_1.status, "value", anomaly_1.status) == "resolved"

    evidence_1_row = await session.execute(select(Evidence).where(Evidence.anomaly_id == anomaly_id))
    evidence_1 = evidence_1_row.scalars().all()
    assert len(evidence_1) > 0


@pytest.mark.asyncio
async def test_missing_document_evidence(session, seed_data):
    """Test C - Missing document creates evidence with document_id = None."""
    client = _make_client(session)
    inv_id = seed_data["inv_id"]
    doc_id = seed_data["doc_id"]
    
    # Remove document entirely
    await session.execute(select(InvoiceExtraction).where(InvoiceExtraction.invoice_id == inv_id))
    await session.execute(select(InvoiceDocument).where(InvoiceDocument.id == doc_id).execution_options(synchronize_session=False))
    # We must set document_id=None on the extraction and delete the document
    ext_row = await session.execute(select(InvoiceExtraction).where(InvoiceExtraction.invoice_id == inv_id))
    ext = ext_row.scalar_one()
    ext.document_id = None
    
    doc_row = await session.execute(select(InvoiceDocument).where(InvoiceDocument.id == doc_id))
    doc = doc_row.scalar_one()
    await session.delete(doc)
    await session.commit()
    
    resp = await client.post(f"/api/extraction/{inv_id}/reconcile", json={"allow_review": True})
    assert resp.status_code == 200
    
    resp = await client.get(f"/api/reconciliation/{inv_id}")
    anomalies = resp.json()["result"]["anomalies"]
    anomaly_id = anomalies[0]["id"]
    
    resp = await client.get(f"/api/reconciliation/anomalies/{anomaly_id}/evidence")
    assert resp.status_code == 200
    evidence = resp.json()
    assert len(evidence) > 0
    assert evidence[0]["document_id"] is None


@pytest.mark.asyncio
async def test_invalid_anomaly_evidence(session, seed_data):
    """Test 11 - 404 for invalid anomaly ID"""
    client = _make_client(session)
    invalid_id = str(uuid.uuid4())
    resp = await client.get(f"/api/reconciliation/anomalies/{invalid_id}/evidence")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ANOMALY_NOT_FOUND"


@pytest.mark.asyncio
async def test_partial_correction_remains_unresolved(session, seed_data):
    """Test 8 - Partial correction where discrepancy remains."""
    client = _make_client(session)
    inv_id = seed_data["inv_id"]
    
    # 1. Run reconciliation -> Anomaly
    resp = await client.post(f"/api/extraction/{inv_id}/reconcile", json={"allow_review": True})
    assert resp.status_code == 200
    audit_id_1 = resp.json()["audit_id"]
    
    # Get anomaly
    resp = await client.get(f"/api/reconciliation/{inv_id}")
    anomalies = resp.json()["result"]["anomalies"]
    price_anomaly = [a for a in anomalies if a["type"] == "PRICE_MISMATCH"][0]
    anomaly_id = price_anomaly["id"]
    
    # 2. Mark REVIEWED
    await client.patch(f"/api/reconciliation/anomalies/{anomaly_id}", json={"status": "reviewed"})
    
    # 3. Submit PARTIAL correction (Correct to 105 instead of 100)
    correction_payload = {
        "audit_id": audit_id_1,
        "field_path": "line_items[0].unit_price",
        "corrected_value": "105.0",
        "original_value": "110.0"
    }
    await client.post(f"/api/extraction/{inv_id}/corrections", json=correction_payload)
    
    # 5. Rerun reconciliation
    resp = await client.post(f"/api/extraction/{inv_id}/reconcile", json={"allow_review": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_status"] == "anomaly"  # Still an anomaly!
    audit_id_2 = data["audit_id"]
    
    # 6. Verify the new anomaly is OPEN, not resolved
    resp = await client.get(f"/api/reconciliation/{inv_id}")
    new_anomalies = resp.json()["result"]["anomalies"]
    new_price_anomaly = [a for a in new_anomalies if a["type"] == "PRICE_MISMATCH"][0]
    
    assert new_price_anomaly["id"] != anomaly_id
    assert new_price_anomaly["status"] == "open"
    assert new_price_anomaly["actual_value"] == "105.00"
