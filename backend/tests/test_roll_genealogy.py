"""Stage 2 — manufacturing roll genealogy + company-scoped uniqueness.

Covers the real workflows (knit -> greige, greige -> dyed, semi-finished ->
finished), partial processing with reconciliation, rejection/loss, correction/
reversal, duplicate-consumption prevention, company/factory isolation, the
separation of kg vs metre fields, the company-scoped unique index, and the
migration's duplicate pre-flight detector.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.core import Company
from app.models.genealogy import RollGenealogyEdge
from app.models.manufacturing import FabricRoll, KnittingProduction, RollKind
from app.services.manufacturing import roll_genealogy as g
from app.services.manufacturing.knitting import record_production

WHEN = date(2026, 11, 7)


def _edges(db, company_id, **where):
    stmt = select(RollGenealogyEdge).where(RollGenealogyEdge.company_id == company_id)
    return db.execute(stmt).scalars().all()


def _greige(db, demo, *, number="KP-G", kg="250"):
    kp = KnittingProduction(company_id=demo["company_id"], number=number, machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], yarn_lot_id=demo["yarn_lot_id"],
                            fabric_type_id=demo["fabric_type_id"], production_date=WHEN,
                            produced_kg=Decimal(kg), yarn_consumed_kg=Decimal("255"))
    db.add(kp); db.flush()
    rolls = record_production(db, kp, [{"weight_kg": kg, "length_m": "900",
                                        "warehouse_id": demo["warehouses"]["greige"]}])["rolls"]
    db.commit()
    return rolls[0]


# ---- workflow: knitting records an origin edge --------------------------------

def test_knitting_records_origin_edge(db, demo):
    roll = _greige(db, demo)
    edges = db.execute(select(RollGenealogyEdge).where(
        RollGenealogyEdge.output_roll_id == roll.id)).scalars().all()
    assert len(edges) == 1
    e = edges[0]
    assert e.process_type == g.KNIT and e.event_type == g.PRODUCE
    assert e.source_roll_id is None                      # yarn, not a roll
    assert Decimal(str(e.output_weight_kg)) == Decimal("250")
    assert Decimal(str(e.output_length_m)) == Decimal("900")
    assert e.uom == g.UOM_KG


# ---- workflow: dyeing records a produce edge for the dyed output --------------

def test_dyeing_records_edge(db, demo):
    from tests.test_dyed_dispatch import _dyed_job
    from app.models.dyeing_cost import DyeingOutput
    o, job = _dyed_job(db, demo, code="GEN1", lots=[{"lot_code": "L1", "qty_kg": Decimal("95")}])
    out = db.execute(select(DyeingOutput).where(DyeingOutput.job_id == job.id)).scalar_one()
    edges = db.execute(select(RollGenealogyEdge).where(
        RollGenealogyEdge.output_roll_id == out.dyed_item_id,
        RollGenealogyEdge.process_type == g.DYE)).scalars().all()
    assert len(edges) >= 1
    assert edges[0].production_ref == job.number


# ---- workflow: finishing is a same-roll stage edge ---------------------------

def test_finishing_records_same_roll_edge(db, demo):
    roll = _greige(db, demo, number="KP-FIN")
    from app.services import quality
    quality.record_finishing(db, demo["company_id"], process_type="COMPACTING",
                             process_date=WHEN, fabric_roll_id=roll.id,
                             input_weight_kg=Decimal("250"), output_weight_kg=Decimal("247"))
    db.commit()
    e = db.execute(select(RollGenealogyEdge).where(
        RollGenealogyEdge.process_type == g.FINISH,
        RollGenealogyEdge.output_roll_id == roll.id)).scalars().first()
    assert e is not None
    assert e.source_roll_id == roll.id and e.output_roll_id == roll.id   # same identity
    assert Decimal(str(e.input_weight_kg)) == Decimal("250")
    assert Decimal(str(e.output_weight_kg)) == Decimal("247")


# ---- reconciliation: input = accepted + loss + rejected + remaining ----------

def test_reconcile_balances():
    g.reconcile(input_qty=100, accepted_output=90, process_loss=5, rejected_qty=3, remaining=2)


def test_reconcile_imbalance_raises():
    with pytest.raises(g.GenealogyError):
        g.reconcile(input_qty=100, accepted_output=90, process_loss=0, rejected_qty=0, remaining=0)


def test_reconcile_rejects_negative():
    with pytest.raises(g.GenealogyError):
        g.reconcile(input_qty=100, accepted_output=110, process_loss=0, rejected_qty=0, remaining=-10)


# ---- partial processing + remaining + rejection/loss -------------------------

def test_partial_processing_records_components(db, demo):
    src = _greige(db, demo, number="KP-P1", kg="100")
    edge = g.record_partial(
        db, company_id=demo["company_id"], source_roll_id=src.id, output_roll_id=None,
        input_qty=Decimal("100"), accepted_output=Decimal("80"),
        process_loss=Decimal("5"), rejected_qty=Decimal("3"), remaining=Decimal("12"),
        production_ref="JOB-P1")
    assert edge.event_type == g.PARTIAL
    assert Decimal(str(edge.input_qty)) == Decimal("100")
    assert Decimal(str(edge.output_qty)) == Decimal("80")
    assert Decimal(str(edge.process_loss)) == Decimal("5")
    assert Decimal(str(edge.rejected_qty)) == Decimal("3")


def test_partial_imbalance_blocked(db, demo):
    src = _greige(db, demo, number="KP-P2", kg="100")
    with pytest.raises(g.GenealogyError):
        g.record_partial(db, company_id=demo["company_id"], source_roll_id=src.id, output_roll_id=None,
                         input_qty=Decimal("100"), accepted_output=Decimal("80"))  # 20 unexplained


# ---- duplicate consumption prevention ----------------------------------------

def test_duplicate_consumption_blocked(db, demo):
    src = _greige(db, demo, number="KP-DUP", kg="100")
    grp = uuid.uuid4()
    g.record_partial(db, company_id=demo["company_id"], source_roll_id=src.id, output_roll_id=None,
                    input_qty=Decimal("50"), accepted_output=Decimal("50"), event_group_id=grp)
    with pytest.raises(g.GenealogyError):
        g.record_partial(db, company_id=demo["company_id"], source_roll_id=src.id, output_roll_id=None,
                        input_qty=Decimal("50"), accepted_output=Decimal("50"), event_group_id=grp)


# ---- correction / reversal preserves the original ----------------------------

def test_reversal_preserves_original_and_negates(db, demo):
    roll = _greige(db, demo, number="KP-REV")
    original = db.execute(select(RollGenealogyEdge).where(
        RollGenealogyEdge.output_roll_id == roll.id)).scalars().first()
    rev = g.reverse_edge(db, original.id, company_id=demo["company_id"],
                        recorded_by=None, reason="miskeyed weight")
    assert rev.correction_of_id == original.id
    assert rev.event_type == g.REVERSAL
    assert Decimal(str(rev.output_weight_kg)) == -Decimal(str(original.output_weight_kg))
    # original is untouched
    db.refresh(original)
    assert original.event_type == g.PRODUCE
    assert Decimal(str(original.output_weight_kg)) == Decimal("250")


def test_reversal_requires_reason(db, demo):
    roll = _greige(db, demo, number="KP-REV2")
    original = db.execute(select(RollGenealogyEdge).where(
        RollGenealogyEdge.output_roll_id == roll.id)).scalars().first()
    with pytest.raises(g.GenealogyError):
        g.reverse_edge(db, original.id, company_id=demo["company_id"], recorded_by=None, reason="  ")


# ---- company isolation --------------------------------------------------------

def test_edge_rejects_cross_company_roll(db, demo):
    roll = _greige(db, demo, number="KP-ISO")
    other = db.execute(select(Company).where(Company.code == "UATB")).scalar_one_or_none()
    if other is None:
        other = Company(code="UATB", name="UAT Weaves", legal_name="UAT Weaves Pvt Ltd",
                        gstin="27ABCDE9999F1Z2", state_code="27", currency="INR")
        db.add(other); db.commit()
    # recording an edge in company B that points at company A's roll must fail
    with pytest.raises(g.GenealogyError):
        g.record_edge(db, company_id=other.id, process_type=g.DYE, output_roll_id=roll.id)


# ---- kg and metre are separate, never conflated ------------------------------

def test_weight_and_length_are_separate_fields(db, demo):
    roll = _greige(db, demo, number="KP-UOM")
    e = db.execute(select(RollGenealogyEdge).where(
        RollGenealogyEdge.output_roll_id == roll.id)).scalars().first()
    # the two dimensions live in distinct columns with distinct values
    assert Decimal(str(e.output_weight_kg)) == Decimal("250")
    assert Decimal(str(e.output_length_m)) == Decimal("900")
    assert e.output_weight_kg != e.output_length_m


# ---- company-scoped uniqueness ------------------------------------------------

def test_duplicate_roll_no_rejected_within_company(db, demo):
    cid = demo["company_id"]
    db.add(FabricRoll(company_id=cid, roll_no="DUP-1", kind=RollKind.GREIGE, weight_kg=Decimal("1")))
    db.flush()
    db.add(FabricRoll(company_id=cid, roll_no="DUP-1", kind=RollKind.GREIGE, weight_kg=Decimal("1")))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_same_roll_no_allowed_in_separate_companies(db, demo):
    other = db.execute(select(Company).where(Company.code == "UATB")).scalar_one_or_none()
    if other is None:
        other = Company(code="UATB", name="UAT Weaves", legal_name="UAT Weaves Pvt Ltd",
                        gstin="27ABCDE9999F1Z2", state_code="27", currency="INR")
        db.add(other); db.commit()
    db.add(FabricRoll(company_id=demo["company_id"], roll_no="SHARED-1", kind=RollKind.GREIGE, weight_kg=Decimal("1")))
    db.add(FabricRoll(company_id=other.id, roll_no="SHARED-1", kind=RollKind.GREIGE, weight_kg=Decimal("1")))
    db.flush()      # company-scoped, so this is allowed
    db.rollback()


# ---- migration duplicate pre-flight detector ---------------------------------

def test_migration_preflight_detects_duplicates():
    # A fresh in-memory engine with a minimal fabric_rolls table WITHOUT the unique
    # index, so we can plant a duplicate and verify the pre-flight catches it.
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0017_roll_genealogy.py"
    spec = importlib.util.spec_from_file_location("mig_0017", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    eng = sa.create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(sa.text("CREATE TABLE fabric_rolls (company_id TEXT, roll_no TEXT)"))
        conn.execute(sa.text("INSERT INTO fabric_rolls VALUES ('C1','R1'),('C1','R1'),('C1','R2')"))
        dups = mod._duplicate_roll_nos(conn)
    assert len(dups) == 1
    assert dups[0][1] == "R1" and dups[0][2] == 2
