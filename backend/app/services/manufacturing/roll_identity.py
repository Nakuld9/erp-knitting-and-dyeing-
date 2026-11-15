"""Protected Roll ID generation (Stage 2).

A single place that mints Roll IDs for *genuinely new physical manufacturing
output*. It wraps and reuses the existing company/FY-scoped ``ROLL`` document
sequence (``app.services.sequences.next_document_number``) so numbering stays
gap-free and concurrency-safe, and it guarantees:

* a stable, immutable **company** identifier in the ID (``Company.code``), never
  an editable company name or a machine code that can drift;
* the fiscal year embedded in the ID, so IDs never collide across financial
  years;
* the result always fits ``FabricRoll.roll_no`` (``String(40)``) — if a format
  would exceed the limit it raises rather than silently truncating.

Formats
-------
* New greige roll:  ``GR-{COMPANY_CODE}-{FY}-{SEQUENCE}``  e.g. ``GR-DEMO-2627-00001``
* New dyed roll:    ``DY-{COMPANY_CODE}-{FY}-{SEQUENCE}``  e.g. ``DY-DEMO-2627-00001``

Historical rolls are never renumbered: this module only mints IDs for new rolls.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.models.core import Branch, Company
from app.services.accounting.posting_engine import fiscal_year_for
from app.services.sequences import next_document_number

ROLL_NO_MAX_LEN = 40          # must match FabricRoll.roll_no String(40)
SEQ_PREFIX_MAX_LEN = 20       # must match DocumentSequence.prefix String(20)
_SEQ_PADDING = 5              # zero-padded sequence width (matches next_document_number)
_FY_SEGMENT_LEN = 4           # fiscal-year segment, e.g. "2627"

# Separate document-sequence streams so the greige prefix can never be frozen
# onto a dyed roll (or vice-versa). "ROLL" is the pre-existing greige stream.
DOC_TYPE_GREIGE = "ROLL"
DOC_TYPE_DYED = "ROLL_DYED"


class RollIdentityError(ValueError):
    """A Roll ID could not be minted safely (e.g. it would exceed the length
    limit, or a required company/factory record is missing/mismatched)."""


def company_code(db: Session, company_id: uuid.UUID) -> str:
    company = db.get(Company, company_id)
    if company is None:
        raise RollIdentityError("Company not found for roll identity")
    code = (company.code or "").strip()
    if not code:
        raise RollIdentityError("Company has no stable code for roll identity")
    return code


def _mint(db: Session, *, company_id: uuid.UUID, doc_type: str, kind_prefix: str,
          when: date) -> str:
    code = company_code(db, company_id)
    fy = fiscal_year_for(when)
    prefix = f"{kind_prefix}-{code}-"

    # Validate length BEFORE touching the sequence so the behaviour is identical
    # on SQLite and PostgreSQL (PG enforces column lengths; SQLite does not). This
    # never truncates — it surfaces the problem. The sequence prefix column is
    # String(20); the final roll_no column is String(40).
    if len(prefix) > SEQ_PREFIX_MAX_LEN:
        raise RollIdentityError(
            f"Roll-ID prefix {prefix!r} exceeds {SEQ_PREFIX_MAX_LEN} chars; "
            "shorten the company code.")
    projected = len(prefix) + _FY_SEGMENT_LEN + 1 + _SEQ_PADDING
    if projected > ROLL_NO_MAX_LEN:
        raise RollIdentityError(
            f"Roll-ID for prefix {prefix!r} would exceed {ROLL_NO_MAX_LEN} chars; "
            "shorten the company code.")

    roll_no = next_document_number(
        db, company_id=company_id, doc_type=doc_type, fiscal_year=fy, prefix=prefix,
    )
    if len(roll_no) > ROLL_NO_MAX_LEN:
        # Belt-and-suspenders: the sequence has grown beyond the padded width.
        raise RollIdentityError(
            f"Generated roll_no {roll_no!r} exceeds {ROLL_NO_MAX_LEN} chars.")
    return roll_no


def new_greige_roll_id(db: Session, *, company_id: uuid.UUID, when: date) -> str:
    """Mint a protected ID for a new greige roll: ``GR-{COMPANY}-{FY}-{SEQ}``."""
    return _mint(db, company_id=company_id, doc_type=DOC_TYPE_GREIGE,
                 kind_prefix="GR", when=when)


def new_dyed_roll_id(db: Session, *, company_id: uuid.UUID, when: date) -> str:
    """Mint a protected ID for a new dyed roll: ``DY-{COMPANY}-{FY}-{SEQ}``."""
    return _mint(db, company_id=company_id, doc_type=DOC_TYPE_DYED,
                 kind_prefix="DY", when=when)


def assert_factory_in_company(db: Session, *, company_id: uuid.UUID,
                              factory_id: uuid.UUID | None) -> None:
    """Enforce company/factory isolation for a newly created roll.

    A ``None`` factory is allowed (single-factory installs and historical rows).
    A set factory must belong to ``company_id`` — otherwise the roll would leak
    across the company boundary.
    """
    if factory_id is None:
        return
    branch = db.get(Branch, factory_id)
    if branch is None or branch.company_id != company_id:
        raise RollIdentityError("Factory does not belong to this company")
