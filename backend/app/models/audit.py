"""
Audit, Reconciliation, Anomaly, Evidence, Correction, and Ledger models.
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
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    AnomalySeverity,
    AnomalyStatus,
    AnomalyType,
    AuditStatus,
    EvidenceSourceType,
    ReconciliationStatus,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Audit(Base):
    """One reconciliation execution of an invoice against a PO."""
    __tablename__ = "audits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[AuditStatus] = mapped_column(
        String(30), nullable=False, default=AuditStatus.PENDING
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Relationships
    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="audits")  # noqa: F821
    purchase_order: Mapped["PurchaseOrder | None"] = relationship(  # noqa: F821
        "PurchaseOrder", back_populates="audits"
    )
    reconciliation_result: Mapped["ReconciliationResult | None"] = relationship(
        "ReconciliationResult", back_populates="audit", uselist=False,
        cascade="all, delete-orphan"
    )
    corrections: Mapped[list["AuditCorrection"]] = relationship(
        "AuditCorrection", back_populates="audit", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_audits_invoice_id", "invoice_id"),
        Index("ix_audits_purchase_order_id", "purchase_order_id"),
        Index("ix_audits_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<Audit {self.id} status={self.status}>"


class ReconciliationResult(Base):
    """
    Deterministic comparison result for one audit execution.
    All arithmetic is performed by application code, never by the LLM.
    """
    __tablename__ = "reconciliation_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # One result per audit
    )

    # All monetary comparisons — Numeric only
    invoice_subtotal: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    po_subtotal: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)

    invoice_tax: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    po_tax: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)

    invoice_discount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    po_discount: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)

    invoice_total: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    po_total: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)

    difference_amount: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    difference_percent: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)

    overall_status: Mapped[ReconciliationStatus] = mapped_column(
        String(30), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    audit: Mapped["Audit"] = relationship("Audit", back_populates="reconciliation_result")
    anomalies: Mapped[list["Anomaly"]] = relationship(
        "Anomaly", back_populates="reconciliation_result", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_reconciliation_results_audit_id", "audit_id"),
        Index("ix_reconciliation_results_status", "overall_status"),
    )

    def __repr__(self) -> str:
        return f"<ReconciliationResult audit={self.audit_id} status={self.overall_status}>"


class Anomaly(Base):
    """
    A single detected discrepancy in the reconciliation.
    Severity and classification are determined by deterministic rules, not the LLM.
    """
    __tablename__ = "anomalies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reconciliation_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reconciliation_results.id", ondelete="CASCADE"),
        nullable=False,
    )

    type: Mapped[AnomalyType] = mapped_column(String(50), nullable=False)
    severity: Mapped[AnomalySeverity] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # All comparison values — Numeric only
    expected_value: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    actual_value: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    difference_amount: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    difference_percent: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    financial_impact: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)

    rule_triggered: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[AnomalyStatus] = mapped_column(
        String(30), nullable=False, default=AnomalyStatus.OPEN
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    reconciliation_result: Mapped["ReconciliationResult"] = relationship(
        "ReconciliationResult", back_populates="anomalies"
    )
    evidence: Mapped[list["Evidence"]] = relationship(
        "Evidence", back_populates="anomaly", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_anomalies_reconciliation_result_id", "reconciliation_result_id"),
        Index("ix_anomalies_type", "type"),
        Index("ix_anomalies_severity", "severity"),
        Index("ix_anomalies_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<Anomaly {self.type} severity={self.severity}>"


class Evidence(Base):
    """
    Traceability record linking an anomaly back to its source in the document and PO.
    """
    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    anomaly_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("anomalies.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoice_documents.id", ondelete="SET NULL"),
        nullable=True,
    )

    source_type: Mapped[EvidenceSourceType] = mapped_column(String(50), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    field_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    expected_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    actual_value: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    anomaly: Mapped["Anomaly"] = relationship("Anomaly", back_populates="evidence")
    document: Mapped["InvoiceDocument | None"] = relationship(  # noqa: F821
        "InvoiceDocument", back_populates="evidence"
    )

    __table_args__ = (
        Index("ix_evidence_anomaly_id", "anomaly_id"),
        Index("ix_evidence_document_id", "document_id"),
    )

    def __repr__(self) -> str:
        return f"<Evidence {self.source_type} field={self.field_path}>"


class AuditCorrection(Base):
    """
    Human review correction record.
    Original AI-extracted value is always preserved. Never overwritten.
    """
    __tablename__ = "audit_corrections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        nullable=False,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Dot-path to the corrected field, e.g. "items[2].unit_price"
    field_path: Mapped[str] = mapped_column(String(300), nullable=False)
    original_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_value: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_by: Mapped[str | None] = mapped_column(String(200), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    audit: Mapped["Audit"] = relationship("Audit", back_populates="corrections")
    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="corrections")  # noqa: F821

    __table_args__ = (
        Index("ix_audit_corrections_audit_id", "audit_id"),
        Index("ix_audit_corrections_invoice_id", "invoice_id"),
    )

    def __repr__(self) -> str:
        return f"<AuditCorrection {self.field_path}>"


class InvoiceLineLedger(Base):
    """
    Tracks quantity invoiced per PO line across multiple invoices.

    This is the source of truth for partial-invoice calculations:
      previously_invoiced_qty = SUM(quantity_invoiced WHERE invoice_id != current)
      remaining = po_item.quantity - previously_invoiced_qty
      overbilled = max(0, current_qty - remaining)
    """
    __tablename__ = "invoice_line_ledger"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    purchase_order_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_order_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )
    quantity_invoiced: Mapped[float] = mapped_column(Numeric(15, 4), nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    invoiced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    purchase_order_item: Mapped["PurchaseOrderItem"] = relationship(  # noqa: F821
        "PurchaseOrderItem", back_populates="ledger_entries"
    )
    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="ledger_entries")  # noqa: F821

    __table_args__ = (
        CheckConstraint(
            "quantity_invoiced > 0", name="ck_ledger_quantity_positive"
        ),
        Index("ix_ledger_po_item_id", "purchase_order_item_id"),
        Index("ix_ledger_invoice_id", "invoice_id"),
        # Critical: for fast remaining-quantity queries per PO line
        Index("ix_ledger_po_item_invoice", "purchase_order_item_id", "invoice_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<InvoiceLineLedger po_item={self.purchase_order_item_id} "
            f"invoice={self.invoice_id} qty={self.quantity_invoiced}>"
        )
