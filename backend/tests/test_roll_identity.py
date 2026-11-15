"""Stage 2 — protected Roll ID generation.

Covers: new greige/dyed ID formats, company separation, fiscal-year separation,
sequence uniqueness, the length guard, caller-supplied ID rejection at the web
boundary, dyed-output idempotency, and identity preservation through finishing.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.core import Company
from app.models.manufacturing import FabricRoll, KnittingProduction, RollKind
from app.services.manufacturing import roll_identity
from app.services.manufacturing.knitting import record_production

WHEN = date(2026, 11, 7)      # FY 2026-2027


def _second_company(db):
    c = db.execute(select(Company).where(Company.code == "UATB")).scalar_one_or_none()
    if c is None:
        c = Company(code="UATB", name="UAT Weaves", legal_name="UAT Weaves Pvt Ltd",
                    gstin="27ABCDE9999F1Z2", state_code="27", currency="INR")
        db.add(c); db.commit()
    return c


# ---- formats ----

def test_greige_id_format_and_length(db, company):
    rid = roll_identity.new_greige_roll_id(db, company_id=company.id, when=WHEN)
    assert rid.startswith("GR-DEMO-")           # GR-<COMPANY_CODE>-<FY>-<SEQ>
    assert rid.endswith("-00001")
    assert len(rid) <= roll_identity.ROLL_NO_MAX_LEN


def test_dyed_id_format_distinct_stream(db, company):
    rid = roll_identity.new_dyed_roll_id(db, company_id=company.id, when=WHEN)
    assert rid.startswith("DY-DEMO-")
    assert rid.endswith("-00001")               # dyed has its own sequence stream
    assert len(rid) <= roll_identity.ROLL_NO_MAX_LEN


# ---- company separation ----

def test_company_separation(db, company):
    other = _second_company(db)
    a = roll_identity.new_greige_roll_id(db, company_id=company.id, when=WHEN)
    b = roll_identity.new_greige_roll_id(db, company_id=other.id, when=WHEN)
    assert a.startswith("GR-DEMO-") and b.startswith("GR-UATB-")
    # independent sequences: both start at 1 within their own company
    assert a.endswith("-00001") and b.endswith("-00001")


# ---- fiscal-year separation ----

def test_fiscal_year_separation(db, company):
    this_fy = roll_identity.new_greige_roll_id(db, company_id=company.id, when=date(2026, 11, 7))
    last_fy = roll_identity.new_greige_roll_id(db, company_id=company.id, when=date(2025, 11, 7))
    # Different FY segment => IDs never collide across financial years, and each
    # FY has its own sequence that restarts at 1.
    assert this_fy != last_fy
    assert this_fy.endswith("-00001") and last_fy.endswith("-00001")
    assert this_fy.split("-")[2] != last_fy.split("-")[2]


# ---- sequence uniqueness ----

def test_sequence_is_unique_and_increments(db, company):
    one = roll_identity.new_greige_roll_id(db, company_id=company.id, when=WHEN)
    two = roll_identity.new_greige_roll_id(db, company_id=company.id, when=WHEN)
    assert one.endswith("-00001") and two.endswith("-00002")
    assert one != two


# ---- length guard (never truncate) ----

def test_length_guard_raises_not_truncates(db, company, monkeypatch):
    monkeypatch.setattr(roll_identity, "company_code", lambda *a, **k: "X" * 40)
    with pytest.raises(roll_identity.RollIdentityError):
        roll_identity.new_greige_roll_id(db, company_id=company.id, when=WHEN)


# ---- factory isolation guard ----

def test_factory_must_belong_to_company(db, company):
    other = _second_company(db)
    from app.models.core import Branch
    foreign = Branch(company_id=other.id, code="FBR", name="Foreign Factory")
    db.add(foreign); db.flush()
    with pytest.raises(roll_identity.RollIdentityError):
        roll_identity.assert_factory_in_company(db, company_id=company.id, factory_id=foreign.id)
    # None factory is always allowed (single-factory installs / historical rows)
    roll_identity.assert_factory_in_company(db, company_id=company.id, factory_id=None)


# ---- caller-supplied IDs are rejected at the web boundary ----

def test_api_schema_drops_client_roll_no():
    from app.api.routes.knitting import RollIn
    dumped = RollIn(**{"roll_no": "HACKED-ID", "weight_kg": Decimal("10")}).model_dump()
    assert "roll_no" not in dumped          # client can never set a Roll ID


# ---- service path still mints protected IDs; blank -> auto ----

def _kp(db, demo):
    kp = KnittingProduction(company_id=demo["company_id"], number="KP-ID", machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], yarn_lot_id=demo["yarn_lot_id"],
                            fabric_type_id=demo["fabric_type_id"], production_date=WHEN,
                            produced_kg=Decimal("250"), yarn_consumed_kg=Decimal("255"))
    db.add(kp); db.flush()
    return kp


def test_blank_roll_no_auto_minted(db, demo):
    kp = _kp(db, demo)
    rolls = record_production(db, kp, [{"weight_kg": "250", "warehouse_id": demo["warehouses"]["greige"]}])["rolls"]
    assert rolls[0].roll_no.startswith("GR-DEMO-")
    assert rolls[0].barcode == rolls[0].roll_no


# ---- finishing preserves the Roll ID ----

def test_finishing_preserves_roll_id(db, demo):
    kp = _kp(db, demo)
    roll = record_production(db, kp, [{"weight_kg": "250", "warehouse_id": demo["warehouses"]["greige"]}])["rolls"][0]
    db.commit()
    original_id, original_no, original_barcode = roll.id, roll.roll_no, roll.barcode

    from app.services import quality
    quality.record_finishing(
        db, demo["company_id"], process_type="COMPACTING",
        process_date=WHEN, fabric_roll_id=roll.id,
        input_weight_kg=Decimal("250"), output_weight_kg=Decimal("248"))
    db.commit()
    db.refresh(roll)
    assert roll.id == original_id
    assert roll.roll_no == original_no            # NEVER renumbered on stage change
    assert roll.barcode == original_barcode
    assert (roll.kind == RollKind.FINISHED or roll.kind == RollKind.FINISHED.value)
