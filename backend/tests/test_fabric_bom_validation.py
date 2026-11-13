"""Fabric BOM input validation and version-control integrity."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.bom import service as bom


def _draft(db, demo, code="FB-VAL"):
    b = bom.create_bom(db, company_id=demo["company_id"], code=code, name=code)
    return b, b.versions[0]


def test_blend_must_sum_to_100(db, demo):
    b, v = _draft(db, demo)
    with pytest.raises(bom.BomError):
        bom.set_yarns(db, v.id, [
            {"yarn_id": demo["yarn_id"], "blend_percent": Decimal("60"),
             "std_consumption_per_kg": Decimal("1"), "std_unit_cost": Decimal("1")}])


def test_consumption_must_be_positive(db, demo):
    b, v = _draft(db, demo)
    with pytest.raises(bom.BomError):
        bom.set_yarns(db, v.id, [
            {"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
             "std_consumption_per_kg": Decimal("0"), "std_unit_cost": Decimal("1")}])


def test_wastage_out_of_range_rejected(db, demo):
    b, v = _draft(db, demo)
    with pytest.raises(bom.BomError):
        bom.set_yarns(db, v.id, [
            {"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
             "std_consumption_per_kg": Decimal("1"), "wastage_percent": Decimal("120"),
             "std_unit_cost": Decimal("1")}])


def test_duplicate_yarn_in_blend_rejected(db, demo):
    b, v = _draft(db, demo)
    with pytest.raises(bom.BomError):
        bom.set_yarns(db, v.id, [
            {"yarn_id": demo["yarn_id"], "blend_percent": Decimal("50"),
             "std_consumption_per_kg": Decimal("0.5"), "std_unit_cost": Decimal("1")},
            {"yarn_id": demo["yarn_id"], "blend_percent": Decimal("50"),
             "std_consumption_per_kg": Decimal("0.5"), "std_unit_cost": Decimal("1")}])


def test_invalid_finishing_process_rejected(db, demo):
    b, v = _draft(db, demo)
    with pytest.raises(bom.BomError):
        bom.set_finishing(db, v.id, [{"process_type": "NOT_A_PROCESS", "std_cost_per_kg": Decimal("1")}])


def test_duplicate_bom_code_rejected(db, demo):
    bom.create_bom(db, company_id=demo["company_id"], code="FB-DUP", name="a")
    db.flush()
    with pytest.raises(bom.BomError):
        bom.create_bom(db, company_id=demo["company_id"], code="FB-DUP", name="b")


def test_effective_date_overlap_rejected(db, demo):
    b, v1 = _draft(db, demo, code="FB-EFF")
    bom.set_yarns(db, v1.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                               "std_consumption_per_kg": Decimal("1"), "std_unit_cost": Decimal("1")}])
    bom.approve_version(db, version_id=v1.id, approved_by=None, effective_from=date(2026, 6, 1))
    v2 = bom.add_version(db, bom_id=b.id, clone_from_version_id=v1.id)
    # a new version effective on/before the current one's start overlaps → rejected
    with pytest.raises(bom.BomError):
        bom.approve_version(db, version_id=v2.id, approved_by=None, effective_from=date(2026, 5, 1))
    # strictly after is fine
    bom.approve_version(db, version_id=v2.id, approved_by=None, effective_from=date(2026, 7, 1))


def test_db_unique_constraint_blocks_duplicate_version_no(db, demo):
    """The (bom_id, version_no) unique index is the real concurrency guard."""
    from sqlalchemy.exc import IntegrityError
    from app.models.fabric_bom import FabricBomVersion, BomStatus
    b, v1 = _draft(db, demo, code="FB-UNI")
    db.add(FabricBomVersion(bom_id=b.id, version_no=1, status=BomStatus.DRAFT))  # dup of v1
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
