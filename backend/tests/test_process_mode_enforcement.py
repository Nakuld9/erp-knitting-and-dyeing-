"""Stage 3A — hybrid process-mode enforcement.

The six company-level switches (knit/dye/finish x internal/outsourced) now gate
the real service entry points:

* internal knitting  -> knitting.record_production
* internal dyeing    -> dyeing.create_job(mode=INTERNAL)
* internal finishing -> quality.record_finishing
* outsourced <proc>  -> dyeing.create_job(mode=JOBWORK) and jobwork.create(OUTWARD)
* inward job-work (we process others' material in-house) -> <proc>_internal

Disabled modes block NEW work only; in-progress/historical transactions always
complete. Config is company-scoped, so disabling in one company never affects
another.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.core import Company
from app.models.dyeing_cost import DyeingStatus
from app.models.manufacturing import KnittingProduction
from app.services import jobwork, quality
from app.services.bom import production, service as bom
from app.services.dyeing import service as dyeing
from app.services.hybrid import HybridConfigError, update_process_mode
from app.services.manufacturing.knitting import record_production

WHEN = date(2026, 11, 7)


def _set(db, demo, **toggles):
    update_process_mode(db, demo["company_id"], toggles)


def _kp(db, demo, number="KP-PM"):
    kp = KnittingProduction(company_id=demo["company_id"], number=number, machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], yarn_lot_id=demo["yarn_lot_id"],
                            fabric_type_id=demo["fabric_type_id"], production_date=WHEN,
                            produced_kg=Decimal("250"), yarn_consumed_kg=Decimal("255"))
    db.add(kp); db.flush()
    return kp


def _roll_spec(demo):
    return [{"weight_kg": "250", "warehouse_id": demo["warehouses"]["greige"]}]


def _jw(db, demo, *, number, direction, process, party):
    return jobwork.create(db, company_id=demo["company_id"], number=number, direction=direction,
                          process=process, party_id=party, order_date=date(2026, 11, 9),
                          qty_kg="100", rate="10", tax_code_id=demo["tax_id"])


def _grey_order(db, demo, *, code, qty="100"):
    b = bom.create_bom(db, company_id=demo["company_id"], code=code, name=code)
    v = b.versions[0]
    bom.set_spec(db, v.id, std_knitting_cost=Decimal("12"))
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "wastage_percent": Decimal("2"),
                              "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None, effective_from=date(2026, 4, 1))
    order = production.create_order(db, company_id=demo["company_id"], bom_version_id=v.id,
                                    planned_qty_kg=Decimal(qty), number="PO-" + code, order_date=date(2026, 6, 1))
    res = order.reservations[0]
    production.issue_material(db, order_id=order.id, reservation_id=res.id, qty=res.planned_qty)
    production.complete_order(db, order_id=order.id, produced_qty_kg=Decimal(qty))
    production.record_actual_knitting_conversion_cost(db, order_id=order.id, amount=Decimal("500"))
    production.value_grey_output(db, order_id=order.id)
    return order


# ---- 1) knit_internal ----

def test_knit_internal_blocks_when_disabled(db, demo):
    _set(db, demo, knit_internal=False)
    with pytest.raises(HybridConfigError):
        record_production(db, _kp(db, demo, "KP-B"), _roll_spec(demo))


def test_knit_internal_allows_by_default(db, demo):
    res = record_production(db, _kp(db, demo, "KP-A"), _roll_spec(demo))
    assert res["rolls"]


# ---- 2) knit_outsourced (outward job-work) ----

def test_knit_outsourced_blocks_outward_jobwork(db, demo):
    with pytest.raises(HybridConfigError):           # default outsourced OFF
        _jw(db, demo, number="JW-K1", direction="OUTWARD", process="KNITTING", party=demo["supplier_id"])


def test_knit_outsourced_allows_when_enabled(db, demo):
    _set(db, demo, knit_outsourced=True)
    assert _jw(db, demo, number="JW-K2", direction="OUTWARD", process="KNITTING", party=demo["supplier_id"]).id


# ---- 3) dye_internal ----

def test_dye_internal_blocks_when_disabled(db, demo):
    _set(db, demo, dye_internal=False)
    with pytest.raises(HybridConfigError):
        dyeing.create_job(db, company_id=demo["company_id"], number="DJ-B", mode="INTERNAL")


def test_dye_internal_allows_by_default(db, demo):
    assert dyeing.create_job(db, company_id=demo["company_id"], number="DJ-A", mode="INTERNAL").id


# ---- 4) dye_outsourced (JOBWORK dyeing + outward job-work) ----

def test_dye_outsourced_blocks_jobwork_mode(db, demo):
    with pytest.raises(HybridConfigError):           # default OFF
        dyeing.create_job(db, company_id=demo["company_id"], number="DJ-JW", mode="JOBWORK",
                          jobwork_supplier_id=demo["supplier_id"])


def test_dye_outsourced_allows_when_enabled(db, demo):
    _set(db, demo, dye_outsourced=True)
    assert dyeing.create_job(db, company_id=demo["company_id"], number="DJ-JW2", mode="JOBWORK",
                             jobwork_supplier_id=demo["supplier_id"]).id
    # the job-work-module path is gated by the same toggle
    assert _jw(db, demo, number="JW-D1", direction="OUTWARD", process="DYEING", party=demo["supplier_id"]).id


# ---- 5) finish_internal ----

def test_finish_internal_blocks_when_disabled(db, demo):
    _set(db, demo, finish_internal=False)
    with pytest.raises(HybridConfigError):
        quality.record_finishing(db, demo["company_id"], process_type="COMPACTING", process_date=WHEN)


def test_finish_internal_allows_by_default(db, demo):
    assert quality.record_finishing(db, demo["company_id"], process_type="COMPACTING", process_date=WHEN).id


# ---- 6) finish_outsourced (outward job-work) ----

def test_finish_outsourced_blocks_outward_jobwork(db, demo):
    with pytest.raises(HybridConfigError):
        _jw(db, demo, number="JW-F1", direction="OUTWARD", process="FINISHING", party=demo["supplier_id"])


def test_finish_outsourced_allows_when_enabled(db, demo):
    _set(db, demo, finish_outsourced=True)
    assert _jw(db, demo, number="JW-F2", direction="OUTWARD", process="FINISHING", party=demo["supplier_id"]).id


# ---- hybrid: both modes enabled ----

def test_hybrid_both_dye_modes_supported(db, demo):
    _set(db, demo, dye_outsourced=True)              # internal already on -> hybrid
    assert dyeing.create_job(db, company_id=demo["company_id"], number="DJ-H1", mode="INTERNAL").id
    assert dyeing.create_job(db, company_id=demo["company_id"], number="DJ-H2", mode="JOBWORK",
                             jobwork_supplier_id=demo["supplier_id"]).id


# ---- both modes disabled ----

def test_both_dye_modes_disabled_blocks_all_new(db, demo):
    _set(db, demo, dye_internal=False, dye_outsourced=False)
    with pytest.raises(HybridConfigError):
        dyeing.create_job(db, company_id=demo["company_id"], number="DJ-X1", mode="INTERNAL")
    with pytest.raises(HybridConfigError):
        dyeing.create_job(db, company_id=demo["company_id"], number="DJ-X2", mode="JOBWORK",
                          jobwork_supplier_id=demo["supplier_id"])


# ---- inward job-work uses the INTERNAL toggle ----

def test_inward_jobwork_gated_by_internal(db, demo):
    _set(db, demo, dye_internal=False)
    with pytest.raises(HybridConfigError):
        _jw(db, demo, number="JW-IN", direction="INWARD", process="DYEING", party=demo["customer_id"])


# ---- in-progress work always completes ----

def test_in_progress_dyeing_completes_after_disable(db, demo):
    order = _grey_order(db, demo, code="PMG1")
    job = dyeing.create_job(db, company_id=demo["company_id"], number="DJ-IP", mode="INTERNAL",
                            grey_production_order_id=order.id)
    dyeing.issue_grey(db, job_id=job.id, source_production_order_id=order.id, qty_kg=order.produced_qty_kg)
    dyeing.record_costs(db, job_id=job.id, dyeing_charge=Decimal("1000"))
    dyeing.record_output(db, job_id=job.id, lots=[{"lot_code": "L1", "qty_kg": Decimal("95")}])
    _set(db, demo, dye_internal=False)               # disable AFTER the job is under way
    dyeing.complete_job(db, job_id=job.id)           # must NOT be blocked
    assert job.status == DyeingStatus.COMPLETED


def test_in_progress_knitting_repost_not_blocked(db, demo):
    kp = _kp(db, demo, "KP-IP")
    record_production(db, kp, _roll_spec(demo))
    db.commit()
    _set(db, demo, knit_internal=False)              # disable after first posting
    res = record_production(db, kp, _roll_spec(demo))  # same number -> idempotent re-post
    assert res.get("already_posted") is True


# ---- company isolation ----

def test_disable_in_one_company_does_not_affect_another(db, demo):
    other = db.execute(select(Company).where(Company.code == "UATB")).scalar_one_or_none()
    if other is None:
        other = Company(code="UATB", name="UAT Weaves", legal_name="UAT Weaves Pvt Ltd",
                        gstin="27ABCDE9999F1Z2", state_code="27", currency="INR")
        db.add(other); db.commit()
    _set(db, demo, dye_internal=False)               # disable only in company A (DEMO)
    with pytest.raises(HybridConfigError):
        dyeing.create_job(db, company_id=demo["company_id"], number="DJ-A-ISO", mode="INTERNAL")
    # company B is unaffected (defaults: internal ON)
    assert dyeing.create_job(db, company_id=other.id, number="DJ-B-ISO", mode="INTERNAL").id
