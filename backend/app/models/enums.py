"""
Enums used across database models.
Defined here to avoid circular imports and to ensure
they are imported before models register them with SQLAlchemy.
"""
import enum


class POStatus(str, enum.Enum):
    OPEN = "open"
    PARTIALLY_INVOICED = "partially_invoiced"
    FULLY_INVOICED = "fully_invoiced"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    UNDER_AUDIT = "under_audit"
    APPROVED = "approved"
    REJECTED = "rejected"


class AuditStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    REVIEW_REQUIRED = "review_required"
    COMPLETED = "completed"
    FAILED = "failed"


class ExtractionStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ReconciliationStatus(str, enum.Enum):
    MATCHED = "matched"
    ANOMALY = "anomaly"
    REVIEW_REQUIRED = "review_required"


class AnomalyType(str, enum.Enum):
    PRICE_MISMATCH = "PRICE_MISMATCH"
    QUANTITY_OVERBILLING = "QUANTITY_OVERBILLING"
    MISSING_ITEM = "MISSING_ITEM"
    EXTRA_ITEM = "EXTRA_ITEM"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"
    TAX_MISMATCH = "TAX_MISMATCH"
    DISCOUNT_MISMATCH = "DISCOUNT_MISMATCH"
    VENDOR_MISMATCH = "VENDOR_MISMATCH"
    PO_MISMATCH = "PO_MISMATCH"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
    DOUBLE_BILLING = "DOUBLE_BILLING"


class AnomalySeverity(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class AnomalyStatus(str, enum.Enum):
    OPEN = "open"
    REVIEWED = "reviewed"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    WAIVED = "waived"


class EvidenceSourceType(str, enum.Enum):
    INVOICE_FIELD = "invoice_field"
    PO_FIELD = "po_field"
    CALCULATION = "calculation"
    DOCUMENT_TEXT = "document_text"
