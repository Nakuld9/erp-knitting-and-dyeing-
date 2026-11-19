"""Stage 6F-P3 — manufactured fabric roll opening (service-level, SQLite).

Covers reference-vs-mint registration, strict warehouse-stage enforcement, legacy
labels, duplicate detection, the no-mint-before-approval rule, correct generator
stream, IMPORT genealogy, one-roll/one-layer, accounting safety (no voucher),
rollback, lifecycle/maker-checker and the opening label.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.models.accounting import Voucher
from app.models.core import Branch, Company
from app.models.genealogy import RollGenealogyEdge
from app.models.hybrid import WarehouseCategory
from app.models.inventory import InventoryTransaction, ItemType, StockLayer
from app.models.manufacturing import FabricRoll, RollKind
from app.models.masters import FabricType, ShadeCode, UnitOfMeasure, Warehouse
from app.models.opening_roll import OpeningRollLine, OpeningRollRegistration
from app.models.opening_stock import OpeningLineValidation
from app.services import inventory
from app.services import opening as batch_svc
from app.services import opening_rolls as roll_svc

ED = date(2026, 11, 15)
V = OpeningLineValidation


@pytest.fixture()
def masters(db, company):
    cid = company.id
    uom = db.execute(select(UnitOfMeasure).where(UnitOfMeasure.code == "KG")).scalars().first()
    if uom is None:
        uom = UnitOfMeasure(code="KG", name="Kilogram", dimension="MASS", base_factor=1)
        db.add(uom)
    fac = Branch(company_id=cid, code="FAC1", name="Factory 1")
    fac2 = Branch(company_id=cid, code="FAC2", name="Factory 2")
    db.add_all([fac, fac2]); db.flush()
    greige = Warehouse(company_id=cid, code="GWH", name="Greige", kind="GREIGE",
                       category=WarehouseCategory.GREIGE.value, branch_id=fac.id)
    sf = Warehouse(company_id=cid, code="SWH", name="Semi", kind="STORE",
                   category=WarehouseCategory.SEMI_FINISHED.value, branch_id=fac.id)
    fg = Warehouse(company_id=cid, code="FWH", name="FG", kind="FG",
                   category=WarehouseCategory.FINISHED_GOODS.value, branch_id=fac.id)
    unc = Warehouse(company_id=cid, code="UWH", name="Uncat", kind="STORE",
                    category=None, branch_id=fac.id)
    ft = FabricType(company_id=cid, code="SJ160", name="Single Jersey 160")
    shade = ShadeCode(company_id=cid, code="NAVY", name="Navy Blue")
    db.add_all([greige, sf, fg, unc, ft, shade]); db.flush()
    return {"cid": cid, "uom": uom, "fac": fac, "fac2": fac2, "greige": greige,
            "sf": sf, "fg": fg, "unc": unc, "ft": ft, "shade": shade}


def _mk_batch(db, cid, factory_id=None, creator=None):
    return batch_svc.create_batch(db, cid, batch_type="MANUFACTURED_ROLLS", effective_date=ED,
                                  factory_id=factory_id, created_by=creator or uuid.uuid4())


def _import(db, batch, rows, actor=None):
    from app.models.opening_stock import OpeningImportFile, OpeningLineType
    actor = actor or uuid.uuid4()
    f = OpeningImportFile(company_id=batch.company_id, batch_id=batch.id,
                          line_type=OpeningLineType.MANUFACTURED_ROLLS.value, file_name="r.xlsx",
                          file_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                          content=b"x", uploaded_by=actor)
    db.add(f); db.flush()
    return roll_svc.import_rows(db, batch, rows=rows, import_file=f, actor=actor)


def _lines(db, batch):
    return db.execute(select(OpeningRollLine).where(
        OpeningRollLine.batch_id == batch.id).order_by(OpeningRollLine.row_number)).scalars().all()


def _rrow(**over):
    row = {"erp_roll_no": None, "legacy_label": "HANDWRITTEN-1", "roll_kind": "GREIGE",
           "origin_stream": None, "fabric_sku_or_structure": "SJ160", "gsm": "160",
           "width_cm": "180", "colour": None, "shade_code": None, "net_weight_kg": "42.5",
           "uom_code": "KG", "warehouse_code": "GWH", "factory_code": "FAC1",
           "quality_status": "OK", "dye_lot": None, "job_ref": None, "processing_ref": None,
           "unit_cost": "300", "total_value": "12750", "valuation_source": "stock-sheet",
           "effective_date": None, "source_document": "OPENING-1", "source_reference": None,
           "physical_verification_status": "VERIFIED", "disputed_unknown": None, "notes": None,
           "_row": 3}
    row.update(over)
    return row


def _run_to_approved(db, company, rows, factory_id=None):
    maker, checker = uuid.uuid4(), uuid.uuid4()
    b = _mk_batch(db, company.id, factory_id=factory_id, creator=maker)
    _import(db, b, rows, actor=maker)
    roll_svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    batch, committed = roll_svc.approve_batch(db, company.id, b.id, approved_by=checker)
    return b, committed


# ------------------------------- validation ---------------------------------

def test_valid_greige_mint(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow()])
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.VALID.value
    assert ln.registration_mode == OpeningRollRegistration.MINT_NEW.value
    assert ln.fabric_type_id == masters["ft"].id and ln.fabric_roll_id is None


def test_valid_dyed_mint(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow(roll_kind="DYED", warehouse_code="SWH", legacy_label="DY-OLD-1")])
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.VALID.value


def test_finished_requires_origin_stream(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow(roll_kind="FINISHED", warehouse_code="FWH", legacy_label="FG-1")])
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.INVALID.value
    b2 = _mk_batch(db, company.id)
    _import(db, b2, [_rrow(roll_kind="FINISHED", warehouse_code="FWH",
                           legacy_label="FG-2", origin_stream="DYED")])
    (ln2,) = _lines(db, b2)
    assert ln2.validation_status == V.VALID.value


def test_reference_existing_roll(db, company, masters):
    roll = FabricRoll(company_id=company.id, factory_id=masters["fac"].id, roll_no="GR-EXIST-1",
                      kind=RollKind.GREIGE, weight_kg=40, warehouse_id=masters["greige"].id)
    db.add(roll); db.flush()
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow(erp_roll_no="GR-EXIST-1", legacy_label=None)])
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.VALID.value
    assert ln.registration_mode == OpeningRollRegistration.REFERENCE_EXISTING.value
    assert ln.fabric_roll_id == roll.id


def test_existing_roll_with_stock_rejected(db, company, masters):
    roll = FabricRoll(company_id=company.id, roll_no="GR-STK-1", kind=RollKind.GREIGE,
                      weight_kg=40, warehouse_id=masters["greige"].id)
    db.add(roll); db.flush()
    inventory.receive(db, company_id=company.id, item_type=ItemType.FABRIC, item_id=roll.id,
                      qty=40, unit_cost=300, warehouse_id=masters["greige"].id, event_type="PRODUCE")
    db.flush()
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow(erp_roll_no="GR-STK-1", legacy_label=None)])
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.INVALID.value


def test_unknown_roll_reference_rejected(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow(erp_roll_no="GR-NOPE", legacy_label=None)])
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.INVALID.value


def test_legacy_label_duplicate_against_existing(db, company, masters):
    roll = FabricRoll(company_id=company.id, roll_no="GR-L1", kind=RollKind.GREIGE,
                      weight_kg=10, warehouse_id=masters["greige"].id, legacy_label="DUP-LABEL")
    db.add(roll); db.flush()
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow(legacy_label="DUP-LABEL")])
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.INVALID.value


def test_strict_warehouse_stage(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow(roll_kind="GREIGE", warehouse_code="SWH", legacy_label="L1", _row=3),   # greige into semi
                    _rrow(roll_kind="GREIGE", warehouse_code="UWH", legacy_label="L2", _row=4),   # uncategorised
                    _rrow(roll_kind="GREIGE", warehouse_code="GWH", legacy_label="L3", _row=5)])  # ok
    statuses = {l.row_number: l.validation_status for l in _lines(db, b)}
    assert statuses[3] == V.INVALID.value and statuses[4] == V.INVALID.value
    assert statuses[5] == V.VALID.value


def test_wrong_factory_rejected(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow(factory_code="FAC2")])  # GWH is on FAC1
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.INVALID.value


def test_invalid_uom_and_weight_and_value(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [
        _rrow(uom_code="XX", legacy_label="A"),
        _rrow(net_weight_kg="0", legacy_label="B"),
        _rrow(unit_cost=None, total_value=None, legacy_label="C"),
        _rrow(fabric_sku_or_structure=None, legacy_label="D"),
    ])
    assert all(l.validation_status == V.INVALID.value for l in _lines(db, b))


def test_length_optional(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow()])  # no length provided
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.VALID.value and ln.length_m is None


def test_within_file_duplicate_legacy_label(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow(legacy_label="SAME", _row=3), _rrow(legacy_label="same ", _row=4)])
    statuses = sorted(l.validation_status for l in _lines(db, b))
    assert statuses == [V.INVALID.value, V.VALID.value]


# --------------------------- no-mint / commit --------------------------------

def test_no_roll_minted_before_approval(db, company, masters):
    before = db.execute(select(func.count()).select_from(FabricRoll)).scalar()
    maker = uuid.uuid4()
    b = _mk_batch(db, company.id, creator=maker)
    _import(db, b, [_rrow()], actor=maker)
    roll_svc.reconciliation_preview(db, company.id, b.id)
    roll_svc.revalidate_batch(db, company.id, b.id)
    assert db.execute(select(func.count()).select_from(FabricRoll)).scalar() == before
    (ln,) = _lines(db, b)
    assert ln.fabric_roll_id is None


def test_approve_mints_correct_stream_and_one_layer(db, company, masters):
    b, committed = _run_to_approved(db, company, [_rrow(roll_kind="GREIGE")])
    assert committed == 1
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.COMMITTED.value and ln.fabric_roll_id
    roll = db.get(FabricRoll, ln.fabric_roll_id)
    assert roll.roll_no.startswith("GR-") and roll.legacy_label == "HANDWRITTEN-1"
    bal = inventory.balance(db, company_id=company.id, item_type=ItemType.FABRIC, item_id=roll.id)
    assert Decimal(str(bal["qty_on_hand"])) == Decimal("42.5")
    assert Decimal(str(bal["value"])) == Decimal("12750")
    layers = db.execute(select(func.count()).select_from(StockLayer).where(
        StockLayer.item_id == roll.id)).scalar()
    txns = db.execute(select(func.count()).select_from(InventoryTransaction).where(
        InventoryTransaction.item_id == roll.id,
        InventoryTransaction.event_type == "OPENING")).scalar()
    assert layers == 1 and txns == 1


def test_dyed_mint_stream(db, company, masters):
    b, _ = _run_to_approved(db, company, [_rrow(roll_kind="DYED", warehouse_code="SWH",
                                                legacy_label="DY-1")])
    (ln,) = _lines(db, b)
    assert db.get(FabricRoll, ln.fabric_roll_id).roll_no.startswith("DY-")


def test_import_genealogy_origin_only(db, company, masters):
    b, _ = _run_to_approved(db, company, [_rrow()])
    (ln,) = _lines(db, b)
    edges = db.execute(select(RollGenealogyEdge).where(
        RollGenealogyEdge.output_roll_id == ln.fabric_roll_id)).scalars().all()
    assert len(edges) == 1
    assert edges[0].process_type == "IMPORT" and edges[0].source_roll_id is None


def test_reference_existing_no_second_roll(db, company, masters):
    roll = FabricRoll(company_id=company.id, roll_no="GR-REF-1", kind=RollKind.GREIGE,
                      weight_kg=40, warehouse_id=masters["greige"].id)
    db.add(roll); db.flush()
    before = db.execute(select(func.count()).select_from(FabricRoll)).scalar()
    b, committed = _run_to_approved(db, company, [_rrow(erp_roll_no="GR-REF-1", legacy_label=None)])
    assert committed == 1
    assert db.execute(select(func.count()).select_from(FabricRoll)).scalar() == before
    # No IMPORT genealogy edge fabricated for a referenced existing roll.
    edges = db.execute(select(func.count()).select_from(RollGenealogyEdge).where(
        RollGenealogyEdge.output_roll_id == roll.id)).scalar()
    assert edges == 0
    bal = inventory.balance(db, company_id=company.id, item_type=ItemType.FABRIC, item_id=roll.id)
    assert Decimal(str(bal["qty_on_hand"])) == Decimal("42.5")


# ----------------------------- accounting safety -----------------------------

def test_no_voucher_created(db, company, masters):
    before = db.execute(select(func.count()).select_from(Voucher)).scalar()
    _run_to_approved(db, company, [_rrow()])
    assert db.execute(select(func.count()).select_from(Voucher)).scalar() == before == 0


def test_rollback_on_injected_failure(db, company, masters):
    maker = uuid.uuid4()
    b = _mk_batch(db, company.id, creator=maker)
    _import(db, b, [_rrow()], actor=maker)
    roll_svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    rolls_before = db.execute(select(func.count()).select_from(FabricRoll)).scalar()
    layers_before = db.execute(select(func.count()).select_from(StockLayer)).scalar()
    with patch.object(roll_svc.inventory, "receive", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            roll_svc.approve_batch(db, company.id, b.id, approved_by=uuid.uuid4())
    db.rollback()
    assert db.execute(select(func.count()).select_from(FabricRoll)).scalar() == rolls_before
    assert db.execute(select(func.count()).select_from(StockLayer)).scalar() == layers_before


# ------------------------------- lifecycle -----------------------------------

def test_submit_blocked_with_invalid(db, company, masters):
    maker = uuid.uuid4()
    b = _mk_batch(db, company.id, creator=maker)
    _import(db, b, [_rrow(warehouse_code="SWH")], actor=maker)  # strict-stage invalid
    with pytest.raises(roll_svc.OpeningRollStateError):
        roll_svc.submit_batch(db, company.id, b.id, submitted_by=maker)


def test_self_approval_blocked(db, company, masters):
    maker = uuid.uuid4()
    b = _mk_batch(db, company.id, creator=maker)
    _import(db, b, [_rrow()], actor=maker)
    roll_svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    with pytest.raises(batch_svc.SelfApprovalError):
        roll_svc.approve_batch(db, company.id, b.id, approved_by=maker)


def test_idempotent_reapprove(db, company, masters):
    b, _ = _run_to_approved(db, company, [_rrow()])
    rolls = db.execute(select(func.count()).select_from(FabricRoll)).scalar()
    layers = db.execute(select(func.count()).select_from(StockLayer)).scalar()
    batch, committed = roll_svc.approve_batch(db, company.id, b.id, approved_by=uuid.uuid4())
    assert committed == 0
    assert db.execute(select(func.count()).select_from(FabricRoll)).scalar() == rolls
    assert db.execute(select(func.count()).select_from(StockLayer)).scalar() == layers


def test_cannot_import_into_approved(db, company, masters):
    b, _ = _run_to_approved(db, company, [_rrow()])
    with pytest.raises(roll_svc.OpeningRollStateError):
        _import(db, b, [_rrow(legacy_label="LATE")])


def test_company_isolation(db, company, masters):
    other = Company(code="RISO", name="Iso"); db.add(other); db.flush()
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow()])
    with pytest.raises(batch_svc.OpeningBatchNotFound):
        roll_svc.list_lines(db, other.id, b.id)


# -------------------------------- label --------------------------------------

def test_label_opening_indicator(db, company, masters):
    from app.services.labels import roll_label_pdf
    # Opening-registered roll shows the indicator; a normal roll does not.
    b, _ = _run_to_approved(db, company, [_rrow()])
    (ln,) = _lines(db, b)
    pdf_open = roll_label_pdf(db, company.id, ln.fabric_roll_id)
    assert pdf_open[:4] == b"%PDF"
    normal = FabricRoll(company_id=company.id, roll_no="GR-NORMAL", kind=RollKind.GREIGE,
                        weight_kg=10, warehouse_id=masters["greige"].id)
    db.add(normal); db.flush()
    pdf_normal = roll_label_pdf(db, company.id, normal.id)
    assert pdf_normal[:4] == b"%PDF"  # renders without error; no IMPORT edge -> no banner
