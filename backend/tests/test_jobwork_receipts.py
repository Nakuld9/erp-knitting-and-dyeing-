"""Stage 3D — job-work receipts, partial receipts & reconciliation."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.accounting import Voucher
from app.models.core import Company
from app.models.genealogy import RollGenealogyEdge
from app.models.inventory import ItemType, StockLayer
from app.models.jobwork import JobWorkIssue, JobWorkIssueStatus, JobWorkReceipt
from app.models.manufacturing import FabricRoll, KnittingProduction, RollKind
from app.models.masters import Supplier, Warehouse
from app.services import jobwork_custody as cust
from app.services import order_lifecycle as ol
from app.services.hybrid import update_process_mode
from app.services.inventory import service as inventory
from app.services.inventory.service import InsufficientStockError
from app.services.manufacturing.knitting import record_production

WHEN = date(2026, 11, 7)


def _worker(db, demo):
    s = db.get(Supplier, demo["supplier_id"]); s.is_job_worker = True; db.flush(); return s


def _wh(db, demo, code, category):
    w = Warehouse(company_id=demo["company_id"], code=code, name=code, kind="STORE", category=category)
    db.add(w); db.flush(); return w


def _greige_roll(db, demo, number, kg="250"):
    kp = KnittingProduction(company_id=demo["company_id"], number=number, machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], yarn_lot_id=demo["yarn_lot_id"],
                            fabric_type_id=demo["fabric_type_id"], production_date=WHEN,
                            produced_kg=Decimal(kg), yarn_consumed_kg=Decimal("255"))
    db.add(kp); db.flush()
    return record_production(db, kp, [{"weight_kg": kg, "length_m": "900",
                                       "warehouse_id": demo["warehouses"]["greige"]}])["rolls"][0]


def _issue(db, demo, roll, *, process, qty=None):
    toggle = {"DYEING": "dye_outsourced", "FINISHING": "finish_outsourced", "KNITTING": "knit_outsourced"}[process]
    update_process_mode(db, demo["company_id"], {toggle: True})
    _worker(db, demo)
    return cust.issue_to_vendor(db, company_id=demo["company_id"], job_worker_id=demo["supplier_id"],
                                process=process, item_type=ItemType.FABRIC, item_id=roll.id,
                                qty=qty or Decimal(str(roll.weight_kg)), source_warehouse_id=roll.warehouse_id,
                                fabric_roll_id=roll.id, weight_kg=roll.weight_kg, length_m=roll.length_m,
                                issue_date=WHEN)


# ---- finishing: full receipt, same roll preserved ----

def test_finishing_full_receipt_preserves_roll(db, demo):
    roll = _greige_roll(db, demo, "KP-F1")
    original_no = roll.roll_no
    iss = _issue(db, demo, roll, process="FINISHING")
    r = cust.receive_from_vendor(db, company_id=demo["company_id"], job_work_issue_id=iss.id,
                                 preserve_roll_id=roll.id, accepted_input=Decimal("250"),
                                 output_weight_kg=Decimal("248"), output_warehouse_id=demo["warehouses"]["fg"],
                                 inspection_result="ACCEPT", challan_no="RC-F1", receipt_date=WHEN)
    db.refresh(roll); db.refresh(iss)
    assert roll.roll_no == original_no                 # same protected Roll ID
    assert roll.kind in (RollKind.FINISHED, RollKind.FINISHED.value)
    assert roll.warehouse_id == demo["warehouses"]["fg"]
    assert iss.status == JobWorkIssueStatus.CLOSED
    assert r.output_roll_id == roll.id


# ---- dyeing: multiple partial receipts, new output rolls ----

def test_dyeing_partial_then_final_receipt(db, demo):
    semi = _wh(db, demo, "WH-SEMI", "SEMI_FINISHED")
    roll = _greige_roll(db, demo, "KP-D1", kg="250")
    iss = _issue(db, demo, roll, process="DYEING")
    # partial: 100kg dyed out
    r1 = cust.receive_from_vendor(db, company_id=demo["company_id"], job_work_issue_id=iss.id,
                                  accepted_input=Decimal("100"), output_warehouse_id=semi.id,
                                  challan_no="RC-D1a", receipt_date=WHEN)
    db.refresh(iss)
    assert iss.status == JobWorkIssueStatus.PARTIALLY_RECEIVED
    assert cust.reconciliation(db, iss)["remaining_outstanding"] == Decimal("150.000000")
    new_roll = db.get(FabricRoll, r1.output_roll_id)
    assert new_roll.roll_no.startswith("DY-DEMO-") and new_roll.warehouse_id == semi.id
    assert new_roll.kind in (RollKind.DYED, RollKind.DYED.value)
    # final: remaining 150
    cust.receive_from_vendor(db, company_id=demo["company_id"], job_work_issue_id=iss.id,
                             accepted_input=Decimal("150"), output_warehouse_id=semi.id,
                             challan_no="RC-D1b", receipt_date=WHEN)
    db.refresh(iss)
    assert iss.status == JobWorkIssueStatus.CLOSED
    assert cust.reconciliation(db, iss)["remaining_outstanding"] == Decimal("0.000000")
    # issue no longer outstanding
    assert cust.vendor_outstanding(db, demo["company_id"], job_worker_id=demo["supplier_id"]) == []


# ---- genealogy linkage ----

def test_dyeing_receipt_records_genealogy(db, demo):
    semi = _wh(db, demo, "WH-SEMI", "SEMI_FINISHED")
    roll = _greige_roll(db, demo, "KP-D2")
    iss = _issue(db, demo, roll, process="DYEING")
    r = cust.receive_from_vendor(db, company_id=demo["company_id"], job_work_issue_id=iss.id,
                                 accepted_input=Decimal("250"), output_warehouse_id=semi.id,
                                 challan_no="RC-D2", receipt_date=WHEN)
    edge = db.execute(select(RollGenealogyEdge).where(
        RollGenealogyEdge.output_roll_id == r.output_roll_id,
        RollGenealogyEdge.source_roll_id == roll.id)).scalars().first()
    assert edge is not None and edge.process_type == "DYE"


# ---- FIFO valuation + no duplicate stock/GL ----

def test_receipt_fifo_value_and_no_voucher(db, demo):
    semi = _wh(db, demo, "WH-SEMI", "SEMI_FINISHED")
    roll = _greige_roll(db, demo, "KP-D3")
    iss = _issue(db, demo, roll, process="DYEING")
    input_val = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=roll.id)["value"]
    v_before = db.execute(select(func.count()).select_from(Voucher).where(Voucher.company_id == demo["company_id"])).scalar()
    r = cust.receive_from_vendor(db, company_id=demo["company_id"], job_work_issue_id=iss.id,
                                 accepted_input=Decimal("250"), output_warehouse_id=semi.id,
                                 challan_no="RC-D3", receipt_date=WHEN)
    v_after = db.execute(select(func.count()).select_from(Voucher).where(Voucher.company_id == demo["company_id"])).scalar()
    assert v_after == v_before                              # no duplicate GL
    out_val = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=r.output_roll_id)["value"]
    assert out_val == input_val                            # FIFO cost carried intact


# ---- rejection / shortage / loss recorded in reconciliation ----

def test_reconciliation_components(db, demo):
    semi = _wh(db, demo, "WH-SEMI", "SEMI_FINISHED")
    roll = _greige_roll(db, demo, "KP-D4", kg="100")
    iss = _issue(db, demo, roll, process="DYEING")
    cust.receive_from_vendor(db, company_id=demo["company_id"], job_work_issue_id=iss.id,
                             accepted_input=Decimal("80"), output_warehouse_id=semi.id,
                             rejected_qty=Decimal("5"), process_loss=Decimal("3"), shortage_qty=Decimal("2"),
                             returned_qty=Decimal("10"), return_warehouse_id=demo["warehouses"]["greige"],
                             challan_no="RC-D4", receipt_date=WHEN)
    rec = cust.reconciliation(db, iss)
    assert rec["rejected_qty"] == Decimal("5.000000")
    assert rec["process_loss_qty"] == Decimal("3.000000")
    assert rec["shortage_qty"] == Decimal("2.000000")
    assert rec["returned_qty"] == Decimal("10.000000")
    # issued 100 = 80 + 10 + 5 + 3 + 2 -> fully resolved -> CLOSED
    assert rec["remaining_outstanding"] == Decimal("0.000000")
    db.refresh(iss)
    assert iss.status == JobWorkIssueStatus.CLOSED


# ---- unprocessed return moves stock back ----

def test_unprocessed_return_moves_stock(db, demo):
    roll = _greige_roll(db, demo, "KP-R1", kg="100")
    iss = _issue(db, demo, roll, process="DYEING")
    cust.receive_from_vendor(db, company_id=demo["company_id"], job_work_issue_id=iss.id,
                             returned_qty=Decimal("100"), return_warehouse_id=demo["warehouses"]["greige"],
                             challan_no="RC-R1", receipt_date=WHEN)
    # the roll's stock is back in the greige warehouse
    g = db.execute(select(func.coalesce(func.sum(StockLayer.qty_remaining), 0)).where(
        StockLayer.company_id == demo["company_id"], StockLayer.item_id == roll.id,
        StockLayer.warehouse_id == demo["warehouses"]["greige"], StockLayer.qty_remaining > 0)).scalar()
    assert Decimal(str(g)) == Decimal("100")


# ---- excess receipt requires authorisation ----

def test_excess_receipt_needs_authorisation(db, demo):
    # resolving MORE input than issued (here via a reported rejection beyond the
    # issued qty) is an over-receipt that requires authorisation.
    roll = _greige_roll(db, demo, "KP-X1", kg="100")
    iss = _issue(db, demo, roll, process="DYEING")
    with pytest.raises(cust.JobWorkCustodyError):
        cust.receive_from_vendor(db, company_id=demo["company_id"], job_work_issue_id=iss.id,
                                 rejected_qty=Decimal("120"), challan_no="RC-X1", receipt_date=WHEN)
    r = cust.receive_from_vendor(db, company_id=demo["company_id"], job_work_issue_id=iss.id,
                                 rejected_qty=Decimal("120"), authorize_excess=True,
                                 challan_no="RC-X2", receipt_date=WHEN)
    assert r.is_excess is True


# ---- duplicate / closed-issue receipt prevention ----

def test_receive_against_closed_issue_rejected(db, demo):
    semi = _wh(db, demo, "WH-SEMI", "SEMI_FINISHED")
    roll = _greige_roll(db, demo, "KP-C1", kg="100")
    iss = _issue(db, demo, roll, process="DYEING")
    cust.receive_from_vendor(db, company_id=demo["company_id"], job_work_issue_id=iss.id,
                             accepted_input=Decimal("100"), output_warehouse_id=semi.id,
                             challan_no="RC-C1", receipt_date=WHEN)
    with pytest.raises(cust.JobWorkCustodyError):       # already CLOSED
        cust.receive_from_vendor(db, company_id=demo["company_id"], job_work_issue_id=iss.id,
                                 accepted_input=Decimal("1"), output_warehouse_id=semi.id,
                                 challan_no="RC-C1b", receipt_date=WHEN)


# ---- wrong destination category rejected ----

def test_dyeing_receipt_wrong_warehouse_rejected(db, demo):
    roll = _greige_roll(db, demo, "KP-W1")
    iss = _issue(db, demo, roll, process="DYEING")
    from app.services.hybrid import HybridConfigError
    with pytest.raises(HybridConfigError):
        cust.receive_from_vendor(db, company_id=demo["company_id"], job_work_issue_id=iss.id,
                                 accepted_input=Decimal("250"), output_warehouse_id=demo["warehouses"]["fg"],
                                 challan_no="RC-W1", receipt_date=WHEN)


def test_finishing_rejected_roll_not_accepted_as_fg(db, demo):
    roll = _greige_roll(db, demo, "KP-W2")
    iss = _issue(db, demo, roll, process="FINISHING")
    with pytest.raises(cust.JobWorkCustodyError):
        cust.receive_from_vendor(db, company_id=demo["company_id"], job_work_issue_id=iss.id,
                                 preserve_roll_id=roll.id, accepted_input=Decimal("250"),
                                 output_warehouse_id=demo["warehouses"]["fg"], inspection_result="REJECT",
                                 challan_no="RC-W2", receipt_date=WHEN)


# ---- dispatch / transfer / reissue prevention while in custody ----

def test_dispatch_blocked_while_in_custody(db, demo):
    roll = _greige_roll(db, demo, "KP-DS1")
    _issue(db, demo, roll, process="FINISHING")       # roll now in custody
    with pytest.raises(ol.LifecycleError):
        ol.create_delivery(db, company_id=demo["company_id"], customer_id=demo["customer_id"],
                           sales_order_id=None, number="DN-X", delivery_date=WHEN, roll_ids=[roll.id])


def test_reissue_blocked_while_in_custody(db, demo):
    roll = _greige_roll(db, demo, "KP-RE1")
    _issue(db, demo, roll, process="FINISHING")
    update_process_mode(db, demo["company_id"], {"dye_outsourced": True})
    with pytest.raises(cust.JobWorkCustodyError):
        cust.issue_to_vendor(db, company_id=demo["company_id"], job_worker_id=demo["supplier_id"],
                             process="DYEING", item_type=ItemType.FABRIC, item_id=roll.id,
                             qty=Decimal("250"), fabric_roll_id=roll.id, issue_date=WHEN)


# ---- company isolation ----

def test_receive_cross_company_issue_rejected(db, demo):
    semi = _wh(db, demo, "WH-SEMI", "SEMI_FINISHED")
    roll = _greige_roll(db, demo, "KP-ISO")
    iss = _issue(db, demo, roll, process="DYEING")
    other = db.execute(select(Company).where(Company.code == "UATB")).scalar_one_or_none()
    if other is None:
        other = Company(code="UATB", name="UAT Weaves", legal_name="UAT Weaves Pvt Ltd",
                        gstin="27ABCDE9999F1Z2", state_code="27", currency="INR")
        db.add(other); db.flush()
    with pytest.raises(cust.JobWorkCustodyError):
        cust.receive_from_vendor(db, company_id=other.id, job_work_issue_id=iss.id,
                                 accepted_input=Decimal("10"), output_warehouse_id=semi.id,
                                 challan_no="RC-ISO", receipt_date=WHEN)
