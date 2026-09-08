"""
Purchase Order models.

IMPORTANT: All monetary values use Numeric(15, 2) — never Float.
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
from app.models.enums import POStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    po_number: Mapped[str] = mapped_column(String(100), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    vendor_tax_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    po_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    # Monetary values — Numeric only, never float
    subtotal: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    discount_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)
    grand_total: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False, default=0)

    status: Mapped[POStatus] = mapped_column(
        String(30), nullable=False, default=POStatus.OPEN
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Relationships
    items: Mapped[list["PurchaseOrderItem"]] = relationship(
        "PurchaseOrderItem", back_populates="purchase_order", cascade="all, delete-orphan"
    )
    invoices: Mapped[list["Invoice"]] = relationship(  # noqa: F821
        "Invoice", back_populates="purchase_order"
    )
    audits: Mapped[list["Audit"]] = relationship(  # noqa: F821
        "Audit", back_populates="purchase_order"
    )

    __table_args__ = (
        # po_number is unique per vendor (different vendors can reuse numbers)
        UniqueConstraint("po_number", "vendor_name", name="uq_po_number_vendor"),
        CheckConstraint("grand_total >= 0", name="ck_po_grand_total_non_negative"),
        Index("ix_purchase_orders_po_number", "po_number"),
        Index("ix_purchase_orders_vendor_name", "vendor_name"),
        Index("ix_purchase_orders_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<PurchaseOrder {self.po_number} vendor={self.vendor_name}>"


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("purchase_orders.id", ondelete="CASCADE"),
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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # Relationships
    purchase_order: Mapped["PurchaseOrder"] = relationship(
        "PurchaseOrder", back_populates="items"
    )
    ledger_entries: Mapped[list["InvoiceLineLedger"]] = relationship(  # noqa: F821
        "InvoiceLineLedger", back_populates="purchase_order_item"
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_po_item_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_po_item_unit_price_non_negative"),
        Index("ix_po_items_po_id", "purchase_order_id"),
        UniqueConstraint("purchase_order_id", "line_number", name="uq_po_item_line"),
    )

    def __repr__(self) -> str:
        return f"<PurchaseOrderItem {self.description} qty={self.quantity}>"
