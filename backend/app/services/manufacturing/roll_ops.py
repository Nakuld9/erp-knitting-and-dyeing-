"""Fabric-roll split and merge — RETIRED (Stage 4A).

Roll split and merge are no longer supported. The former implementation minted
DERIVED roll numbers (e.g. ``{parent}-S1`` / ``{parent}-MRG``), bypassing the
Stage-2 protected identity generator; it recorded no ``RollGenealogyEdge``; and
its usability check ignored Stage-3D vendor custody, so a roll physically at a
job worker could be split/merged. The business decision is that no new
split/merge workflow is required, so these operations are retired rather than
reimplemented.

The two public functions are kept so existing imports resolve, but they reject
every call and perform NO database work — a direct service call therefore cannot
bypass the API restriction, and a rejected request changes no roll, genealogy or
inventory balance. Historical split/merge records (``FabricRoll`` rows with
``status`` SPLIT/MERGED and ``parent_roll_id`` set) are left untouched and remain
readable through the normal roll endpoints.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session


class RollOpError(Exception):
    """A roll split/merge request. The operation is retired, so this is always
    raised to reject the call."""


_UNSUPPORTED = (
    "Roll split/merge operations are not supported. Roll identity is protected "
    "(Stage 2) and these legacy operations have been retired.")


def split_roll(db: Session, *, company_id: uuid.UUID, roll_id: uuid.UUID,
               parts=None, created_by: uuid.UUID | None = None) -> dict:
    """RETIRED — always rejects. No derived Roll IDs are created and no database
    state is changed."""
    raise RollOpError(_UNSUPPORTED)


def merge_rolls(db: Session, *, company_id: uuid.UUID, roll_ids=None,
                new_roll_no: str | None = None, created_by: uuid.UUID | None = None) -> dict:
    """RETIRED — always rejects. No derived Roll IDs are created and no database
    state is changed."""
    raise RollOpError(_UNSUPPORTED)
