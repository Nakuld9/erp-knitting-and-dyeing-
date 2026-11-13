"""Dyeing API: full HTTP flow + company/factory isolation + accounting/inventory
valuation consistency."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.core.security import hash_password
from app.models.core import Branch, Role, User
from app.models.inventory import ItemType
from app.models.tenancy import UserCompanyMembership, UserFactoryMembership
from app.services.bom import production, service as bom
from app.services.dyeing import service as dyeing
from app.services.inventory import service as inventory


def _login(client, u="admin", p="admin123"):
    r = client.post("/api/auth/login", data={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _valued_grey_order(db, demo, *, code, qty="100", conversion="600"):
    b = bom.create_bom(db, company_id=demo["company_id"], code=code, name=code)
    v = b.versions[0]
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None)
    o = production.create_order(db, company_id=demo["company_id"], bom_version_id=v.id,
                               planned_qty_kg=Decimal(qty), number="PO-"+code)
    res = o.reservations[0]
    production.issue_material(db, order_id=o.id, reservation_id=res.id, qty=res.planned_qty)
    production.complete_order(db, order_id=o.id, produced_qty_kg=Decimal(qty))
    production.record_actual_knitting_conversion_cost(db, order_id=o.id, amount=Decimal(conversion))
    production.value_grey_output(db, order_id=o.id)
    return o


def test_dyeing_api_end_to_end(client, demo, db):
    order = _valued_grey_order(db, demo, code="APIGR1")
    db.commit()
    h = _login(client)
    # create job
    r = client.post("/api/dyeing/jobs", headers=h, json={
        "number": "DJ-API-1", "mode": "INTERNAL", "grey_production_order_id": str(order.id)})
    assert r.status_code == 201, r.text
    jid = r.json()["id"]
    # issue grey
    r = client.post(f"/api/dyeing/jobs/{jid}/issue-grey", headers=h, json={
        "source_production_order_id": str(order.id), "qty_kg": "100"})
    assert r.status_code == 200, r.text
    assert float(r.json()["cost"]) > 0
    # costs + output + complete
    assert client.post(f"/api/dyeing/jobs/{jid}/costs", headers=h, json={
        "dyeing_charge": "3000", "chemical_cost": "1500"}).status_code == 200
    assert client.post(f"/api/dyeing/jobs/{jid}/output", headers=h, json={
        "lots": [{"lot_code": "DL-1", "qty_kg": "95"}]}).status_code == 200
    assert client.post(f"/api/dyeing/jobs/{jid}/complete", headers=h, json={}).status_code == 200
    # dyed cost
    r = client.get(f"/api/dyeing/jobs/{jid}/cost", headers=h)
    assert r.status_code == 200, r.text
    cost = r.json()
    assert cost["cost_complete"] is True
    assert float(cost["saleable_output_kg"]) == 95.0
    assert float(cost["actual_dyed_cost_per_kg"]) > 0
    # trace drills to grey order + yarn issues
    tr = client.get(f"/api/dyeing/jobs/{jid}/trace", headers=h).json()
    assert tr["grey_sources"][0]["production_order_id"] == str(order.id)
    assert len(tr["grey_sources"][0]["yarn_issues"]) >= 1


def test_dyeing_api_requires_auth(client, demo, db):
    assert client.get("/api/dyeing/jobs").status_code == 401


def _restricted(db, company_id, factory_id, username):
    role = db.execute(select(Role).where(Role.code == "DYEING_SUPERVISOR")).scalar_one()
    u = User(username=username, email=f"{username}@d.local", full_name=username,
             hashed_password=hash_password("secret12"), is_active=True, is_superuser=False,
             company_id=company_id)
    u.roles.append(role)
    db.add(u); db.flush()
    db.add_all([UserCompanyMembership(user_id=u.id, company_id=company_id, is_default=True),
                UserFactoryMembership(user_id=u.id, company_id=company_id, branch_id=factory_id)])
    db.flush()
    return u


def test_dyeing_factory_isolation(client, demo, db):
    order = _valued_grey_order(db, demo, code="APIGR2")
    cid = demo["company_id"]
    f1 = Branch(company_id=cid, code="DF1", name="Dye F1")
    f2 = Branch(company_id=cid, code="DF2", name="Dye F2")
    db.add_all([f1, f2]); db.flush()
    _restricted(db, cid, f1.id, "dye_f1")
    _restricted(db, cid, f2.id, "dye_f2")
    db.commit()

    h1 = _login(client, "dye_f1", "secret12")
    # dye_f1 creates a job in F1
    jid = client.post("/api/dyeing/jobs", headers=h1, json={
        "number": "DJ-F1", "mode": "INTERNAL", "factory_id": str(f1.id)}).json()["id"]
    # dye_f2 (only F2) cannot read it
    h2 = _login(client, "dye_f2", "secret12")
    assert client.get(f"/api/dyeing/jobs/{jid}", headers=h2).status_code == 403
    # creating a job in an inaccessible factory → 403; forged factory → 404
    assert client.post("/api/dyeing/jobs", headers=h1, json={
        "number": "X", "mode": "INTERNAL", "factory_id": str(f2.id)}).status_code == 403
    assert client.post("/api/dyeing/jobs", headers=h1, json={
        "number": "Y", "mode": "INTERNAL", "factory_id": str(uuid.uuid4())}).status_code == 404
    # dye_f1 sees only its own factory's jobs in the list
    rows = client.get("/api/dyeing/jobs", headers=h1).json()
    assert all(j["factory_id"] == str(f1.id) for j in rows)


def test_dyeing_company_isolation(client, demo, db):
    """A user whose acting company is B cannot read company A's dyeing job."""
    from app.models.core import Company
    order = _valued_grey_order(db, demo, code="ISO1")
    # a job in company A (DEMO)
    job = dyeing.create_job(db, company_id=demo["company_id"], number="DJ-ISO",
                            mode="INTERNAL", grey_production_order_id=order.id)
    # a second company B with its own user
    cob = Company(code="COB", name="Company B", state_code="29")
    db.add(cob); db.flush()
    role = db.execute(select(Role).where(Role.code == "DYEING_SUPERVISOR")).scalar_one()
    ub = User(username="userb", email="ub@b.local", full_name="UB",
              hashed_password=hash_password("secret12"), is_active=True, is_superuser=False,
              company_id=cob.id)
    ub.roles.append(role)
    db.add(ub); db.flush()
    db.add(UserCompanyMembership(user_id=ub.id, company_id=cob.id, is_default=True))
    db.commit()
    h = _login(client, "userb", "secret12")
    # B's user cannot see A's job, and A's job is absent from B's list
    assert client.get(f"/api/dyeing/jobs/{job.id}", headers=h).status_code == 404
    assert all(j["id"] != str(job.id) for j in client.get("/api/dyeing/jobs", headers=h).json())


