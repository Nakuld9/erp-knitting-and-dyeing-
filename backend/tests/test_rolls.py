"""Roll auto-numbering and denormalised lineage (machine / yarn / fabric)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.manufacturing import KnittingProduction
from app.services.manufacturing.knitting import record_production


def _produce(db, demo, specs):
    kp = KnittingProduction(company_id=demo["company_id"], number="KP-RL", machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], yarn_lot_id=demo["yarn_lot_id"],
                            fabric_type_id=demo["fabric_type_id"], production_date=date(2026, 11, 7),
                            produced_kg=Decimal("250"), yarn_consumed_kg=Decimal("255"))
    db.add(kp)
    db.flush()
    res = record_production(db, kp, specs)
    db.commit()
    return res["rolls"]


def test_roll_auto_numbering_and_lineage(db, demo):
    rolls = _produce(db, demo, [
        {"weight_kg": "125", "warehouse_id": demo["warehouses"]["greige"]},   # no roll_no -> auto
        {"roll_no": "", "weight_kg": "125"},                                   # blank -> auto
    ])
    numbers = [r.roll_no for r in rolls]
    assert all(n.startswith("GR-K-01-") for n in numbers)     # GR-<machine>-<seq>
    assert len(set(numbers)) == 2                             # unique
    for r in rolls:
        assert r.barcode == r.roll_no
        assert r.machine_id == demo["machines"]["knit"]
        assert r.yarn_id == demo["yarn_id"]
        assert r.yarn_lot_id == demo["yarn_lot_id"]
        assert r.fabric_type_id == demo["fabric_type_id"]


def test_supplied_roll_no_is_kept(db, demo):
    rolls = _produce(db, demo, [{"roll_no": "MY-ROLL-1", "weight_kg": "250"}])
    assert rolls[0].roll_no == "MY-ROLL-1"


def test_roll_list_api_shows_machine_yarn_fabric(client, auth_headers, db, demo):
    _produce(db, demo, [{"weight_kg": "250", "warehouse_id": demo["warehouses"]["greige"]}])
    rows = client.get("/api/rolls", headers=auth_headers).json()
    r = rows[0]
    assert r["machine"] == "K-01"
    assert r["yarn"] == "30s Combed Cotton"
    assert r["fabric"] == "Single Jersey 160"
    # label still renders with the new fields
    label = client.get(f"/api/rolls/{r['id']}/label", headers=auth_headers)
    assert label.status_code == 200 and label.content[:4] == b"%PDF"
