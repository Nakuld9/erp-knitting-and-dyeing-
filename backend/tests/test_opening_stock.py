"""Stage 6F-P2 — opening Yarn & Trading Fabric stock (service-level).

Covers row validation, the legacy-lot policy, dedup, company/factory isolation,
the submit/approve lifecycle composition with P1, FIFO stock creation at approval,
accounting safety (no voucher), idempotency and rollback-on-failure.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.models.accounting import Voucher
from app.models.core import Branch, Company
from app.models.hybrid import WarehouseCategory
from app.models.inventory import InventoryTransaction, ItemType, StockLayer
from app.models.manufacturing import FabricRoll
from app.models.masters import Supplier, UnitOfMeasure, Warehouse, YarnLot, YarnMaster
from app.models.opening_stock import (
    OpeningImportFile,
    OpeningLineType,
    OpeningLineValidation,
    OpeningLotPolicy,
    OpeningStockLine,
)
from app.models.trading import TradingFabricItem
from app.services import inventory
from app.services import opening as batch_svc
from app.services import opening_stock as stock_svc

ED = date(2026, 11, 15)


# ------------------------------- fixtures -----------------------------------

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
    ywh = Warehouse(company_id=cid, code="YWH", name="Yarn Store", kind="STORE",
                    category=WarehouseCategory.YARN_STORE.value, branch_id=fac.id)
    twh = Warehouse(company_id=cid, code="TWH", name="Trading WH", kind="STORE",
                    category=WarehouseCategory.TRADING.value, branch_id=fac.id)
    db.add_all([ywh, twh])
    yarn = YarnMaster(company_id=cid, code="Y1", name="Cotton 30s", uom_id=uom.id, is_active=True)
    sup = Supplier(company_id=cid, code="SUP1", name="Supplier One")
    titem = TradingFabricItem(company_id=cid, code="T1", name="Trading Jersey")
    db.add_all([yarn, sup, titem]); db.flush()
    return {"cid": cid, "uom": uom, "fac": fac, "fac2": fac2, "ywh": ywh, "twh": twh,
            "yarn": yarn, "sup": sup, "titem": titem}


def _mk_batch(db, cid, btype, factory_id=None, creator=None):
    return batch_svc.create_batch(db, cid, batch_type=btype, effective_date=ED,
                                  factory_id=factory_id, created_by=creator or uuid.uuid4())


def _import(db, batch, line_type, rows, actor=None):
    actor = actor or uuid.uuid4()
    f = OpeningImportFile(company_id=batch.company_id, batch_id=batch.id, line_type=line_type,
                          file_name="t.xlsx",
                          file_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                          content=b"xlsxbytes", uploaded_by=actor)
    db.add(f); db.flush()
    return stock_svc.import_rows(db, batch, line_type=line_type, rows=rows,
                                 import_file=f, actor=actor)


def _lines(db, batch):
    return db.execute(select(OpeningStockLine).where(
        OpeningStockLine.batch_id == batch.id)
        .order_by(OpeningStockLine.row_number)).scalars().all()


def _yrow(m, **over):
    row = {"yarn_code": "Y1", "internal_lot_no": "LOT-1", "supplier_code": None,
           "supplier_lot_no": None, "warehouse_code": "YWH", "factory_code": "FAC1",
           "quantity": "100", "uom_code": "KG", "weight_kg": "100", "unit_cost": "250",
           "total_value": "25000", "effective_date": None, "source_document": "GRN-OLD-1",
           "source_reference": "legacy", "legacy_unknown": None, "_row": 3}
    row.update(over)
    return row


def _trow(m, **over):
    row = {"trading_sku_code": "T1", "warehouse_code": "TWH", "factory_code": "FAC1",
           "quantity": "50", "uom_code": "KG", "total_weight_kg": "50", "unit_cost": "400",
           "total_value": "20000", "effective_date": None, "source_document": "INV-OLD-1",
           "source_reference": "legacy", "_row": 3}
    row.update(over)
    return row


# ------------------------------- yarn rows ----------------------------------

def test_valid_yarn_row_new_lot(db, company, masters):
    b = _mk_batch(db, company.id, "YARN")
    _import(db, b, "YARN", [_yrow(masters)])
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.VALID.value
    assert ln.lot_policy == OpeningLotPolicy.NEW_LOT.value
    assert ln.item_id == masters["yarn"].id and ln.dedup_key


def test_existing_lot_reference_preserved(db, company, masters):
    lot = YarnLot(company_id=company.id, yarn_id=masters["yarn"].id, internal_lot_no="LOT-EX",
                  warehouse_id=masters["ywh"].id, unit_cost=200)
    db.add(lot); db.flush()
    b = _mk_batch(db, company.id, "YARN")
    _import(db, b, "YARN", [_yrow(masters, internal_lot_no="LOT-EX")])
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.VALID.value
    assert ln.lot_policy == OpeningLotPolicy.EXISTING_LOT.value and ln.yarn_lot_id == lot.id


def test_supplier_lot_reference_preserved(db, company, masters):
    b = _mk_batch(db, company.id, "YARN")
    _import(db, b, "YARN", [_yrow(masters, supplier_code="SUP1", supplier_lot_no="SLOT-9")])
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.VALID.value
    assert ln.supplier_id == masters["sup"].id and ln.supplier_lot_no == "SLOT-9"


def test_unknown_supplier_rejected(db, company, masters):
    b = _mk_batch(db, company.id, "YARN")
    _import(db, b, "YARN", [_yrow(masters, supplier_code="NOPE")])
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.INVALID.value


def test_legacy_unknown_lot_policy(db, company, masters):
    b = _mk_batch(db, company.id, "YARN")
    # No lot number and not flagged legacy -> rejected.
    _import(db, b, "YARN", [_yrow(masters, internal_lot_no=None, legacy_unknown=None)])
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.INVALID.value
    # Flagged legacy -> accepted as an authorised placeholder.
    b2 = _mk_batch(db, company.id, "YARN")
    _import(db, b2, "YARN", [_yrow(masters, internal_lot_no=None, legacy_unknown="true")])
    (ln2,) = _lines(db, b2)
    assert ln2.validation_status == OpeningLineValidation.VALID.value
    assert ln2.lot_policy == OpeningLotPolicy.LEGACY_PLACEHOLDER.value


def test_wrong_warehouse_category_rejected_yarn(db, company, masters):
    b = _mk_batch(db, company.id, "YARN")
    _import(db, b, "YARN", [_yrow(masters, warehouse_code="TWH")])  # TRADING, not YARN_STORE
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.INVALID.value


def test_cross_company_yarn_rejected(db, company, masters):
    other = Company(code="OTHER", name="Other Co"); db.add(other); db.flush()
    oy = YarnMaster(company_id=other.id, code="YONLYOTHER", name="x", is_active=True)
    db.add(oy); db.flush()
    b = _mk_batch(db, company.id, "YARN")
    _import(db, b, "YARN", [_yrow(masters, yarn_code="YONLYOTHER")])
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.INVALID.value


def test_wrong_factory_rejected(db, company, masters):
    b = _mk_batch(db, company.id, "YARN")
    _import(db, b, "YARN", [_yrow(masters, factory_code="FAC2")])  # YWH is on FAC1
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.INVALID.value


def test_duplicate_lot_row_rejected(db, company, masters):
    b = _mk_batch(db, company.id, "YARN")
    _import(db, b, "YARN", [_yrow(masters, _row=3), _yrow(masters, _row=4)])  # same lot+wh
    lns = _lines(db, b)
    statuses = sorted(l.validation_status for l in lns)
    assert statuses == [OpeningLineValidation.INVALID.value, OpeningLineValidation.VALID.value]


def test_existing_stock_blocks_duplicate_opening(db, company, masters):
    lot = YarnLot(company_id=company.id, yarn_id=masters["yarn"].id, internal_lot_no="LOT-STK",
                  warehouse_id=masters["ywh"].id, unit_cost=200)
    db.add(lot); db.flush()
    inventory.receive(db, company_id=company.id, item_type=ItemType.YARN, item_id=masters["yarn"].id,
                      qty=10, unit_cost=200, lot_id=lot.id, warehouse_id=masters["ywh"].id,
                      event_type="GRN")
    db.flush()
    b = _mk_batch(db, company.id, "YARN")
    _import(db, b, "YARN", [_yrow(masters, internal_lot_no="LOT-STK")])
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.INVALID.value


def test_negative_quantity_rejected(db, company, masters):
    b = _mk_batch(db, company.id, "YARN")
    _import(db, b, "YARN", [_yrow(masters, quantity="-5")])
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.INVALID.value


# ------------------------------ trading rows --------------------------------

def test_valid_trading_row(db, company, masters):
    b = _mk_batch(db, company.id, "TRADING_FABRIC")
    _import(db, b, "TRADE_FABRIC", [_trow(masters)])
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.VALID.value
    assert ln.item_type == ItemType.TRADING_FABRIC.value and ln.yarn_lot_id is None


def test_trading_wrong_category_rejected(db, company, masters):
    b = _mk_batch(db, company.id, "TRADING_FABRIC")
    _import(db, b, "TRADE_FABRIC", [_trow(masters, warehouse_code="YWH")])
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.INVALID.value


def test_trading_cross_company_sku_rejected(db, company, masters):
    b = _mk_batch(db, company.id, "TRADING_FABRIC")
    _import(db, b, "TRADE_FABRIC", [_trow(masters, trading_sku_code="NOPE")])
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.INVALID.value


def test_trading_duplicate_sku_warehouse_rejected(db, company, masters):
    b = _mk_batch(db, company.id, "TRADING_FABRIC")
    _import(db, b, "TRADE_FABRIC", [_trow(masters, _row=3), _trow(masters, _row=4)])
    statuses = sorted(l.validation_status for l in _lines(db, b))
    assert statuses == [OpeningLineValidation.INVALID.value, OpeningLineValidation.VALID.value]


# --------------------------- lifecycle + commit -----------------------------

def _run_to_approved(db, company, masters, rows, line_type, btype):
    maker, checker = uuid.uuid4(), uuid.uuid4()
    b = _mk_batch(db, company.id, btype, creator=maker)
    _import(db, b, line_type, rows, actor=maker)
    stock_svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    batch, committed = stock_svc.approve_batch(db, company.id, b.id, approved_by=checker)
    return b, committed


def test_approve_creates_yarn_fifo_layer(db, company, masters):
    b, committed = _run_to_approved(db, company, masters, [_yrow(masters)], "YARN", "YARN")
    assert committed == 1
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.COMMITTED.value
    assert ln.stock_layer_id and ln.yarn_lot_id
    bal = inventory.balance(db, company_id=company.id, item_type=ItemType.YARN,
                            item_id=masters["yarn"].id, lot_id=ln.yarn_lot_id)
    from decimal import Decimal
    assert Decimal(str(bal["qty_on_hand"])) == Decimal("100")
    assert Decimal(str(bal["value"])) == Decimal("25000")
    # The movement is tagged as an opening, distinct from GRN/production.
    txn = db.execute(select(InventoryTransaction).where(
        InventoryTransaction.source_id == str(ln.id))).scalar_one()
    assert txn.event_type == "OPENING" and txn.source_module == "opening_yarn"


def test_approve_creates_trading_layer_no_roll(db, company, masters):
    before_rolls = db.execute(select(func.count()).select_from(FabricRoll)).scalar()
    b, committed = _run_to_approved(db, company, masters, [_trow(masters)], "TRADE_FABRIC", "TRADING_FABRIC")
    assert committed == 1
    (ln,) = _lines(db, b)
    layer = db.get(StockLayer, ln.stock_layer_id)
    assert layer.item_type == ItemType.TRADING_FABRIC.value   # 'TRADE_FABRIC'
    assert layer.item_type != ItemType.FABRIC.value
    # No manufactured roll / lot / genealogy created.
    assert db.execute(select(func.count()).select_from(FabricRoll)).scalar() == before_rolls


def test_fifo_consume_after_opening(db, company, masters):
    b, _ = _run_to_approved(db, company, masters, [_yrow(masters)], "YARN", "YARN")
    (ln,) = _lines(db, b)
    out = inventory.consume(db, company_id=company.id, item_type=ItemType.YARN,
                            item_id=masters["yarn"].id, qty=40, lot_id=ln.yarn_lot_id)
    from decimal import Decimal
    assert Decimal(str(out["total_cost"])) == Decimal("10000")  # 40 * 250


def test_lines_only_editable_in_draft(db, company, masters):
    maker = uuid.uuid4()
    b = _mk_batch(db, company.id, "YARN", creator=maker)
    _import(db, b, "YARN", [_yrow(masters)], actor=maker)
    stock_svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    with pytest.raises(stock_svc.OpeningStockStateError):
        _import(db, b, "YARN", [_yrow(masters, internal_lot_no="LOT-2")], actor=maker)


def test_submit_blocked_with_invalid_line(db, company, masters):
    maker = uuid.uuid4()
    b = _mk_batch(db, company.id, "YARN", creator=maker)
    _import(db, b, "YARN", [_yrow(masters, warehouse_code="TWH")], actor=maker)  # invalid
    with pytest.raises(stock_svc.OpeningStockStateError):
        stock_svc.submit_batch(db, company.id, b.id, submitted_by=maker)


def test_submit_blocked_with_no_lines(db, company, masters):
    maker = uuid.uuid4()
    b = _mk_batch(db, company.id, "YARN", creator=maker)
    with pytest.raises(stock_svc.OpeningStockStateError):
        stock_svc.submit_batch(db, company.id, b.id, submitted_by=maker)


def test_self_approval_blocked(db, company, masters):
    maker = uuid.uuid4()
    b = _mk_batch(db, company.id, "YARN", creator=maker)
    _import(db, b, "YARN", [_yrow(masters)], actor=maker)
    stock_svc.submit_batch(db, company.id, b.id, submitted_by=maker)
    with pytest.raises(batch_svc.SelfApprovalError):
        stock_svc.approve_batch(db, company.id, b.id, approved_by=maker)


def test_cannot_import_into_approved(db, company, masters):
    b, _ = _run_to_approved(db, company, masters, [_yrow(masters)], "YARN", "YARN")
    with pytest.raises(stock_svc.OpeningStockStateError):
        _import(db, b, "YARN", [_yrow(masters, internal_lot_no="LOT-9")])


def test_company_isolation_lines(db, company, masters):
    other = Company(code="ISO2", name="Iso Two"); db.add(other); db.flush()
    b = _mk_batch(db, company.id, "YARN")
    _import(db, b, "YARN", [_yrow(masters)])
    with pytest.raises(batch_svc.OpeningBatchNotFound):
        stock_svc.list_lines(db, other.id, b.id)


# ----------------------------- accounting safety ----------------------------

def test_no_voucher_created_through_opening(db, company, masters):
    before = db.execute(select(func.count()).select_from(Voucher)).scalar()
    _run_to_approved(db, company, masters, [_yrow(masters)], "YARN", "YARN")
    _run_to_approved(db, company, masters, [_trow(masters)], "TRADE_FABRIC", "TRADING_FABRIC")
    after = db.execute(select(func.count()).select_from(Voucher)).scalar()
    assert before == after == 0


def test_idempotent_reapprove_no_double_stock(db, company, masters):
    b, _ = _run_to_approved(db, company, masters, [_yrow(masters)], "YARN", "YARN")
    layers_before = db.execute(select(func.count()).select_from(StockLayer)).scalar()
    # Re-approving an already-APPROVED batch commits nothing new.
    batch, committed = stock_svc.approve_batch(db, company.id, b.id, approved_by=uuid.uuid4())
    assert committed == 0
    assert db.execute(select(func.count()).select_from(StockLayer)).scalar() == layers_before


def test_rollback_leaves_no_partial_stock(db, company, masters):
    # Two batches registering the SAME new lot number. The first commits; the
    # second must fail at approval (lot now has stock) and create no partial stock.
    b1, _ = _run_to_approved(db, company, masters, [_yrow(masters, internal_lot_no="LOT-DUP")],
                             "YARN", "YARN")
    maker2 = uuid.uuid4()
    b2 = _mk_batch(db, company.id, "YARN", creator=maker2)
    _import(db, b2, "YARN", [_yrow(masters, internal_lot_no="LOT-DUP")], actor=maker2)
    # The dedup/inventory pass at submit should already flag it; approval must never
    # double-count even if forced.
    with pytest.raises(stock_svc.OpeningStockError):
        stock_svc.submit_batch(db, company.id, b2.id, submitted_by=maker2)
    layers = db.execute(select(func.count()).select_from(StockLayer)).scalar()
    # Only the first batch's single layer exists.
    assert layers == 1


# ---------------- P2-FIX: company-scoped lot identity (SQLite) ---------------

def test_unique_index_blocks_direct_duplicate_lot(db, company, masters):
    """The DB unique index enforces (company_id, internal_lot_no) even on SQLite."""
    from sqlalchemy.exc import IntegrityError
    db.add(YarnLot(company_id=company.id, yarn_id=masters["yarn"].id,
                   internal_lot_no="UQ-1", unit_cost=10))
    db.flush()
    db.add(YarnLot(company_id=company.id, yarn_id=masters["yarn"].id,
                   internal_lot_no="UQ-1", unit_cost=10))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_same_lot_number_different_company_allowed(db, company, masters):
    other = Company(code="UQOTHER", name="Other"); db.add(other); db.flush()
    db.add(YarnLot(company_id=company.id, yarn_id=masters["yarn"].id,
                   internal_lot_no="SHARED", unit_cost=10))
    oy = YarnMaster(company_id=other.id, code="OY", name="OY", is_active=True)
    db.add(oy); db.flush()
    db.add(YarnLot(company_id=other.id, yarn_id=oy.id, internal_lot_no="SHARED", unit_cost=10))
    db.flush()  # no conflict across companies
    assert db.execute(select(func.count()).select_from(YarnLot).where(
        YarnLot.internal_lot_no == "SHARED")).scalar() == 2


def test_cross_yarn_lot_number_rejected(db, company, masters):
    # A lot number already used by a different yarn SKU in the company is rejected
    # (the lot number is the company-scoped identity, matching the unique index).
    other_yarn = YarnMaster(company_id=company.id, code="Y2", name="Poly 40s", is_active=True)
    db.add(other_yarn)
    db.flush()  # assign other_yarn.id before referencing it
    db.add(YarnLot(company_id=company.id, yarn_id=other_yarn.id, internal_lot_no="XREF",
                   warehouse_id=masters["ywh"].id, unit_cost=10))
    db.flush()
    b = _mk_batch(db, company.id, "YARN")
    _import(db, b, "YARN", [_yrow(masters, internal_lot_no="XREF")])  # same number, yarn Y1
    (ln,) = _lines(db, b)
    assert ln.validation_status == OpeningLineValidation.INVALID.value


def test_sequential_duplicate_new_lot_blocked_at_submit(db, company, masters):
    # Batch 1 commits lot "SEQ"; batch 2 importing the same new lot is flagged
    # invalid (lot now has stock) and cannot be submitted — the deterministic
    # sequential guard that complements the DB index under concurrency.
    _run_to_approved(db, company, masters, [_yrow(masters, internal_lot_no="SEQ")], "YARN", "YARN")
    maker = uuid.uuid4()
    b2 = _mk_batch(db, company.id, "YARN", creator=maker)
    _import(db, b2, "YARN", [_yrow(masters, internal_lot_no="SEQ")], actor=maker)
    (ln,) = _lines(db, b2)
    assert ln.validation_status == OpeningLineValidation.INVALID.value
    with pytest.raises(stock_svc.OpeningStockStateError):
        stock_svc.submit_batch(db, company.id, b2.id, submitted_by=maker)
