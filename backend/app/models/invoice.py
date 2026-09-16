"""
Invoice models.

An invoice may or may not reference a PO.
Null PO fields are valid and indicate a potential mismatch scenario.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import InvoiceStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invoice_number: Mapped[str] = mapped_column(String(100), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    vendor_tax_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invoice_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # PO linkage — nullable; null means no PO found yet
    po_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id", ondelete="SET NULL"),
        nullable=True,
    )

    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    # Monetary values — Numeric only
    subtotal: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    discount_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    grand_total: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)

    status: Mapped[InvoiceStatus] = mapped_column(
        String(30), nullable=False, default=InvoiceStatus.DRAFT
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Relationships
    purchase_order: Mapped["PurchaseOrder | None"] = relationship(  # noqa: F821
        "PurchaseOrder", back_populates="invoices"
    )
    items: Mapped[list["InvoiceItem"]] = relationship(
        "InvoiceItem", back_populates="invoice", cascade="all, delete-orphan"
    )
    documents: Mapped[list["InvoiceDocument"]] = relationship(
        "InvoiceDocument", back_populates="invoice", cascade="all, delete-orphan"
    )
    extractions: Mapped[list["InvoiceExtraction"]] = relationship(
        "InvoiceExtraction", back_populates="invoice", cascade="all, delete-orphan"
    )
    corrections: Mapped[list["AuditCorrection"]] = relationship(  # noqa: F821
        "AuditCorrection", back_populates="invoice"
    )
    audits: Mapped[list["Audit"]] = relationship(  # noqa: F821
        "Audit", back_populates="invoice"
    )
    ledger_entries: Mapped[list["InvoiceLineLedger"]] = relationship(  # noqa: F821
        "InvoiceLineLedger", back_populates="invoice"
    )

    __table_args__ = (
        # Uniqueness: vendor + invoice_number (two vendors can use same number)
        UniqueConstraint("vendor_name", "invoice_number", name="uq_invoice_vendor_number"),
        CheckConstraint("grand_total >= 0", name="ck_invoice_grand_total_non_negative"),
        Index("ix_invoices_invoice_number", "invoice_number"),
        Index("ix_invoices_vendor_name", "vendor_name"),
        Index("ix_invoices_po_number", "po_number"),
        Index("ix_invoices_purchase_order_id", "purchase_order_id"),
        Index("ix_invoices_status", "status"),
        # Composite index for duplicate detection signals
        Index(
            "ix_invoices_dup_detection",
            "vendor_name",
            "invoice_date",
            "grand_total",
        ),
    )

    def __repr__(self) -> str:
        return f"<Invoice {self.invoice_number} vendor={self.vendor_name}>"


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    quantity: Mapped[float] = mapped_column(Numeric(15, 4), nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    tax_rate_percent: Mapped[float] = mapped_column(
        Numeric(6, 3), nullable=False, default=0
    )
    discount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    line_total: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_text: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="items")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_inv_item_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_inv_item_unit_price_non_negative"),
        Index("ix_invoice_items_invoice_id", "invoice_id"),
        UniqueConstraint("invoice_id", "line_number", name="uq_invoice_item_line"),
    )

    def __repr__(self) -> str:
        return f"<InvoiceItem {self.description} qty={self.quantity}>"


class InvoiceDocument(Base):
    __tablename__ = "invoice_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # SHA-256 hex digest of the file for duplicate detection
    document_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="documents")
    extractions: Mapped[list["InvoiceExtraction"]] = relationship(
        "InvoiceExtraction", back_populates="document"
    )
    evidence: Mapped[list["Evidence"]] = relationship(  # noqa: F821
        "Evidence", back_populates="document"
    )

    __table_args__ = (
        Index("ix_invoice_documents_invoice_id", "invoice_id"),
        Index("ix_invoice_documents_hash", "document_hash"),
    )

    def __repr__(self) -> str:
        return f"<InvoiceDocument {self.original_filename}>"


class InvoiceExtraction(Base):
    """
    Stores raw AI extraction output. Never overwritten after creation.
    Human corrections are stored in AuditCorrection, not here.
    """
    __tablename__ = "invoice_extractions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoice_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # JSONB columns for flexible AI output storage
    from sqlalchemy.dialects.postgresql import JSONB

    raw_extraction: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    normalized_extraction: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    extraction_status: Mapped[str] = mapped_column(String(30), nullable=False)
    overall_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Relationships
    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="extractions")
    document: Mapped["InvoiceDocument | None"] = relationship(
        "InvoiceDocument", back_populates="extractions"
    )

    __table_args__ = (
        Index("ix_invoice_extractions_invoice_id", "invoice_id"),
        Index("ix_invoice_extractions_document_id", "document_id"),
    )

    def __repr__(self) -> str:
        return f"<InvoiceExtraction {self.model_name} status={self.extraction_status}>"
