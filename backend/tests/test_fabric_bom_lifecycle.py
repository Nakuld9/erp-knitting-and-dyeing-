"""Production-order lifecycle controls: issue caps, completion guards, cancel."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.fabric_bom import ProductionOrderStatus, ReservationStatus
from app.services.bom import production, service as bom


def _yarn_bom_order(db, demo, *, code, qty="100"):
    b = bom.create_bom(db, company_id=demo["company_id"], code=code, name=code)
    v = b.versions[0]
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None, effective_from=date(2026, 4, 1))
    o = production.create_order(db, company_id=demo["company_id"], bom_version_id=v.id,
                                planned_qty_kg=Decimal(qty), number="PO-"+code, order_date=date(2026, 6, 1))
    res = o.reservations[0]
    return o, res


def test_create_order_rejects_nonpositive_qty(db, demo):
    b = bom.create_bom(db, company_id=demo["company_id"], code="FB-LC0", name="x")
    v = b.versions[0]
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "std_consumption_per_kg": Decimal("1"),
                              "std_unit_cost": Decimal("1"), "blend_percent": Decimal("100")}])
    bom.approve_version(db, version_id=v.id, approved_by=None)
    with pytest.raises(production.ProductionError):
        production.create_order(db, company_id=demo["company_id"], bom_version_id=v.id,
                                planned_qty_kg=Decimal("0"), number="PO-ZERO")


def test_over_issue_rejected_unless_authorized(db, demo):
    o, res = _yarn_bom_order(db, demo, code="FB-LC1")
    planned = Decimal(str(res.planned_qty))
    # issuing more than planned is rejected
    with pytest.raises(production.ProductionError):
        production.issue_material(db, order_id=o.id, reservation_id=res.id, qty=planned + Decimal("1"))
    # exact planned is fine → ISSUED
    production.issue_material(db, order_id=o.id, reservation_id=res.id, qty=planned)
    db.refresh(res)
    assert res.status == ReservationStatus.ISSUED
    # a further issue (now over) is rejected, but the authorized path allows it
    with pytest.raises(production.ProductionError):
        production.issue_material(db, order_id=o.id, reservation_id=res.id, qty=Decimal("1"))
    production.issue_material(db, order_id=o.id, reservation_id=res.id, qty=Decimal("1"),
                             allow_over_issue=True)


def test_complete_requires_no_outstanding_and_positive_qty(db, demo):
    o, res = _yarn_bom_order(db, demo, code="FB-LC2")
    # outstanding reservation → cannot complete
    with pytest.raises(production.ProductionError):
        production.complete_order(db, order_id=o.id, produced_qty_kg=Decimal("90"))
    production.issue_material(db, order_id=o.id, reservation_id=res.id, qty=res.planned_qty)
    # non-positive produced qty → rejected
    with pytest.raises(production.ProductionError):
        production.complete_order(db, order_id=o.id, produced_qty_kg=Decimal("0"))
    o = production.complete_order(db, order_id=o.id, produced_qty_kg=Decimal("95"))
    assert o.status == ProductionOrderStatus.COMPLETED
    # a completed order cannot be silently re-completed or re-issued
    with pytest.raises(production.ProductionError):
        production.complete_order(db, order_id=o.id, produced_qty_kg=Decimal("80"))
    with pytest.raises(production.ProductionError):
        production.issue_material(db, order_id=o.id, reservation_id=res.id, qty=Decimal("1"),
                                 allow_over_issue=True)


def test_cancel_releases_reservations_and_blocks_further_ops(db, demo):
    o, res = _yarn_bom_order(db, demo, code="FB-LC3")
    o = production.cancel_order(db, order_id=o.id)
    assert o.status == ProductionOrderStatus.CANCELLED
    db.refresh(res)
    assert res.status == ReservationStatus.RELEASED
    # no issue/complete/cancel on a cancelled order
    with pytest.raises(production.ProductionError):
        production.issue_material(db, order_id=o.id, reservation_id=res.id, qty=Decimal("1"))
    with pytest.raises(production.ProductionError):
        production.complete_order(db, order_id=o.id, produced_qty_kg=Decimal("1"), allow_outstanding=True)
    with pytest.raises(production.ProductionError):
        production.cancel_order(db, order_id=o.id)
