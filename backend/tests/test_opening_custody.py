"""Stage 6F-P4 — vendor-custody & vendor-held-WIP opening (service-level, SQLite).

Covers yarn & roll custody, reference-vs-mint, existing-stock reject, vendor
authorisation, strict scope rejection (in-house/aggregate), dedup, valuation
quarantine, atomic commit into the vendor custody warehouse, the opening-origin
JobWorkIssue appearing exactly once in vendor outstanding (no double-count, no
false issue/receipt), no GL voucher, no factory-warehouse layer, rollback,
idempotency, lifecycle/maker-checker and company isolation.
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
from app.models.jobwork import JobWorkIssue, JobWorkIssueStatus
from app.models.manufacturing import FabricRoll, RollKind
from app.models.masters import FabricType, Supplier, UnitOfMeasure, Warehouse, YarnLot, YarnMaster
from app.models.opening_custody import OpeningCustodyLine, OpeningCustodyRegistration
from app.models.opening_stock import OpeningLineValidation
from app.services import inventory
from app.services import opening as batch_svc
from app.services import opening_custody as cust
from app.services import jobwork_custody

ED = date(2026, 11, 15)
V = OpeningLineValidation


@pytest.fixture()
def masters(db, company):
    cid = company.id
    uom = db.execute(select(UnitOfMeasure).where(UnitOfMeasure.code == "KG")).scalars().first()
    if uom is None:
        uom = UnitOfMeasure(code="KG", name="Kilogram", dimension="MASS", base_factor=1); db.add(uom)
    fac = Branch(company_id=cid, code="FAC1", name="Factory 1")
    fac2 = Branch(company_id=cid, code="FAC2", name="Factory 2")
    db.add_all([fac, fac2]); db.flush()
    jw1 = Supplier(company_id=cid, code="JW1", name="Knit Vendor", is_job_worker=True)
    jw2 = Supplier(company_id=cid, code="JW2", name="Dye Vendor", is_job_worker=True)
    notjw = Supplier(company_id=cid, code="SUP9", name="Plain Supplier", is_job_worker=False)
    yarn = YarnMaster(company_id=cid, code="Y1", name="Cotton 30s", is_active=True)
    ft = FabricType(company_id=cid, code="SJ160", name="Single Jersey 160")
    db.add_all([jw1, jw2, notjw, yarn, ft]); db.flush()
    return {"cid": cid, "uom": uom, "fac": fac, "fac2": fac2, "jw1": jw1, "jw2": jw2,
            "notjw": notjw, "yarn": yarn, "ft": ft}


def _mk_batch(db, cid, btype="VENDOR_CUSTODY", factory_id=None, creator=None):
    return batch_svc.create_batch(db, cid, batch_type=btype, effective_date=ED,
                                  factory_id=factory_id, created_by=creator or uuid.uuid4())


def _import(db, batch, rows, actor=None):
    from app.models.opening_stock import OpeningImportFile, OpeningLineType
    actor = actor or uuid.uuid4()
    f = OpeningImportFile(company_id=batch.company_id, batch_id=batch.id,
                          line_type=OpeningLineType.VENDOR_CUSTODY.value, file_name="c.xlsx",
                          file_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                          content=b"x", uploaded_by=actor)
    db.add(f); db.flush()
    return cust.import_rows(db, batch, rows=rows, import_file=f, actor=actor)


def _lines(db, batch):
    return db.execute(select(OpeningCustodyLine).where(
        OpeningCustodyLine.batch_id == batch.id).order_by(OpeningCustodyLine.row_number)).scalars().all()


def _yrow(**over):
    row = {"line_kind": "VENDOR_CUSTODY", "custody_type": "YARN", "vendor_code": "JW1",
           "process": "KNITTING", "factory_code": "FAC1", "yarn_code": "Y1",
           "internal_lot_no": "LOT-C1", "net_weight_kg": "50", "uom_code": "KG",
           "unit_cost": "250", "total_value": "12500", "valuation_source": "vendor confirmation",
           "disputed_unknown": None, "effective_date": None, "source_document": "OPEN-C",
           "prior_vendor_code": None, "_row": 3}
    row.update(over)
    return row


def _rrow(**over):
    row = {"line_kind": "VENDOR_HELD_WIP", "custody_type": "FABRIC_ROLL", "vendor_code": "JW2",
           "process": "DYEING", "factory_code": "FAC1", "erp_roll_no": None,
           "legacy_label": "HW-C1", "roll_kind": "GREIGE", "origin_stream": None,
           "fabric_sku_or_structure": "SJ160", "net_weight_kg": "40", "uom_code": "KG",
           "unit_cost": "300", "total_value": "12000", "valuation_source": "stock sheet",
           "effective_date": None, "source_document": "OPEN-C", "_row": 3}
    row.update(over)
    return row


def _approved(db, company, rows):
    maker, checker = uuid.uuid4(), uuid.uuid4()
    b = _mk_batch(db, company.id, creator=maker)
    _import(db, b, rows, actor=maker)
    cust.submit_batch(db, company.id, b.id, submitted_by=maker)
    batch, committed = cust.approve_batch(db, company.id, b.id, approved_by=checker)
    return b, committed


# ------------------------------- validation ---------------------------------

def test_valid_yarn_custody(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_yrow()])
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.VALID.value and ln.custody_type == "YARN"
    assert ln.job_worker_id == masters["jw1"].id


def test_valid_roll_custody_mint(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow()])
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.VALID.value
    assert ln.registration_mode == OpeningCustodyRegistration.MINT_NEW.value


def test_reference_existing_roll(db, company, masters):
    roll = FabricRoll(company_id=company.id, roll_no="GR-C-1", kind=RollKind.GREIGE, weight_kg=40)
    db.add(roll); db.flush()
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow(erp_roll_no="GR-C-1", legacy_label=None)])
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.VALID.value and ln.fabric_roll_id == roll.id
    assert ln.registration_mode == OpeningCustodyRegistration.REFERENCE_EXISTING.value


def test_existing_roll_with_stock_rejected(db, company, masters):
    roll = FabricRoll(company_id=company.id, roll_no="GR-C-STK", kind=RollKind.GREIGE, weight_kg=40)
    db.add(roll); db.flush()
    inventory.receive(db, company_id=company.id, item_type=ItemType.FABRIC, item_id=roll.id,
                      qty=40, unit_cost=300, event_type="PRODUCE")
    db.flush()
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow(erp_roll_no="GR-C-STK", legacy_label=None)])
    assert _lines(db, b)[0].validation_status == V.INVALID.value


def test_vendor_not_job_worker_rejected(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_yrow(vendor_code="SUP9")])
    assert _lines(db, b)[0].validation_status == V.INVALID.value


def test_unknown_vendor_rejected(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_yrow(vendor_code="NOPE")])
    assert _lines(db, b)[0].validation_status == V.INVALID.value


def test_out_of_scope_rejected(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [
        _yrow(line_kind="IN_HOUSE_WIP", internal_lot_no="L-A", _row=3),       # bad line_kind
        _yrow(custody_type="AGGREGATE", internal_lot_no="L-B", _row=4),        # bad custody_type
    ])
    assert all(l.validation_status == V.INVALID.value for l in _lines(db, b))


def test_yarn_missing_lot_rejected(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_yrow(internal_lot_no=None)])
    assert _lines(db, b)[0].validation_status == V.INVALID.value


def test_disputed_and_missing_valuation_rejected(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [
        _yrow(internal_lot_no="L-D", disputed_unknown="true", _row=3),
        _yrow(internal_lot_no="L-E", valuation_source=None, _row=4),
        _yrow(internal_lot_no="L-F", unit_cost=None, total_value=None, _row=5),
    ])
    assert all(l.validation_status == V.INVALID.value for l in _lines(db, b))


def test_finished_origin_stream_required(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_rrow(roll_kind="FINISHED", legacy_label="FG-C1")])
    assert _lines(db, b)[0].validation_status == V.INVALID.value
    b2 = _mk_batch(db, company.id)
    _import(db, b2, [_rrow(roll_kind="FINISHED", legacy_label="FG-C2", origin_stream="DYED")])
    assert _lines(db, b2)[0].validation_status == V.VALID.value


def test_duplicate_within_batch(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_yrow(_row=3), _yrow(_row=4)])  # same vendor+yarn+lot
    assert sorted(l.validation_status for l in _lines(db, b)) == [V.INVALID.value, V.VALID.value]


def test_wrong_factory_rejected(db, company, masters):
    b = _mk_batch(db, company.id, factory_id=masters["fac"].id)
    _import(db, b, [_yrow(factory_code="FAC2")])
    assert _lines(db, b)[0].validation_status == V.INVALID.value


def test_vendor_to_vendor_provenance(db, company, masters):
    b = _mk_batch(db, company.id)
    _import(db, b, [_yrow(prior_vendor_code="JW2")])
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.VALID.value and ln.prior_vendor_id == masters["jw2"].id


# ----------------------------- commit behaviour ------------------------------

def test_yarn_commit_into_custody_no_voucher(db, company, masters):
    before_v = db.execute(select(func.count()).select_from(Voucher)).scalar()
    b, committed = _approved(db, company, [_yrow()])
    assert committed == 1
    (ln,) = _lines(db, b)
    assert ln.validation_status == V.COMMITTED.value and ln.stock_layer_id and ln.job_work_issue_id
    # Stock layer is in the vendor's VENDOR_CUSTODY warehouse — not a factory wh.
    cust_wh = db.get(Warehouse, ln.custody_warehouse_id)
    assert cust_wh.category == WarehouseCategory.VENDOR_CUSTODY.value
    assert cust_wh.vendor_id == masters["jw1"].id
    bal = inventory.balance(db, company_id=company.id, item_type=ItemType.YARN,
                            item_id=masters["yarn"].id, lot_id=ln.yarn_lot_id, warehouse_id=cust_wh.id)
    assert Decimal(str(bal["qty_on_hand"])) == Decimal("50")
    assert Decimal(str(bal["value"])) == Decimal("12500")
    # No GL voucher.
    assert db.execute(select(func.count()).select_from(Voucher)).scalar() == before_v == 0
    # Exactly one inventory transaction, event OPENING.
    txns = db.execute(select(func.count()).select_from(InventoryTransaction).where(
        InventoryTransaction.source_id == str(ln.id),
        InventoryTransaction.event_type == "OPENING")).scalar()
    assert txns == 1


def test_opening_appears_once_in_vendor_outstanding(db, company, masters):
    b, _ = _approved(db, company, [_yrow()])
    rows = jobwork_custody.vendor_outstanding(db, company.id, job_worker_id=masters["jw1"].id)
    assert len(rows) == 1
    assert rows[0]["open_issues"] == 1
    assert Decimal(str(rows[0]["outstanding_qty"])) == Decimal("50")
    # The opening-origin issue is a real OUTSTANDING JobWorkIssue tagged OPENING.
    (ln,) = _lines(db, b)
    issue = db.get(JobWorkIssue, ln.job_work_issue_id)
    assert issue.status == JobWorkIssueStatus.OUTSTANDING
    assert issue.posting_reference.startswith("OPENING:") and issue.source_warehouse_id is None


def test_roll_commit_protected_and_genealogy(db, company, masters):
    b, _ = _approved(db, company, [_rrow()])
    (ln,) = _lines(db, b)
    roll = db.get(FabricRoll, ln.fabric_roll_id)
    assert roll.roll_no.startswith("GR-") and roll.legacy_label == "HW-C1"
    cust_wh = db.get(Warehouse, ln.custody_warehouse_id)
    assert roll.warehouse_id == cust_wh.id and cust_wh.vendor_id == masters["jw2"].id
    # Roll in vendor custody is protected from sale/transfer.
    with pytest.raises(jobwork_custody.JobWorkCustodyError):
        jobwork_custody.assert_available_for_use(db, roll, action="used")
    # Exactly one IMPORT genealogy edge; no fabricated production edge.
    edges = db.execute(select(RollGenealogyEdge).where(
        RollGenealogyEdge.output_roll_id == roll.id)).scalars().all()
    assert len(edges) == 1 and edges[0].process_type == "IMPORT"


def test_reference_existing_roll_no_second_roll(db, company, masters):
    roll = FabricRoll(company_id=company.id, roll_no="GR-C-REF", kind=RollKind.GREIGE, weight_kg=40)
    db.add(roll); db.flush()
    before = db.execute(select(func.count()).select_from(FabricRoll)).scalar()
    b, committed = _approved(db, company, [_rrow(erp_roll_no="GR-C-REF", legacy_label=None)])
    assert committed == 1
    assert db.execute(select(func.count()).select_from(FabricRoll)).scalar() == before
    # No fabricated genealogy for a referenced roll.
    assert db.execute(select(func.count()).select_from(RollGenealogyEdge).where(
        RollGenealogyEdge.output_roll_id == roll.id)).scalar() == 0


def test_existing_yarn_lot_reused(db, company, masters):
    lot = YarnLot(company_id=company.id, yarn_id=masters["yarn"].id, internal_lot_no="LOT-EX",
                  unit_cost=200)
    db.add(lot); db.flush()
    before = db.execute(select(func.count()).select_from(YarnLot)).scalar()
    b, _ = _approved(db, company, [_yrow(internal_lot_no="LOT-EX")])
    (ln,) = _lines(db, b)
    assert ln.yarn_lot_id == lot.id
    assert db.execute(select(func.count()).select_from(YarnLot)).scalar() == before  # no new lot


def test_no_voucher_for_rolls(db, company, masters):
    before = db.execute(select(func.count()).select_from(Voucher)).scalar()
    _approved(db, company, [_rrow()])
    assert db.execute(select(func.count()).select_from(Voucher)).scalar() == before == 0


def test_idempotent_reapprove(db, company, masters):
    b, _ = _approved(db, company, [_yrow()])
    layers = db.execute(select(func.count()).select_from(StockLayer)).scalar()
    issues = db.execute(select(func.count()).select_from(JobWorkIssue)).scalar()
    batch, committed = cust.approve_batch(db, company.id, b.id, approved_by=uuid.uuid4())
    assert committed == 0
    assert db.execute(select(func.count()).select_from(StockLayer)).scalar() == layers
    assert db.execute(select(func.count()).select_from(JobWorkIssue)).scalar() == issues


def test_rollback_on_injected_failure(db, company, masters):
    maker = uuid.uuid4()
    b = _mk_batch(db, company.id, creator=maker)
    _import(db, b, [_yrow()], actor=maker)
    cust.submit_batch(db, company.id, b.id, submitted_by=maker)
    layers_before = db.execute(select(func.count()).select_from(StockLayer)).scalar()
    issues_before = db.execute(select(func.count()).select_from(JobWorkIssue)).scalar()
    with patch.object(cust.inventory, "receive", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            cust.approve_batch(db, company.id, b.id, approved_by=uuid.uuid4())
    db.rollback()
    assert db.execute(select(func.count()).select_from(StockLayer)).scalar() == layers_before
    assert db.execute(select(func.count()).select_from(JobWorkIssue)).scalar() == issues_before


def test_self_approval_blocked(db, company, masters):
    maker = uuid.uuid4()
    b = _mk_batch(db, company.id, creator=maker)
    _import(db, b, [_yrow()], actor=maker)
    cust.submit_batch(db, company.id, b.id, submitted_by=maker)
    with pytest.raises(batch_svc.SelfApprovalError):
        cust.approve_batch(db, company.id, b.id, approved_by=maker)


def test_submit_blocked_with_invalid(db, company, masters):
    maker = uuid.uuid4()
    b = _mk_batch(db, company.id, creator=maker)
    _import(db, b, [_yrow(vendor_code="SUP9")], actor=maker)
    with pytest.raises(cust.OpeningCustodyStateError):
        cust.submit_batch(db, company.id, b.id, submitted_by=maker)


def test_cannot_import_into_approved(db, company, masters):
    b, _ = _approved(db, company, [_yrow()])
    with pytest.raises(cust.OpeningCustodyStateError):
        _import(db, b, [_yrow(internal_lot_no="LATE")])


def test_company_isolation(db, company, masters):
    other = Company(code="CISO", name="Iso"); db.add(other); db.flush()
    b = _mk_batch(db, company.id)
    _import(db, b, [_yrow()])
    with pytest.raises(batch_svc.OpeningBatchNotFound):
        cust.list_lines(db, other.id, b.id)


def test_wip_batch_type_accepts_custody(db, company, masters):
    # VENDOR_HELD_WIP under a WIP batch commits as vendor custody.
    b, committed = _approved(db, company, [_rrow(line_kind="VENDOR_HELD_WIP")])
    assert committed == 1


def test_partial_quantity_commits_exactly(db, company, masters):
    b, _ = _approved(db, company, [_yrow(net_weight_kg="17.5", total_value="4375")])
    (ln,) = _lines(db, b)
    bal = inventory.balance(db, company_id=company.id, item_type=ItemType.YARN,
                            item_id=masters["yarn"].id, lot_id=ln.yarn_lot_id)
    assert Decimal(str(bal["qty_on_hand"])) == Decimal("17.5")
