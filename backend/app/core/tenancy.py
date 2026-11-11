"""Company-ownership guards for references used by transactional services.

Use these at service / API boundaries before a transaction references a
company-owned master or document, so a caller cannot point at another company's
supplier, customer, account, warehouse or document by id. They fail closed.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session


class CompanyOwnershipError(ValueError):
    """A referenced record is missing, inactive, or belongs to another company."""


def require_company_record(db: Session, model, record_id, company_id: uuid.UUID, *,
                           label: str | None = None, require_active: bool = True,
                           optional: bool = False):
    """Return the record only if it exists and belongs to ``company_id``.

    ``optional`` allows a None id (returns None); otherwise a missing id is an
    error. ``require_active`` additionally requires is_active where the model has
    it. Raises :class:`CompanyOwnershipError` on any failure.
    """
    name = label or model.__name__
    if record_id is None:
        if optional:
            return None
        raise CompanyOwnershipError(f"{name} is required")
    if not hasattr(model, "company_id"):
        raise TypeError(f"{model.__name__} is not a company-scoped model")
    stmt = select(model).where(model.id == record_id, model.company_id == company_id)
    if require_active and hasattr(model, "is_active"):
        stmt = stmt.where(model.is_active.is_(True))
    record = db.execute(stmt).scalar_one_or_none()
    if record is None:
        raise CompanyOwnershipError(
            f"{name} was not found, is inactive, or is outside this company")
    return record
