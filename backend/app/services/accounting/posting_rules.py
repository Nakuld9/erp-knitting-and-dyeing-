"""Resolve account codes for business events from configurable rules.

Lookup order: a company-specific ``accounting_posting_rules`` row first, then a
built-in default. Business modules reference semantic *slots* (``inventory``,
``input_cgst``, ``receivable`` …) so the chart of accounts stays configurable.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting import PostingRule

# event_type -> {slot: default account code}
DEFAULT_RULES: dict[str, dict[str, str]] = {
    "CHEMICAL_GRN": {
        "inventory": "1100", "input_cgst": "1121", "input_sgst": "1122",
        "input_igst": "1123", "payable": "2010",
    },
    "YARN_GRN": {
        "inventory": "1060", "input_cgst": "1121", "input_sgst": "1122",
        "input_igst": "1123", "payable": "2010",
    },
    "CHEMICAL_CONSUMPTION": {"wip": "1080", "inventory": "1100"},
    "DYE_CONSUMPTION": {"wip": "1080", "inventory": "1090"},
    "YARN_ISSUE": {"wip": "1080", "inventory": "1060"},
    "KNITTING_PRODUCTION": {"greige": "1070", "wip": "1080"},
    "WIP_TO_FG": {"fg": "1085", "wip": "1080"},
    "COGS": {"cogs": "5150", "fg": "1085"},
    # Dispatch of fabric rolls: credit the sub-ledger account the roll sits in
    # (greige vs finished), debit COGS.
    "FABRIC_DELIVERY": {"cogs": "5150", "greige": "1070", "fg": "1085"},
    "SALES_INVOICE": {
        "receivable": "1040", "sales": "4010", "output_cgst": "2041",
        "output_sgst": "2042", "output_igst": "2043",
    },
    "CUSTOMER_RECEIPT": {"bank": "1020", "receivable": "1040"},
    "SUPPLIER_PAYMENT": {"payable": "2010", "bank": "1020"},
}


def resolve_account(db: Session, company_id: uuid.UUID, event_type: str, slot: str) -> str:
    row = db.execute(
        select(PostingRule.account_code).where(
            PostingRule.company_id == company_id,
            PostingRule.event_type == event_type,
            PostingRule.role == slot,
        )
    ).scalar_one_or_none()
    if row:
        return row
    try:
        return DEFAULT_RULES[event_type][slot]
    except KeyError as exc:
        raise KeyError(f"No posting rule for event '{event_type}' slot '{slot}'") from exc


def seed_default_posting_rules(db: Session, company_id: uuid.UUID) -> int:
    """Persist the defaults as editable rows for a company (idempotent)."""
    created = 0
    for event_type, slots in DEFAULT_RULES.items():
        for slot, code in slots.items():
            exists = db.execute(
                select(PostingRule).where(
                    PostingRule.company_id == company_id,
                    PostingRule.event_type == event_type,
                    PostingRule.role == slot,
                )
            ).scalar_one_or_none()
            if exists is None:
                db.add(PostingRule(company_id=company_id, event_type=event_type, role=slot, account_code=code))
                created += 1
    db.flush()
    return created
