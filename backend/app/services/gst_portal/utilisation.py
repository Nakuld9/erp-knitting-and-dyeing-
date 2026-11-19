"""GST credit cross-utilisation — policy-versioned and CA-gated (Stage 6F-P5 GST).

Utilisation follows a CA-approved, versioned policy (the legal set-off order is
configurable, never hardcoded). Prohibited CGST<->SGST cross-utilisation is rejected
unless a policy version explicitly permits it. A proposal is previewed, then
CA-approved, then — only when explicitly authorised — posted as ONE balanced voucher
through the EXISTING posting engine (Dr output-liability / Cr input-credit). Nothing
posts automatically; a mere accounting journal transfer is not treated as valid
statutory utilisation.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.gst_itc import CrossAdjStatus, GstCrossAdjustment, GstPolicyVersion, PolicyStatus
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_rules import resolve_account
from app.services.audit import record_audit
from app.services.gst_portal.common import MZERO, m, now, period_valid

HEADS = ("IGST", "CGST", "SGST", "CESS")
DEFAULT_RULES = {
    "utilisation_order": {
        "IGST": ["IGST", "CGST", "SGST"],
        "CGST": ["CGST", "IGST"],
        "SGST": ["SGST", "IGST"],
        "CESS": ["CESS"],
    },
    "allow_cgst_sgst_cross": False,
    "auto_claim": False,
}


class GstUtilisationError(ValueError):
    """An invalid utilisation / policy request."""


class ProhibitedUtilisation(GstUtilisationError):
    """A utilisation entry that is not legally permitted (e.g. CGST<->SGST cross)."""


class UtilisationPostingBlocked(GstUtilisationError):
    """The hard posting gate for a cross-adjustment is not satisfied."""


# -------------------------------- policy ------------------------------------

def create_policy(db: Session, company_id: uuid.UUID, *, rules: dict | None = None,
                  title: str | None = None, effective_from: date | None = None,
                  actor=None) -> GstPolicyVersion:
    last = db.execute(select(GstPolicyVersion).where(
        GstPolicyVersion.company_id == company_id)
        .order_by(GstPolicyVersion.version_no.desc())).scalars().first()
    version_no = (last.version_no + 1) if last else 1
    pol = GstPolicyVersion(
        company_id=company_id, version_no=version_no, effective_from=effective_from,
        rules=json.dumps(rules or DEFAULT_RULES), status=PolicyStatus.DRAFT.value,
        title=title, created_by=actor, updated_by=actor)
    db.add(pol)
    db.flush()
    record_audit(db, action="GST_POLICY_CREATED", entity="gst_policy_version",
                 entity_id=str(pol.id), actor_id=actor,
                 detail={"company_id": str(company_id), "version": version_no})
    return pol


def approve_policy(db: Session, company_id: uuid.UUID, policy_id: uuid.UUID, *,
                   approver_name: str, actor=None) -> GstPolicyVersion:
    pol = db.get(GstPolicyVersion, policy_id)
    if pol is None or pol.company_id != company_id:
        raise GstUtilisationError("policy not found")
    # Supersede any previously approved version so exactly one is current.
    for other in db.execute(select(GstPolicyVersion).where(
            GstPolicyVersion.company_id == company_id,
            GstPolicyVersion.status == PolicyStatus.APPROVED.value)).scalars().all():
        other.status = PolicyStatus.SUPERSEDED.value
    pol.status = PolicyStatus.APPROVED.value
    pol.approved_by = actor
    pol.approver_name = approver_name
    pol.approved_at = now()
    db.flush()
    record_audit(db, action="GST_POLICY_APPROVED", entity="gst_policy_version",
                 entity_id=str(pol.id), actor_id=actor,
                 detail={"company_id": str(company_id), "version": pol.version_no,
                         "approver": approver_name})
    return pol


def active_policy(db: Session, company_id: uuid.UUID) -> GstPolicyVersion | None:
    return db.execute(select(GstPolicyVersion).where(
        GstPolicyVersion.company_id == company_id,
        GstPolicyVersion.status == PolicyStatus.APPROVED.value)
        .order_by(GstPolicyVersion.version_no.desc())).scalars().first()


def _rules_of(pol: GstPolicyVersion) -> dict:
    try:
        return json.loads(pol.rules) if pol and pol.rules else DEFAULT_RULES
    except Exception:  # noqa: BLE001
        return DEFAULT_RULES


# ----------------------------- computation ----------------------------------

def compute_utilisation(available: dict, liability: dict, rules: dict) -> dict:
    """Compute the legal set-off following ``rules['utilisation_order']``; never
    produces a prohibited CGST<->SGST entry unless the rules allow it."""
    order = rules.get("utilisation_order", DEFAULT_RULES["utilisation_order"])
    allow_cross = bool(rules.get("allow_cgst_sgst_cross", False))
    avail = {h: m(available.get(h, 0)) for h in HEADS}
    liab = {h: m(liability.get(h, 0)) for h in HEADS}
    entries = []
    for credit_head in HEADS:
        for liab_head in order.get(credit_head, [credit_head]):
            if avail[credit_head] <= MZERO:
                break
            if liab.get(liab_head, MZERO) <= MZERO:
                continue
            if {credit_head, liab_head} == {"CGST", "SGST"} and not allow_cross:
                continue
            use = min(avail[credit_head], liab[liab_head])
            if use > MZERO:
                entries.append({"credit_head": credit_head, "liability_head": liab_head,
                                "amount": str(use)})
                avail[credit_head] = quantize(avail[credit_head] - use, MONEY_SCALE)
                liab[liab_head] = quantize(liab[liab_head] - use, MONEY_SCALE)
    return {
        "entries": entries,
        "available_input": {h: str(m(available.get(h, 0))) for h in HEADS},
        "liability": {h: str(m(liability.get(h, 0))) for h in HEADS},
        "remaining_credit": {h: str(avail[h]) for h in HEADS},
        "remaining_liability": {h: str(liab[h]) for h in HEADS},
    }


def validate_utilisation(entries: list[dict], rules: dict) -> None:
    """Reject any entry not permitted by the policy — specifically CGST<->SGST cross
    unless ``allow_cgst_sgst_cross`` is set in an approved policy version."""
    allow_cross = bool(rules.get("allow_cgst_sgst_cross", False))
    for e in entries:
        pair = {str(e.get("credit_head")).upper(), str(e.get("liability_head")).upper()}
        if pair == {"CGST", "SGST"} and not allow_cross:
            raise ProhibitedUtilisation(
                "CGST credit cannot be set off against SGST liability (or vice versa); "
                "this cross-utilisation is not legally permitted")


# ------------------------- proposal / approve / post ------------------------

def _ref(tax_period: str) -> str:
    return f"GSTUTIL:{tax_period}"


def propose_cross_adjustment(db: Session, company_id: uuid.UUID, *, tax_period: str,
                             available: dict, liability: dict, actor=None) -> dict:
    if not period_valid(tax_period):
        raise GstUtilisationError("tax_period must be MMYYYY")
    pol = active_policy(db, company_id)
    if pol is None:
        raise GstUtilisationError(
            "no APPROVED GST policy version; a CA-approved policy is required before utilisation")
    existing = db.execute(select(GstCrossAdjustment).where(
        GstCrossAdjustment.company_id == company_id,
        GstCrossAdjustment.posting_reference == _ref(tax_period))).scalar_one_or_none()
    if existing is not None and existing.status != CrossAdjStatus.REVERSED.value:
        raise GstUtilisationError(
            f"a cross-adjustment already exists for period {tax_period} (status {existing.status})")
    rules = _rules_of(pol)
    proposal = compute_utilisation(available, liability, rules)
    validate_utilisation(proposal["entries"], rules)
    rec = GstCrossAdjustment(
        company_id=company_id, tax_period=tax_period, policy_version_id=pol.id,
        posting_reference=_ref(tax_period), proposal=json.dumps(proposal),
        status=CrossAdjStatus.PROPOSED.value, created_at_actor=actor,
        created_by=actor, updated_by=actor)
    db.add(rec)
    db.flush()
    record_audit(db, action="GST_CROSSADJ_PROPOSED", entity="gst_cross_adjustment",
                 entity_id=str(rec.id), actor_id=actor,
                 detail={"company_id": str(company_id), "tax_period": tax_period,
                         "policy_version": pol.version_no})
    return {"cross_adjustment_id": str(rec.id), "tax_period": tax_period,
            "policy_version": pol.version_no, "status": rec.status, **proposal}


def approve_cross_adjustment(db: Session, company_id: uuid.UUID, adj_id: uuid.UUID, *,
                             approver_name: str, actor=None) -> GstCrossAdjustment:
    rec = db.get(GstCrossAdjustment, adj_id)
    if rec is None or rec.company_id != company_id:
        raise GstUtilisationError("cross-adjustment not found")
    if rec.status != CrossAdjStatus.PROPOSED.value:
        raise GstUtilisationError(f"only a PROPOSED cross-adjustment can be CA-approved (is {rec.status})")
    rec.status = CrossAdjStatus.CA_APPROVED.value
    rec.ca_approved_by = actor
    rec.ca_approver_name = approver_name
    rec.ca_approved_at = now()
    db.flush()
    record_audit(db, action="GST_CROSSADJ_APPROVED", entity="gst_cross_adjustment",
                 entity_id=str(rec.id), actor_id=actor,
                 detail={"company_id": str(company_id), "approver": approver_name})
    return rec


def post_cross_adjustment(db: Session, company_id: uuid.UUID, adj_id: uuid.UUID, *,
                          authorize: bool = False, posted_by=None, actor_name=None):
    """HARD-GATED posting: requires CA_APPROVED status, an APPROVED policy, and an
    explicit ``authorize=True``. Posts ONE balanced voucher (Dr output / Cr input)."""
    rec = db.get(GstCrossAdjustment, adj_id)
    if rec is None or rec.company_id != company_id:
        raise GstUtilisationError("cross-adjustment not found")
    existing = pe.find_existing(db, company_id, rec.posting_reference)
    if existing is not None and rec.status == CrossAdjStatus.POSTED.value:
        return existing, 0
    if not authorize:
        raise UtilisationPostingBlocked(
            "GST cross-utilisation posting requires explicit authorisation (authorize=True)")
    if rec.status != CrossAdjStatus.CA_APPROVED.value:
        raise UtilisationPostingBlocked(f"cross-adjustment must be CA_APPROVED to post (is {rec.status})")
    pol = db.get(GstPolicyVersion, rec.policy_version_id) if rec.policy_version_id else None
    if pol is None or pol.status != PolicyStatus.APPROVED.value:
        raise UtilisationPostingBlocked("the governing GST policy version is not APPROVED")
    proposal = json.loads(rec.proposal)
    validate_utilisation(proposal["entries"], _rules_of(pol))

    lines = []
    for e in proposal["entries"]:
        ch, lh, amt = e["credit_head"].upper(), e["liability_head"].upper(), m(e["amount"])
        if amt <= MZERO:
            continue
        if "CESS" in (ch, lh):
            raise UtilisationPostingBlocked(
                "cess cross-utilisation posting requires a configured cess ledger account")
        out_acct = resolve_account(db, company_id, "GST_ADJUSTMENT", f"output_{lh.lower()}")
        in_acct = resolve_account(db, company_id, "GST_ADJUSTMENT", f"input_{ch.lower()}")
        lines.append(pe.PostingLine(out_acct, debit=amt,
                                    narration=f"Utilise {ch} credit vs {lh} liability"))
        lines.append(pe.PostingLine(in_acct, credit=amt,
                                    narration=f"Utilise {ch} credit vs {lh} liability"))
    if not lines:
        raise UtilisationPostingBlocked("nothing to utilise")

    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="GADJ", voucher_date=date(
            int(rec.tax_period[2:]), int(rec.tax_period[:2]), 1),
        posting_reference=rec.posting_reference,
        narration=f"GST credit cross-utilisation {rec.tax_period}",
        source_module="gst_utilisation", source_id=str(rec.id),
        created_by=posted_by, approved_by=posted_by, actor_name=actor_name, lines=lines))
    rec.status = CrossAdjStatus.POSTED.value
    rec.posted_voucher_id = voucher.id
    rec.posted_at = now()
    db.flush()
    record_audit(db, action="GST_CROSSADJ_POSTED", entity="gst_cross_adjustment",
                 entity_id=str(rec.id), actor_id=posted_by,
                 detail={"company_id": str(company_id), "voucher": voucher.number,
                         "tax_period": rec.tax_period})
    return voucher, len([e for e in proposal["entries"] if m(e["amount"]) > MZERO])


def list_cross_adjustments(db: Session, company_id: uuid.UUID, *, tax_period: str | None = None):
    stmt = select(GstCrossAdjustment).where(GstCrossAdjustment.company_id == company_id)
    if tax_period:
        stmt = stmt.where(GstCrossAdjustment.tax_period == tax_period)
    return db.execute(stmt.order_by(GstCrossAdjustment.tax_period)).scalars().all()
