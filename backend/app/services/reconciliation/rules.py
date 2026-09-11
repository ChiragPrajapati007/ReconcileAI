"""
Deterministic rules for severity classification and vendor normalization.

Severity rules
──────────────
These are deterministic — never LLM-decided.

| Condition                                                    | Severity     |
|--------------------------------------------------------------|--------------|
| Duplicate invoice OR overbilling OR impact ≥ HIGH threshold  | HIGH         |
| Financial impact ≥ MEDIUM threshold                          | MEDIUM       |
| Financial impact > 0  (below MEDIUM threshold)               | LOW          |
| Cannot determine (missing PO, unresolved line)               | NEEDS_REVIEW |

Vendor normalization
────────────────────
Lowercase, strip whitespace, remove common suffixes (Pvt. Ltd., Private
Limited, etc.) so that "Rajan Electronics Pvt. Ltd." matches "rajan electronics".
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.core.config import Settings


# Common Indian business suffixes to strip during normalization
_VENDOR_SUFFIXES = [
    r"\bpvt\.?\s*ltd\.?\b",
    r"\bprivate\s+limited\b",
    r"\blimited\b",
    r"\bltd\.?\b",
    r"\bllp\b",
    r"\binc\.?\b",
    r"\bcorp\.?\b",
    r"\bcorporation\b",
    r"\b&\s*co\.?\b",
]
_VENDOR_SUFFIX_RE = re.compile(
    "|".join(_VENDOR_SUFFIXES), re.IGNORECASE
)


def normalize_vendor(name: str) -> str:
    """Normalize a vendor name for comparison.

    • lowercase
    • strip leading/trailing whitespace
    • remove common business suffixes
    • collapse multiple spaces
    """
    if not name:
        return ""
    text = name.strip().lower()
    text = _VENDOR_SUFFIX_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def classify_severity(
    anomaly_type: str,
    financial_impact: Decimal | None,
    settings: Settings,
) -> str:
    """Return an AnomalySeverity value string based on deterministic rules."""
    # Always-HIGH types regardless of financial impact
    always_high = {"DUPLICATE_INVOICE", "QUANTITY_OVERBILLING", "DOUBLE_BILLING"}
    if anomaly_type in always_high:
        return "HIGH"

    if financial_impact is None:
        return "NEEDS_REVIEW"

    abs_impact = abs(financial_impact)

    if abs_impact >= settings.SEVERITY_HIGH_THRESHOLD:
        return "HIGH"
    if abs_impact >= settings.SEVERITY_MEDIUM_THRESHOLD:
        return "MEDIUM"
    if abs_impact > Decimal("0"):
        return "LOW"

    return "NEEDS_REVIEW"
