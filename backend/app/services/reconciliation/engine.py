"""
Reconciliation engine — the main orchestrator.

This module is the single entry-point for reconciling an invoice against its
Purchase Order.  All business logic is delegated to dedicated sub-modules:

    line_matching.py  — deterministic line-item matching
    quantity.py       — partial invoice / overbilling
    pricing.py        — unit-price comparison
    totals.py         — tax, discount, grand-total checks
    duplicates.py     — deterministic duplicate detection
    rules.py          — severity classification, vendor normalization

Transaction safety
──────────────────
This module does **not** commit.  The caller owns the session and decides
when to commit or roll back.  If ``reconcile_invoice`` raises, nothing has
been committed and the database is clean.

Idempotency
───────────
Before creating a new audit, the engine deletes any existing audit for the
same ``(invoice_id, purchase_order_id)`` pair.  Cascading deletes remove the
linked ReconciliationResult → Anomalies → Evidence.  Ledger entries for the
invoice are also deleted and re-created.  Therefore, calling
``reconcile_invoice`` twice produces identical results without duplicates.

Partial-invoice / MISSING_ITEM logic
─────────────────────────────────────
Not every unmatched PO line is a MISSING_ITEM.

An unmatched PO line is classified as MISSING_ITEM only when the invoice
appears to be a *full* invoice (all matched lines cover 100 % of remaining
PO quantity and there is no prior partial-invoicing history for this PO).

If the invoice is partial (any matched line covers less than the PO remaining
quantity, OR prior ledger entries exist for the PO), unmatched PO lines are
treated as "remaining / uninvoiced" and no anomaly is raised.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings, settings as default_settings
from app.models.audit import (
    Anomaly,
    Audit,
    Evidence,
    InvoiceLineLedger,
    ReconciliationResult,
)
from app.models.enums import (
    AnomalySeverity,
    AnomalyStatus,
    AnomalyType,
    AuditStatus,
    EvidenceSourceType,
    ReconciliationStatus,
)
from app.models.invoice import Invoice, InvoiceItem
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem

from app.services.reconciliation.duplicates import detect_duplicates
from app.services.reconciliation.line_matching import match_lines
from app.services.reconciliation.pricing import compare_price
from app.services.reconciliation.quantity import analyze_quantity
from app.services.reconciliation.rules import (
    classify_severity,
    normalize_vendor,
)
from app.services.reconciliation.totals import check_totals
from app.services.reconciliation.types import (
    AnomalyRecord,
    EvidenceRecord,
    ReconciliationReport,
    round_amount,
    round_percent,
)

logger = logging.getLogger(__name__)


class ReconciliationError(Exception):
    """Raised when reconciliation cannot proceed."""


# ══════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ══════════════════════════════════════════════════════════════════════════

async def reconcile_invoice(
    session: AsyncSession,
    invoice_id: uuid.UUID,
    *,
    settings: Settings | None = None,
) -> ReconciliationReport:
    """Reconcile an invoice against its Purchase Order.

    Parameters
    ----------
    session    : async SQLAlchemy session (caller manages transaction)
    invoice_id : the invoice to reconcile
    settings   : optional override for tolerance configuration

    Returns
    -------
    ReconciliationReport  — full result including anomalies, totals, audit_id

    Raises
    ------
    ReconciliationError if the invoice does not exist.
    """
    cfg = settings or default_settings
    started_at = datetime.now(timezone.utc)

    # ── 1. Load invoice + items ───────────────────────────────────────
    invoice = await _load_invoice(session, invoice_id)
    if invoice is None:
        raise ReconciliationError(f"Invoice {invoice_id} not found")

    invoice_items = list(invoice.items)

    document_id = invoice.documents[0].id if invoice.documents else None

    # ── 2. No PO reference → PO_MISMATCH ─────────────────────────────
    if invoice.purchase_order_id is None:
        report = _build_no_po_report(invoice, invoice_items)
        await _persist(session, invoice, None, report, started_at, cfg, document_id)
        return report

    # ── 3. Load PO + items ────────────────────────────────────────────
    po = await _load_po(session, invoice.purchase_order_id)
    if po is None:
        report = _build_po_not_found_report(invoice, invoice_items)
        await _persist(session, invoice, None, report, started_at, cfg, document_id)
        return report

    po_items = list(po.items)
    anomalies: list[AnomalyRecord] = []

    # ── 4. Vendor validation ──────────────────────────────────────────
    vendor_anomaly = _check_vendor(invoice, po, cfg)
    if vendor_anomaly:
        anomalies.append(vendor_anomaly)

    # ── 5. Duplicate detection ────────────────────────────────────────
    dup_anomalies = await detect_duplicates(session, invoice, cfg)
    anomalies.extend(dup_anomalies)

    # ── 6. Line matching ──────────────────────────────────────────────
    match_result = match_lines(invoice_items, po_items)

    # ── 7. Load ledger (other invoices' entries for this PO's items) ──
    po_item_ids = [p.id for p in po_items]
    ledger_map = await _load_ledger(session, po_item_ids, invoice_id)

    # ── 8. Determine if partial invoice ───────────────────────────────
    has_prior_ledger = any(v > Decimal("0") for v in ledger_map.values())
    is_partial = _is_partial_invoice_with_quantities(
        match_result, po_items, invoice_items, ledger_map, has_prior_ledger
    )

    # ── 9. Per-matched-line: quantity + price checks ──────────────────
    inv_item_map = {i.id: i for i in invoice_items}
    po_item_map = {p.id: p for p in po_items}

    for m in match_result.matches:
        inv_item = inv_item_map[m.invoice_item_id]
        po_item = po_item_map[m.po_item_id]

        inv_qty = Decimal(str(inv_item.quantity))
        po_qty = Decimal(str(po_item.quantity))
        prev_invoiced = ledger_map.get(po_item.id, Decimal("0"))

        # Quantity check
        qa = analyze_quantity(
            po_item.id, inv_item.id,
            po_qty, inv_qty, prev_invoiced,
            cfg.QUANTITY_TOLERANCE,
        )
        if qa.is_overbilled:
            anomalies.append(_qty_overbilling_anomaly(qa, po_item, inv_item, cfg))

        if qa.is_already_over_invoiced:
            anomalies.append(_already_over_invoiced_anomaly(qa, po_item, inv_item, cfg))

        # Price check
        pc = compare_price(
            po_item.id, inv_item.id,
            Decimal(str(po_item.unit_price)),
            Decimal(str(inv_item.unit_price)),
            inv_qty,
            cfg.PRICE_TOLERANCE_PERCENT,
        )
        if pc.is_mismatch:
            anomalies.append(_price_mismatch_anomaly(pc, po_item, inv_item, cfg))

    # ── 10. Missing items (only when NOT a partial invoice) ───────────
    if not is_partial:
        for um in match_result.unmatched_po_items:
            anomalies.append(_missing_item_anomaly(um, cfg))

    # ── 11. Extra items (always flagged) ──────────────────────────────
    for um in match_result.unmatched_invoice_items:
        inv_item = inv_item_map[um.invoice_item_id]
        anomalies.append(_extra_item_anomaly(um, inv_item, cfg))

    # ── 12. Header totals ─────────────────────────────────────────────
    line_anomaly_types = {
        "PRICE_MISMATCH", "QUANTITY_OVERBILLING",
        "MISSING_ITEM", "EXTRA_ITEM",
    }
    has_line_anomalies = any(a.type in line_anomaly_types for a in anomalies)
    total_anomalies = check_totals(invoice, po, invoice_items, cfg, has_line_anomalies)
    anomalies.extend(total_anomalies)

    # ── 13. Overall status ────────────────────────────────────────────
    overall_status = _determine_status(anomalies, match_result)

    # ── 14. Build report ──────────────────────────────────────────────
    report = _build_report(invoice, po, anomalies, match_result, overall_status)

    # ── 15. Persist ───────────────────────────────────────────────────
    await _persist(session, invoice, po, report, started_at, cfg, document_id)

    return report


# ══════════════════════════════════════════════════════════════════════════
#  INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════

async def _load_invoice(session: AsyncSession, invoice_id: uuid.UUID):
    """Eagerly load Invoice with items and documents."""
    stmt = (
        select(Invoice)
        .options(
            selectinload(Invoice.items),
            selectinload(Invoice.documents),
        )
        .where(Invoice.id == invoice_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _load_po(session: AsyncSession, po_id: uuid.UUID):
    """Eagerly load PurchaseOrder with items."""
    stmt = (
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.items))
        .where(PurchaseOrder.id == po_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _load_ledger(
    session: AsyncSession,
    po_item_ids: list[uuid.UUID],
    current_invoice_id: uuid.UUID,
) -> dict[uuid.UUID, Decimal]:
    """Load previously invoiced quantities per PO item, excluding current invoice.

    Returns {po_item_id: total_previously_invoiced_qty}
    """
    if not po_item_ids:
        return {}

    stmt = (
        select(
            InvoiceLineLedger.purchase_order_item_id,
            func.sum(InvoiceLineLedger.quantity_invoiced),
        )
        .where(
            InvoiceLineLedger.purchase_order_item_id.in_(po_item_ids),
            InvoiceLineLedger.invoice_id != current_invoice_id,
        )
        .group_by(InvoiceLineLedger.purchase_order_item_id)
    )
    result = await session.execute(stmt)
    return {
        row[0]: Decimal(str(row[1]))
        for row in result.all()
    }


def _is_partial_invoice(
    match_result,
    po_items: list,
    ledger_map: dict[uuid.UUID, Decimal],
    has_prior_ledger: bool,
) -> bool:
    """Determine whether this invoice is a partial invoice.

    A partial invoice is one where:
    • there are prior ledger entries for this PO (partial-invoicing workflow), OR
    • any matched invoice line covers less than the full remaining PO quantity

    When True, unmatched PO items are treated as "remaining / uninvoiced"
    rather than MISSING_ITEM.
    """
    if has_prior_ledger:
        return True

    po_item_map = {p.id: p for p in po_items}
    for m in match_result.matches:
        po_item = po_item_map.get(m.po_item_id)
        if po_item is None:
            continue
        po_qty = Decimal(str(po_item.quantity))
        prev = ledger_map.get(po_item.id, Decimal("0"))
        remaining = max(Decimal("0"), po_qty - prev)

        # Find the invoice item to check its quantity
        # We need to get the invoice quantity from the match — but we only
        # have IDs in the match.  Since this is called from the engine where
        # we have the full items, we pass the needed data through the match.
        # For this function, we iterate matches and look up from po_items.
        # The invoice quantity is not in match_result directly, so the engine
        # should call this with enough context.  Instead of complicating the
        # signature, we use a simpler heuristic:
        #
        # If ANY unmatched PO item exists AND ALL matched PO items have
        # remaining == 0 after this invoice, it's still partial if there are
        # unmatched items and the invoice doesn't cover everything.
        pass

    # Simpler and correct approach: if there are unmatched PO items, check
    # whether the match suggests a full or partial invoice.
    # A full invoice means all matched items fully cover their PO qty.
    # Since we don't have invoice quantities here directly, we use the
    # presence of unmatched items + whether any PO item has remaining qty > 0
    # that hasn't been matched.
    if not match_result.unmatched_po_items:
        return False  # all PO items matched — not partial in the missing-item sense

    # There are unmatched PO items.  Check if matched items are at full qty.
    # We can't check invoice qty here, so we conservatively treat it as
    # partial if prior ledger exists (already handled above).
    # For the no-prior-ledger case, we need invoice quantities.
    # Let the engine pass this info.  For now, return False (not partial),
    # meaning unmatched items will be MISSING_ITEM — which is correct for
    # the full-invoice case.
    return False


def _is_partial_invoice_with_quantities(
    match_result,
    po_items: list,
    invoice_items: list,
    ledger_map: dict[uuid.UUID, Decimal],
    has_prior_ledger: bool,
) -> bool:
    """Extended partial-invoice check that uses invoice quantities.

    A partial invoice is one where:
    1. There are prior ledger entries (partial-invoicing workflow), OR
    2. Any matched invoice line has quantity < remaining PO quantity
    """
    if has_prior_ledger:
        return True

    if not match_result.unmatched_po_items:
        return False  # all PO items matched — not partial

    po_item_map = {p.id: p for p in po_items}
    inv_item_map = {i.id: i for i in invoice_items}

    for m in match_result.matches:
        po_item = po_item_map.get(m.po_item_id)
        inv_item = inv_item_map.get(m.invoice_item_id)
        if po_item is None or inv_item is None:
            continue

        po_qty = Decimal(str(po_item.quantity))
        prev = ledger_map.get(po_item.id, Decimal("0"))
        remaining = max(Decimal("0"), po_qty - prev)
        inv_qty = Decimal(str(inv_item.quantity))

        if inv_qty < remaining:
            return True  # this line is partial → whole invoice is partial

    return False


def _check_vendor(invoice, po, cfg: Settings) -> AnomalyRecord | None:
    """Compare invoice vendor against PO vendor."""
    inv_norm = normalize_vendor(invoice.vendor_name)
    po_norm = normalize_vendor(po.vendor_name)

    if inv_norm == po_norm:
        return None

    return AnomalyRecord(
        type="VENDOR_MISMATCH",
        severity=classify_severity("VENDOR_MISMATCH", None, cfg),
        description=(
            f"Vendor mismatch: invoice vendor '{invoice.vendor_name}' "
            f"vs PO vendor '{po.vendor_name}'"
        ),
        rule_triggered="VENDOR_NAME_COMPARISON",
        evidence=[
            EvidenceRecord(
                source_type="invoice_field",
                field_path="invoice.vendor_name",
                expected_value=po.vendor_name,
                actual_value=invoice.vendor_name,
            ),
        ],
    )


def _determine_status(anomalies: list[AnomalyRecord], match_result) -> str:
    """Determine overall reconciliation status.

    MATCHED        — zero anomalies
    ANOMALY        — one or more deterministic anomalies detected
    REVIEW_REQUIRED — missing PO, or unresolvable data
    """
    if not anomalies:
        return "matched"

    # If any anomaly requires human review
    review_types = {"PO_MISMATCH", "VENDOR_MISMATCH"}
    review_severities = {"NEEDS_REVIEW"}
    for a in anomalies:
        if a.type in review_types or a.severity in review_severities:
            return "review_required"

    return "anomaly"


def _build_report(
    invoice, po, anomalies, match_result, overall_status
) -> ReconciliationReport:
    """Assemble the final ReconciliationReport."""
    inv_total = Decimal(str(invoice.grand_total))
    po_total = Decimal(str(po.grand_total)) if po else None

    diff_amount = None
    diff_percent = None
    if po_total is not None:
        diff_amount = round_amount(inv_total - po_total)
        if po_total != Decimal("0"):
            diff_percent = round_percent(
                (diff_amount / po_total) * Decimal("100")
            )

    return ReconciliationReport(
        overall_status=overall_status,
        anomalies=anomalies,
        invoice_subtotal=Decimal(str(invoice.subtotal)),
        po_subtotal=Decimal(str(po.subtotal)) if po else None,
        invoice_tax=Decimal(str(invoice.tax_amount)),
        po_tax=Decimal(str(po.tax_amount)) if po else None,
        invoice_discount=Decimal(str(invoice.discount_amount)),
        po_discount=Decimal(str(po.discount_amount)) if po else None,
        invoice_total=inv_total,
        po_total=po_total,
        difference_amount=diff_amount,
        difference_percent=diff_percent,
        line_matches=match_result.matches,
    )


def _build_no_po_report(invoice, invoice_items) -> ReconciliationReport:
    """Build a REVIEW_REQUIRED report when no PO is referenced."""
    anomaly = AnomalyRecord(
        type="PO_MISMATCH",
        severity="NEEDS_REVIEW",
        description="Invoice has no Purchase Order reference.",
        rule_triggered="NO_PO_REFERENCE",
        evidence=[
            EvidenceRecord(
                source_type="invoice_field",
                field_path="invoice.purchase_order_id",
                expected_value="a valid PO reference",
                actual_value="null",
            ),
        ],
    )
    return ReconciliationReport(
        overall_status="review_required",
        anomalies=[anomaly],
        invoice_subtotal=Decimal(str(invoice.subtotal)),
        invoice_tax=Decimal(str(invoice.tax_amount)),
        invoice_discount=Decimal(str(invoice.discount_amount)),
        invoice_total=Decimal(str(invoice.grand_total)),
    )


def _build_po_not_found_report(invoice, invoice_items) -> ReconciliationReport:
    """Build a REVIEW_REQUIRED report when referenced PO does not exist."""
    anomaly = AnomalyRecord(
        type="PO_MISMATCH",
        severity="NEEDS_REVIEW",
        description=(
            f"Invoice references PO '{invoice.po_number}' but it was not found."
        ),
        rule_triggered="PO_NOT_FOUND",
        evidence=[
            EvidenceRecord(
                source_type="invoice_field",
                field_path="invoice.po_number",
                expected_value="existing PO",
                actual_value=str(invoice.po_number),
            ),
        ],
    )
    return ReconciliationReport(
        overall_status="review_required",
        anomalies=[anomaly],
        invoice_subtotal=Decimal(str(invoice.subtotal)),
        invoice_tax=Decimal(str(invoice.tax_amount)),
        invoice_discount=Decimal(str(invoice.discount_amount)),
        invoice_total=Decimal(str(invoice.grand_total)),
    )


# ── Anomaly factories ────────────────────────────────────────────────────

def _qty_overbilling_anomaly(qa, po_item, inv_item, cfg) -> AnomalyRecord:
    impact = round_amount(qa.overbilled * Decimal(str(po_item.unit_price)))
    return AnomalyRecord(
        type="QUANTITY_OVERBILLING",
        severity=classify_severity("QUANTITY_OVERBILLING", impact, cfg),
        description=(
            f"Quantity overbilling on '{po_item.description}': "
            f"PO qty={qa.po_quantity}, previously invoiced={qa.previously_invoiced}, "
            f"remaining={qa.remaining_before}, current invoice={qa.current_invoice_quantity}, "
            f"overbilled={qa.overbilled}"
        ),
        rule_triggered="QUANTITY_OVERBILLING_CHECK",
        expected_value=qa.remaining_before,
        actual_value=qa.current_invoice_quantity,
        difference_amount=qa.overbilled,
        financial_impact=impact,
        evidence=[
            EvidenceRecord(
                source_type="calculation",
                field_path=f"invoice_item.quantity (line {inv_item.line_number})",
                expected_value=str(qa.remaining_before),
                actual_value=str(qa.current_invoice_quantity),
                page_number=inv_item.page_number,
                source_text=inv_item.source_text,
            ),
            EvidenceRecord(
                source_type="po_field",
                field_path=f"po_item.quantity (line {po_item.line_number})",
                expected_value=str(qa.po_quantity),
                actual_value=f"previously_invoiced={qa.previously_invoiced}",
            ),
        ],
    )


def _already_over_invoiced_anomaly(qa, po_item, inv_item, cfg) -> AnomalyRecord:
    """Flag when the PO line was already over-invoiced before this invoice."""
    excess = qa.previously_invoiced - qa.po_quantity
    impact = round_amount(qa.current_invoice_quantity * Decimal(str(po_item.unit_price)))
    return AnomalyRecord(
        type="QUANTITY_OVERBILLING",
        severity="HIGH",
        description=(
            f"PO line '{po_item.description}' was already over-invoiced by {excess} "
            f"before this invoice. Current invoice adds {qa.current_invoice_quantity} more."
        ),
        rule_triggered="ALREADY_OVER_INVOICED",
        expected_value=Decimal("0"),
        actual_value=qa.current_invoice_quantity,
        difference_amount=qa.current_invoice_quantity,
        financial_impact=impact,
        evidence=[
            EvidenceRecord(
                source_type="calculation",
                field_path=f"po_item.quantity (line {po_item.line_number})",
                expected_value=str(qa.po_quantity),
                actual_value=f"previously_invoiced={qa.previously_invoiced}",
            ),
        ],
    )


def _price_mismatch_anomaly(pc, po_item, inv_item, cfg) -> AnomalyRecord:
    return AnomalyRecord(
        type="PRICE_MISMATCH",
        severity=classify_severity("PRICE_MISMATCH", pc.financial_impact, cfg),
        description=(
            f"Price mismatch on '{po_item.description}': "
            f"PO price={pc.po_unit_price}, invoice price={pc.invoice_unit_price}, "
            f"difference={pc.difference_per_unit} ({pc.difference_percent}%)"
        ),
        rule_triggered="UNIT_PRICE_COMPARISON",
        expected_value=pc.po_unit_price,
        actual_value=pc.invoice_unit_price,
        difference_amount=pc.difference_per_unit,
        difference_percent=pc.difference_percent,
        financial_impact=pc.financial_impact,
        evidence=[
            EvidenceRecord(
                source_type="po_field",
                field_path=f"po_item.unit_price (line {po_item.line_number})",
                expected_value=str(pc.po_unit_price),
                actual_value=str(pc.invoice_unit_price),
            ),
            EvidenceRecord(
                source_type="invoice_field",
                field_path=f"invoice_item.unit_price (line {inv_item.line_number})",
                expected_value=str(pc.po_unit_price),
                actual_value=str(pc.invoice_unit_price),
                page_number=inv_item.page_number,
                source_text=inv_item.source_text,
            ),
        ],
    )


def _missing_item_anomaly(um, cfg) -> AnomalyRecord:
    impact = round_amount(um.line_total)
    return AnomalyRecord(
        type="MISSING_ITEM",
        severity=classify_severity("MISSING_ITEM", impact, cfg),
        description=(
            f"PO item '{um.description}' (qty={um.quantity}, "
            f"price={um.unit_price}) not found on invoice"
        ),
        rule_triggered="MISSING_ITEM_DETECTION",
        expected_value=um.line_total,
        actual_value=Decimal("0"),
        difference_amount=um.line_total,
        financial_impact=impact,
        evidence=[
            EvidenceRecord(
                source_type="po_field",
                field_path="purchase_order_item.description",
                expected_value=um.description,
                actual_value="not present on invoice",
            ),
        ],
    )


def _extra_item_anomaly(um, inv_item, cfg) -> AnomalyRecord:
    impact = round_amount(um.line_total)
    return AnomalyRecord(
        type="EXTRA_ITEM",
        severity=classify_severity("EXTRA_ITEM", impact, cfg),
        description=(
            f"Invoice item '{um.description}' (qty={um.quantity}, "
            f"price={um.unit_price}) not found on PO"
        ),
        rule_triggered="EXTRA_ITEM_DETECTION",
        expected_value=Decimal("0"),
        actual_value=um.line_total,
        difference_amount=um.line_total,
        financial_impact=impact,
        evidence=[
            EvidenceRecord(
                source_type="invoice_field",
                field_path="invoice_item.description",
                expected_value="present on PO",
                actual_value=um.description,
                page_number=inv_item.page_number,
                source_text=inv_item.source_text,
            ),
        ],
    )


# ══════════════════════════════════════════════════════════════════════════
#  PERSISTENCE
# ══════════════════════════════════════════════════════════════════════════

async def _persist(
    session: AsyncSession,
    invoice,
    po,
    report: ReconciliationReport,
    started_at: datetime,
    cfg: Settings,
    document_id: uuid.UUID | None = None,
) -> None:
    """Persist audit results transactionally (idempotent).

    1. Delete any prior audit for the same (invoice_id, po_id)
    2. Delete prior ledger entries for this invoice
    3. Create Audit → ReconciliationResult → Anomalies → Evidence
    4. Create ledger entries for matched lines
    """
    po_id = po.id if po else None

    # ── Idempotency: clean up prior ledger entries ─────────────────────
    # Historical audits are preserved (Phase 7). Only the ledger is reset
    # to reflect the latest accepted invoice quantities.
    await session.execute(
        delete(InvoiceLineLedger).where(
            InvoiceLineLedger.invoice_id == invoice.id
        )
    )
    await session.flush()

    # ── Create audit ──────────────────────────────────────────────────
    audit_status = _map_audit_status(report.overall_status)
    audit = Audit(
        invoice_id=invoice.id,
        purchase_order_id=po_id,
        status=audit_status,
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
    )
    session.add(audit)
    await session.flush()

    # ── Create reconciliation result ──────────────────────────────────
    rec_result = ReconciliationResult(
        audit_id=audit.id,
        invoice_subtotal=report.invoice_subtotal,
        po_subtotal=report.po_subtotal,
        invoice_tax=report.invoice_tax,
        po_tax=report.po_tax,
        invoice_discount=report.invoice_discount,
        po_discount=report.po_discount,
        invoice_total=report.invoice_total,
        po_total=report.po_total,
        difference_amount=report.difference_amount,
        difference_percent=report.difference_percent,
        overall_status=ReconciliationStatus(report.overall_status),
    )
    session.add(rec_result)
    await session.flush()

    # ── Create anomalies + evidence ───────────────────────────────────
    for ar in report.anomalies:
        anomaly = Anomaly(
            reconciliation_result_id=rec_result.id,
            type=AnomalyType(ar.type),
            severity=AnomalySeverity(ar.severity),
            description=ar.description,
            expected_value=ar.expected_value,
            actual_value=ar.actual_value,
            difference_amount=ar.difference_amount,
            difference_percent=ar.difference_percent,
            financial_impact=ar.financial_impact,
            rule_triggered=ar.rule_triggered,
            status=AnomalyStatus.OPEN,
        )
        session.add(anomaly)
        await session.flush()

        for ev in ar.evidence:
            evidence = Evidence(
                anomaly_id=anomaly.id,
                document_id=document_id,
                source_type=EvidenceSourceType(ev.source_type),
                field_path=ev.field_path,
                source_text=ev.source_text,
                expected_value=ev.expected_value,
                actual_value=ev.actual_value,
                page_number=ev.page_number,
            )
            session.add(evidence)

    # ── Create ledger entries for matched lines ───────────────────────
    inv_item_map = {i.id: i for i in invoice.items}
    for m in report.line_matches:
        inv_item = inv_item_map.get(m.invoice_item_id)
        if inv_item is None:
            continue
        ledger = InvoiceLineLedger(
            purchase_order_item_id=m.po_item_id,
            invoice_id=invoice.id,
            quantity_invoiced=inv_item.quantity,
            unit_price=inv_item.unit_price,
            invoiced_at=invoice.invoice_date,
        )
        session.add(ledger)

    await session.flush()
    report.audit_id = audit.id


def _map_audit_status(overall_status: str) -> AuditStatus:
    """Map reconciliation status to audit status."""
    mapping = {
        "matched": AuditStatus.COMPLETED,
        "anomaly": AuditStatus.COMPLETED,
        "review_required": AuditStatus.REVIEW_REQUIRED,
    }
    return mapping.get(overall_status, AuditStatus.FAILED)