def test_accounting_inventory_valuation_consistency(db, demo):
    """Grey received value == grey actual cost; grey FIFO-out into dyeing == that
    value; dyed received value == total accumulated cost (sub-ledger consistency)."""
    order = _valued_grey_order(db, demo, code="CONS1", qty="100", conversion="600")
    # grey inventory value received equals the computed grey actual cost
    grey = production.grey_actual_cost(db, order.id)
    bal_grey = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=order.id)
    assert bal_grey["value"] == grey["actual_grey_total_cost"]

    job = dyeing.create_job(db, company_id=demo["company_id"], number="CONS-DJ", mode="INTERNAL",
                            grey_production_order_id=order.id)
    inp = dyeing.issue_grey(db, job_id=job.id, source_production_order_id=order.id, qty_kg=Decimal("100"))
    # full grey consumed → grey balance 0, and the draw cost equals the grey value
    bal_grey2 = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=order.id)
    assert bal_grey2["qty_on_hand"] == Decimal("0.000000")
    assert inp.cost == grey["actual_grey_total_cost"]

    dyeing.record_costs(db, job_id=job.id, dyeing_charge=Decimal("3000"), chemical_cost=Decimal("1500"))
    dyeing.record_output(db, job_id=job.id, lots=[{"lot_code": "L1", "qty_kg": Decimal("96")}])
    dyeing.complete_job(db, job_id=job.id)
    cost = dyeing.dyed_actual_cost(db, job.id)
    # dyed fabric inventory layer value == the job's recorded dyed_inventory_value
    # (exact sub-ledger consistency); both match the accumulated actual cost within
    # per-kg rounding dust (≤ qty×0.005).
    out_item = uuid.UUID(cost["output_lots"][0]["dyed_item_id"])
    bal_dyed = inventory.balance(db, company_id=demo["company_id"], item_type=ItemType.FABRIC, item_id=out_item)
    job_val = db.get(type(job), job.id).dyed_inventory_value
    assert bal_dyed["value"] == job_val
    assert abs(job_val - cost["total_actual_cost"]) <= Decimal("96") * Decimal("0.005")
