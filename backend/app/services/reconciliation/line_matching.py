"""
Deterministic line-item matching.

Strategy
────────
Invoice lines are matched to PO lines using two progressively weaker levels:

Level 1 — **exact normalized description**
    ``normalized_description`` fields compared directly (case-insensitive,
    pre-stripped by the ingestion layer).

Level 2 — **relaxed normalization**
    Lowercase, strip ALL punctuation and hyphens, collapse whitespace.
    ``"HP-Laptop 15"`` ≡ ``"HP Laptop 15"``

No embeddings.  No LLM.  If a line cannot be matched at either level,
it is classified as unmatched (either MISSING_ITEM candidate on the PO side
or EXTRA_ITEM candidate on the invoice side).

Each PO item is matched at most once.  Each invoice item is matched at most once.
Greedy: level-1 matches are locked in before level-2 is attempted.
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.services.reconciliation.types import (
    LineMatch,
    LineMatchResult,
    UnmatchedInvoiceItem,
    UnmatchedPOItem,
)


def _exact_normalize(desc: str | None) -> str:
    """Level 1: use the pre-computed normalized_description (lowercased, stripped)."""
    if not desc:
        return ""
    return desc.strip().lower()


def _relaxed_normalize(desc: str | None) -> str:
    """Level 2: aggressive normalization.

    • lowercase
    • replace punctuation / hyphens with space
    • collapse whitespace
    """
    if not desc:
        return ""
    text = desc.strip().lower()
    text = re.sub(r"[^\w\s]", " ", text)   # punctuation → space
    text = re.sub(r"\s+", " ", text)        # collapse spaces
    return text.strip()


def match_lines(invoice_items: list, po_items: list) -> LineMatchResult:
    """Match invoice lines to PO lines deterministically.

    Parameters
    ----------
    invoice_items : list of InvoiceItem ORM objects
    po_items      : list of PurchaseOrderItem ORM objects

    Returns
    -------
    LineMatchResult with matches, unmatched PO items, and unmatched invoice items.
    """
    matches: list[LineMatch] = []
    matched_po_ids: set = set()
    matched_inv_ids: set = set()

    # ── Level 1: exact normalized_description ─────────────────────────
    po_norm_map: dict[str, list] = {}
    for po_item in po_items:
        key = _exact_normalize(po_item.normalized_description or po_item.description)
        po_norm_map.setdefault(key, []).append(po_item)

    for inv_item in invoice_items:
        if inv_item.id in matched_inv_ids:
            continue
        key = _exact_normalize(inv_item.normalized_description or inv_item.description)
        candidates = po_norm_map.get(key, [])
        for po_item in candidates:
            if po_item.id not in matched_po_ids:
                matches.append(LineMatch(
                    invoice_item_id=inv_item.id,
                    po_item_id=po_item.id,
                    invoice_description=inv_item.description,
                    po_description=po_item.description,
                    match_level=1,
                    confidence="exact",
                ))
                matched_po_ids.add(po_item.id)
                matched_inv_ids.add(inv_item.id)
                break

    # ── Level 2: relaxed normalization ────────────────────────────────
    po_relaxed_map: dict[str, list] = {}
    for po_item in po_items:
        if po_item.id in matched_po_ids:
            continue
        key = _relaxed_normalize(po_item.description)
        po_relaxed_map.setdefault(key, []).append(po_item)

    for inv_item in invoice_items:
        if inv_item.id in matched_inv_ids:
            continue
        key = _relaxed_normalize(inv_item.description)
        candidates = po_relaxed_map.get(key, [])
        for po_item in candidates:
            if po_item.id not in matched_po_ids:
                matches.append(LineMatch(
                    invoice_item_id=inv_item.id,
                    po_item_id=po_item.id,
                    invoice_description=inv_item.description,
                    po_description=po_item.description,
                    match_level=2,
                    confidence="relaxed",
                ))
                matched_po_ids.add(po_item.id)
                matched_inv_ids.add(inv_item.id)
                break

    # ── Collect unmatched items ───────────────────────────────────────
    unmatched_po = [
        UnmatchedPOItem(
            po_item_id=p.id,
            description=p.description,
            quantity=Decimal(str(p.quantity)),
            unit_price=Decimal(str(p.unit_price)),
            line_total=Decimal(str(p.line_total)),
        )
        for p in po_items if p.id not in matched_po_ids
    ]

    unmatched_inv = [
        UnmatchedInvoiceItem(
            invoice_item_id=i.id,
            description=i.description,
            quantity=Decimal(str(i.quantity)),
            unit_price=Decimal(str(i.unit_price)),
            line_total=Decimal(str(i.line_total)),
        )
        for i in invoice_items if i.id not in matched_inv_ids
    ]

    return LineMatchResult(
        matches=matches,
        unmatched_po_items=unmatched_po,
        unmatched_invoice_items=unmatched_inv,
    )
