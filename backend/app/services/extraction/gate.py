"""
Confidence gate — deterministic tier classification.

Maps a numeric confidence score to a gate status that controls
whether extraction proceeds automatically, requires review,
or is blocked from reconciliation.

Thresholds are configurable via Settings.
"""
from __future__ import annotations

from decimal import Decimal

from app.core.config import settings
from app.services.extraction.schemas import GateStatus


def apply_gate(confidence: float) -> GateStatus:
    """Classify confidence score into a gate tier.

    Thresholds (configurable in Settings):
      HIGH   ≥ 0.85  →  AUTO    (proceed automatically)
      MEDIUM ≥ 0.60  →  REVIEW  (flag for human review)
      LOW    < 0.60  →  BLOCKED (block automatic reconciliation)
    """
    high = float(settings.CONFIDENCE_HIGH_THRESHOLD)
    medium = float(settings.CONFIDENCE_MEDIUM_THRESHOLD)

    if confidence >= high:
        return GateStatus.AUTO
    elif confidence >= medium:
        return GateStatus.REVIEW
    else:
        return GateStatus.BLOCKED
