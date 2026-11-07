"""Batching: assign greige rolls to a dye batch (lot), auto-sum weight, trace."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.manufacturing import KnittingProduction
from app.services.manufacturing.knitting import record_production


def _make_greige_rolls(db, demo):
    kp = KnittingProduction(company_id=demo["company_id"], number="KP-BATCH", machine_id=demo["machines"]["knit"],
                            yarn_id=demo["yarn_id"], fabric_type_id=demo["fabric_type_id"],
                            production_date=date(2026, 11, 7), produced_kg=Decimal("250"),
                            yarn_consumed_kg=Decimal("255"))
    db.add(kp)
    db.flush()
    res = record_production(db, kp, [
        {"roll_no": "GR-1", "weight_kg": "125", "warehouse_id": demo["warehouses"]["greige"]},
        {"roll_no": "GR-2", "weight_kg": "125", "warehouse_id": demo["warehouses"]["greige"]},
    ])
    db.commit()
    return [str(r.id) for r in res["rolls"]]


def test_create_batch_from_rolls_sums_weight(client, auth_headers, db, demo):
    roll_ids = _make_greige_rolls(db, demo)

    avail = client.get("/api/rolls?kind=GREIGE&unbatched=true", headers=auth_headers).json()
    assert {r["roll_no"] for r in avail} >= {"GR-1", "GR-2"}

    created = client.post("/api/dyeing/batches", headers=auth_headers, json={
        "batch_code": "DY-BATCH-1", "shade_id": str(demo["shade_id"]), "roll_ids": roll_ids}).json()
    assert created["rolls"] == 2
    assert created["fabric_kg"] == "250.000000"          # 125 + 125, auto-summed

    # the rolls are no longer available for batching
    avail2 = client.get("/api/rolls?kind=GREIGE&unbatched=true", headers=auth_headers).json()
    assert not ({"GR-1", "GR-2"} & {r["roll_no"] for r in avail2})

    # assigned rolls are listed on the batch
    on_batch = client.get(f"/api/dyeing/batches/{created['batch_id']}/rolls", headers=auth_headers).json()
    assert {r["roll_no"] for r in on_batch} == {"GR-1", "GR-2"}


def test_roll_cannot_be_double_batched(client, auth_headers, db, demo):
    roll_ids = _make_greige_rolls(db, demo)
    client.post("/api/dyeing/batches", headers=auth_headers, json={
        "batch_code": "DY-A", "roll_ids": roll_ids})
    second = client.post("/api/dyeing/batches", headers=auth_headers, json={
        "batch_code": "DY-B", "roll_ids": [roll_ids[0]]})
    assert second.status_code == 422
    assert "already assigned" in second.text


def test_assign_rolls_endpoint_resums(client, auth_headers, db, demo):
    roll_ids = _make_greige_rolls(db, demo)
    batch = client.post("/api/dyeing/batches", headers=auth_headers, json={
        "batch_code": "DY-C", "roll_ids": [roll_ids[0]]}).json()
    assert batch["fabric_kg"] == "125.000000"
    updated = client.post(f"/api/dyeing/batches/{batch['batch_id']}/assign-rolls", headers=auth_headers,
                          json={"roll_ids": [roll_ids[1]]}).json()
    assert updated["rolls"] == 2 and updated["fabric_kg"] == "250.000000"
