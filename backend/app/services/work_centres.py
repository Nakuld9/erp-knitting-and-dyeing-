"""Machines as work centres.

Each production machine is a work centre: a WORK cost centre sitting under its
department cost centre (Knitting / Dyeing / Finishing). Linking the machine to
its work centre lets production postings carry a ``cost_centre_id`` so cost
accumulates per machine in the general ledger, rolling up to the department.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.masters import CostCentre, CostCentreType, Machine
from app.seed.seed import DEPT_FOR_MACHINE_TYPE


def _department_centre(db: Session, company_id: uuid.UUID, machine_type: str) -> CostCentre | None:
    code = DEPT_FOR_MACHINE_TYPE.get(machine_type)
    if not code:
        return None
    return db.execute(
        select(CostCentre).where(CostCentre.company_id == company_id, CostCentre.code == code)
    ).scalar_one_or_none()


def ensure_work_centre(db: Session, company_id: uuid.UUID, machine: Machine) -> CostCentre | None:
    """Return the machine's work-centre cost centre, creating and linking it on
    first use. A machine already linked to a cost centre is left as-is."""
    if machine.cost_centre_id is not None:
        return db.get(CostCentre, machine.cost_centre_id)

    code = f"WC-{machine.code}"
    existing = db.execute(
        select(CostCentre).where(CostCentre.company_id == company_id, CostCentre.code == code)
    ).scalar_one_or_none()
    if existing is None:
        dept = _department_centre(db, company_id, machine.machine_type)
        existing = CostCentre(company_id=company_id, code=code, name=machine.name,
                              centre_type=CostCentreType.WORK,
                              parent_id=dept.id if dept else None)
        db.add(existing)
        db.flush()
    machine.cost_centre_id = existing.id
    db.flush()
    return existing


def machine_cost_centre_id(db: Session, company_id: uuid.UUID, machine_id: uuid.UUID | None):
    """The cost-centre id to stamp on a machine's production postings, creating
    the work centre on first use. Returns None when there is no machine."""
    if machine_id is None:
        return None
    machine = db.get(Machine, machine_id)
    if machine is None or machine.company_id != company_id:
        return None
    wc = ensure_work_centre(db, company_id, machine)
    return wc.id if wc else None
