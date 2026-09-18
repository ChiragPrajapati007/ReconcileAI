"""
Extraction API endpoints.

POST /api/extraction/{invoice_id}/upload       — upload document + run extraction
GET  /api/extraction/{invoice_id}              — retrieve latest extraction result
POST /api/extraction/{invoice_id}/reconcile   — populate invoice from extraction + reconcile
POST /api/extraction/{invoice_id}/corrections — record a human field correction
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.db.session import get_db
from app.models.audit import Audit
from app.models.invoice import Invoice, InvoiceDocument, InvoiceExtraction
from app.schemas.extraction import (
    CanonicalExtractionOut,
    CanonicalLineItemOut,
    CorrectionOut,
    CorrectionRequest,
    ExtractionDetailOut,
    ExtractionUploadResponse,
    ExtractionIngestResponse,
    PopulationResultOut,
    RawExtractionOut,
    RawLineItemOut,
    ReconcileFromExtractionRequest,
    ReconcileFromExtractionResponse,
    SourceReferenceOut,
    ValidationIssueOut,
)
from app.services.extraction.correction import apply_correction
from app.services.extraction.ingestion import (
    IngestionError,
    IngestedDocument,
    save_document,
    validate_file,
)
from app.services.extraction.pipeline import run_extraction_pipeline, ExtractionPipelineError
from app.services.extraction.populator import (
    GateBlockedError,
    ExtractionNotFoundError,
    populate_invoice_from_extraction,
)
from app.services.extraction.schemas import ExtractionResult, GateStatus
from app.services.reconciliation.engine import reconcile_invoice, ReconciliationError

router = APIRouter()


@router.post(
    "/ingest",
    response_model=ExtractionIngestResponse,
    summary="Autonomous end-to-end invoice ingestion",
)
async def ingest_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> ExtractionIngestResponse:
    """
    Ingest a new document, create a provisional invoice, run extraction,
    and automatically reconcile if confidence is high enough.
    Returns the new invoice ID for frontend navigation.
    """
    # 1. Validate file
    content = await file.read()
    try:
        validate_file(file.filename or "unknown", content, file.content_type or "application/pdf")
        ingested = save_document(content, file.filename or "unknown", file.content_type or "application/pdf")
    except IngestionError as e:
        raise AppError(status_code=400, code="INVALID_FILE", message=str(e))

    # 3.5 Check for duplicate document hash (Rule A)
    dup_doc = await db.execute(
        select(InvoiceDocument).where(InvoiceDocument.document_hash == ingested.document_hash)
    )
    if dup_doc.scalars().first() is not None:
        raise AppError(
            status_code=409, 
            code="DUPLICATE_DOCUMENT", 
            message="An identical document has already been uploaded."
        )

    # 4. Create Provisional Invoice
    invoice_id = uuid.uuid4()
    provisional_invoice = Invoice(
        id=invoice_id,
        invoice_number=f"PROV-{invoice_id.hex[:8].upper()}",
        vendor_name="PROVISIONAL INVOICE",
        # Use a timezone-aware UTC datetime for dummy date
        invoice_date=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        status=__import__("app.models.enums", fromlist=["InvoiceStatus"]).InvoiceStatus.DRAFT,
    )
    db.add(provisional_invoice)
    await db.flush()

    # 5. Create Document Record
    doc = InvoiceDocument(
        invoice_id=invoice_id,
        filename=ingested.filename,
        original_filename=ingested.original_filename,
        mime_type=ingested.mime_type,
        file_size=ingested.file_size,
        storage_path=ingested.storage_path,
        document_hash=ingested.document_hash,
    )
    db.add(doc)
    await db.flush()
    doc_id = doc.id
    
    # COMMIT 1: Save provisional invoice and document safely
    await db.commit()

    provider = _get_provider()
    if provider is None:
        raise AppError(
            status_code=503,
            code="PROVIDER_NOT_CONFIGURED",
            message="No extraction provider configured. Set GEMINI_API_KEY in .env.",
        )

    # 6. Run Extraction
    try:
        await run_extraction_pipeline(
            db=db,
            invoice_id=invoice_id,
            document_id=doc.id,
            ingested=ingested,
            file_content=content,
            provider=provider,
        )
    except ExtractionPipelineError as e:
        await db.rollback()
        # Return a meaningful error but keep the invoice in DRAFT so user can retry/recover
        raise AppError(status_code=500, code="EXTRACTION_FAILED", message=f"Extraction pipeline error: {str(e)}")

    # COMMIT 2: Persist extraction results (success or failure from provider)
    await db.commit()

    # 7. Populate and Reconcile if eligible
    gate_status_str = "blocked"
    reconciled = False
    
    try:
        pop_result = await populate_invoice_from_extraction(db, invoice_id)
        gate_status_str = pop_result.gate_status.value
        
        if pop_result.eligible:
            # High confidence, try to reconcile
            await reconcile_invoice(db, invoice_id)
            reconciled = True
            
        # COMMIT 3: Persist population & reconciliation changes
        await db.commit()
    except GateBlockedError as e:
        await db.rollback()
        gate_status_str = e.gate_status.value
        # Safe to ignore, remains provisional / DRAFT for review
    except Exception as e:
        # Some other failure during population or reconciliation
        # CRITICAL: Rollback so partial reconciliation anomalies/ledgers aren't persisted
        await db.rollback()
        import logging
        logging.getLogger(__name__).exception("Failed to populate/reconcile after ingestion")

    return ExtractionIngestResponse(
        invoice_id=invoice_id,
        document_id=doc_id,
        gate_status=gate_status_str,
        reconciled=reconciled,
    )


def _get_provider():
    """Lazily create the extraction provider.

    Returns None if GEMINI_API_KEY is not configured (tests use mock).
    """
    from app.core.config import settings
    if not settings.GEMINI_API_KEY:
        return None
    from app.services.extraction.gemini_provider import GeminiProvider
    return GeminiProvider()


def _build_upload_response(
    document_id: uuid.UUID,
    result: ExtractionResult,
    extraction_id: str | None = None,
) -> ExtractionUploadResponse:
    """Convert internal ExtractionResult to API response."""
    # Raw extraction
    raw_out = RawExtractionOut(
        vendor_name=result.raw.vendor_name,
        invoice_number=result.raw.invoice_number,
        invoice_date=result.raw.invoice_date,
        due_date=result.raw.due_date,
        currency=result.raw.currency,
        po_number=result.raw.po_number,
        subtotal=result.raw.subtotal,
        tax_amount=result.raw.tax_amount,
        discount_amount=result.raw.discount_amount,
        grand_total=result.raw.grand_total,
        line_items=[
            RawLineItemOut(**item.model_dump())
            for item in result.raw.line_items
        ],
        extraction_notes=result.raw.extraction_notes,
    )

    # Canonical extraction
    canonical_out = None
    if result.canonical:
        c = result.canonical
        canonical_out = CanonicalExtractionOut(
            vendor_name=c.vendor_name,
            invoice_number=c.invoice_number,
            invoice_date=c.invoice_date,
            due_date=c.due_date,
            currency=c.currency,
            po_number=c.po_number,
            subtotal=str(c.subtotal),
            tax_amount=str(c.tax_amount),
            discount_amount=str(c.discount_amount),
            grand_total=str(c.grand_total),
            line_items=[
                CanonicalLineItemOut(
                    line_number=li.line_number,
                    description=li.description,
                    quantity=str(li.quantity),
                    unit_price=str(li.unit_price),
                    line_total=str(li.line_total),
                    tax_rate_percent=str(li.tax_rate_percent),
                    discount=str(li.discount),
                    page_number=li.page_number,
                    source_text=li.source_text,
                )
                for li in c.line_items
            ],
        )

    return ExtractionUploadResponse(
        document_id=str(document_id),
        extraction_id=extraction_id,
        extraction_status=result.extraction_status,
        confidence=result.confidence,
        gate_status=result.gate_status.value,
        raw_extraction=raw_out,
        canonical_extraction=canonical_out,
        validation_issues=[
            ValidationIssueOut(
                field=vi.field,
                issue_type=vi.issue_type,
                message=vi.message,
                severity=vi.severity.value,
            )
            for vi in result.validation_issues
        ],
        source_references=[
            SourceReferenceOut(
                field=sr.field,
                page_number=sr.page_number,
                source_text=sr.source_text,
                extraction_method=sr.extraction_method,
            )
            for sr in result.source_references
        ],
        provider_name=result.provider_name,
        model_name=result.model_name,
    )


@router.post(
    "/{invoice_id}/upload",
    response_model=ExtractionUploadResponse,
    summary="Upload a document and extract financial data",
)
async def upload_and_extract(
    invoice_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> ExtractionUploadResponse:
    """Upload an invoice/receipt document and run the extraction pipeline.

    Accepts PDF, PNG, JPEG, TIFF, WebP files up to 20 MB.
    Returns structured extraction with confidence and validation results.
    """
    # Verify invoice exists
    inv_result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    if inv_result.scalar_one_or_none() is None:
        raise AppError(
            status_code=404,
            code="INVOICE_NOT_FOUND",
            message=f"Invoice {invoice_id} not found.",
        )

    # Read file content
    content = await file.read()

    # Validate
    try:
        mime_type = validate_file(content, file.filename or "unknown", file.content_type)
    except IngestionError as exc:
        raise AppError(status_code=400, code="INVALID_FILE", message=str(exc))

    # Save document to filesystem
    ingested = save_document(content, file.filename or "unknown", mime_type)

    # Duplicate check for the new file (exclude this invoice's current docs to allow retrying)
    dup_doc = await db.execute(
        select(InvoiceDocument).where(
            InvoiceDocument.document_hash == ingested.document_hash,
            InvoiceDocument.invoice_id != invoice_id
        )
    )
    if dup_doc.scalars().first() is not None:
        raise AppError(
            status_code=409, 
            code="DUPLICATE_DOCUMENT", 
            message="An identical document has already been uploaded for another invoice."
        )

    # Persist InvoiceDocument record
    doc = InvoiceDocument(
        invoice_id=invoice_id,
        filename=ingested.filename,
        original_filename=ingested.original_filename,
        mime_type=ingested.mime_type,
        file_size=ingested.file_size,
        storage_path=ingested.storage_path,
        document_hash=ingested.document_hash,
    )
    db.add(doc)
    await db.flush()
    doc_id = doc.id
    # COMMIT 1: Save document safely before extraction
    await db.commit()

    # Get provider
    provider = _get_provider()
    if provider is None:
        raise AppError(
            status_code=503,
            code="PROVIDER_NOT_CONFIGURED",
            message="No extraction provider configured. Set GEMINI_API_KEY in .env.",
        )

    # Run extraction pipeline
    try:
        result = await run_extraction_pipeline(
            db=db,
            invoice_id=invoice_id,
            document_id=doc_id,
            ingested=ingested,
            file_content=content,
            provider=provider,
        )
        # COMMIT 2: Save extraction results
        await db.commit()
    except ExtractionPipelineError as e:
        await db.rollback()
        raise AppError(status_code=500, code="EXTRACTION_FAILED", message=f"Extraction pipeline error: {str(e)}")

    return _build_upload_response(doc_id, result)


@router.get(
    "/{invoice_id}",
    response_model=ExtractionDetailOut,
    summary="Get the latest extraction result for an invoice",
)
async def get_extraction(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ExtractionDetailOut:
    """Retrieve the most recent extraction result for an invoice."""
    result = await db.execute(
        select(InvoiceExtraction)
        .where(InvoiceExtraction.invoice_id == invoice_id)
        .order_by(InvoiceExtraction.created_at.desc())
    )
    extraction = result.scalars().first()

    if extraction is None:
        raise AppError(
            status_code=404,
            code="EXTRACTION_NOT_FOUND",
            message=f"No extraction result found for invoice {invoice_id}.",
        )

    return ExtractionDetailOut(
        extraction_id=str(extraction.id),
        document_id=str(extraction.document_id) if extraction.document_id else None,
        extraction_status=extraction.extraction_status,
        confidence=float(extraction.overall_confidence) if extraction.overall_confidence else None,
        provider_name=extraction.model_provider,
        model_name=extraction.model_name,
        raw_extraction=extraction.raw_extraction,
        normalized_extraction=extraction.normalized_extraction,
        created_at=extraction.created_at,
    )


@router.post(
    "/{invoice_id}/reconcile",
    response_model=ReconcileFromExtractionResponse,
    summary="Populate invoice from extraction then run deterministic reconciliation",
)
async def reconcile_from_extraction(
    invoice_id: uuid.UUID,
    body: ReconcileFromExtractionRequest = ReconcileFromExtractionRequest(),
    db: AsyncSession = Depends(get_db),
) -> ReconcileFromExtractionResponse:
    """Gate-checked extraction → Invoice population → Phase 3 reconciliation.

    Flow:
      1. Load latest InvoiceExtraction for the invoice.
      2. Check confidence gate:
         - AUTO  → proceed automatically.
         - REVIEW → blocked unless allow_review=True (caller must have applied corrections).
         - BLOCKED → HTTP 422, explain why.
      3. Write canonical data into Invoice + InvoiceItem rows.
      4. Link PO via extracted po_number.
      5. Run the existing Phase 3 deterministic reconciliation engine.
      6. Return population summary + reconciliation status.
    """
    try:
        pop_result = await populate_invoice_from_extraction(
            db,
            invoice_id,
            allow_review=body.allow_review,
        )
    except ExtractionNotFoundError as exc:
        raise AppError(status_code=404, code="EXTRACTION_NOT_FOUND", message=str(exc))
    except GateBlockedError as exc:
        raise AppError(
            status_code=422,
            code="EXTRACTION_BLOCKED",
            message=exc.reason,
        )

    pop_out = PopulationResultOut(
        extraction_id=str(pop_result.extraction_id),
        gate_status=pop_result.gate_status.value,
        eligible=pop_result.eligible,
        block_reason=pop_result.block_reason,
        po_found=pop_result.po_found,
        po_id=str(pop_result.po_id) if pop_result.po_id else None,
        line_items_written=pop_result.line_items_written,
    )

    # If not eligible (REVIEW gate without allow_review), return early
    if not pop_result.eligible:
        return ReconcileFromExtractionResponse(
            invoice_id=str(invoice_id),
            population=pop_out,
        )

    # Run the Phase 3 engine
    try:
        report = await reconcile_invoice(db, invoice_id)
        await db.commit()
    except ReconciliationError as exc:
        await db.rollback()
        raise AppError(status_code=422, code="RECONCILIATION_ERROR", message=str(exc))
    except Exception:
        await db.rollback()
        raise

    # Load the created audit to get its id
    audit_result = await db.execute(
        select(Audit)
        .where(Audit.invoice_id == invoice_id)
        .order_by(Audit.created_at.desc())
    )
    audit = audit_result.scalars().first()

    return ReconcileFromExtractionResponse(
        invoice_id=str(invoice_id),
        population=pop_out,
        audit_id=str(audit.id) if audit else None,
        overall_status=report.overall_status,
        anomaly_count=len(report.anomalies),
    )


@router.post(
    "/{invoice_id}/corrections",
    response_model=CorrectionOut,
    status_code=201,
    summary="Record a human correction to an extracted field",
)
async def record_correction(
    invoice_id: uuid.UUID,
    body: CorrectionRequest,
    db: AsyncSession = Depends(get_db),
) -> CorrectionOut:
    """Store a human field correction without modifying the original AI extraction.

    The original raw_extraction and normalized_extraction in InvoiceExtraction
    are NEVER modified. Corrections are stored in AuditCorrection and merged
    at reconciliation time via get_corrected_canonical().
    """
    # Verify audit belongs to this invoice
    audit_id = uuid.UUID(body.audit_id)
    audit_row = await db.execute(
        select(Audit).where(
            Audit.id == audit_id,
            Audit.invoice_id == invoice_id,
        )
    )
    if audit_row.scalar_one_or_none() is None:
        raise AppError(
            status_code=404,
            code="AUDIT_NOT_FOUND",
            message=f"Audit {body.audit_id} not found for invoice {invoice_id}.",
        )

    correction = await apply_correction(
        db,
        audit_id=audit_id,
        invoice_id=invoice_id,
        field_path=body.field_path,
        corrected_value=body.corrected_value,
        original_value=body.original_value,
        reason=body.reason,
        corrected_by=body.corrected_by,
    )
    await db.commit()

    return CorrectionOut(
        id=str(correction.id),
        audit_id=str(correction.audit_id),
        invoice_id=str(correction.invoice_id),
        field_path=correction.field_path,
        original_value=correction.original_value,
        corrected_value=correction.corrected_value,
        reason=correction.reason,
        corrected_by=correction.corrected_by,
        created_at=correction.created_at,
    )
