"""Phase 6: factory-scoped stock position (stock-on-hand by factory)."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.core.security import hash_password
from app.models.core import Branch, Company, Role, User
from app.models.inventory import ItemType
from app.models.masters import Warehouse
from app.models.tenancy import UserCompanyMembership, UserFactoryMembership
from app.services.inventory import service as inventory
from app.services.inventory.service import receive

ITEM = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _setup(db):
    a = db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()
    f1 = Branch(company_id=a.id, code="SF1", name="Stock F1")
    f2 = Branch(company_id=a.id, code="SF2", name="Stock F2")
    db.add_all([f1, f2])
    db.flush()
    w1 = Warehouse(company_id=a.id, branch_id=f1.id, code="SW1", name="SWH1", kind="STORE")
    w2 = Warehouse(company_id=a.id, branch_id=f2.id, code="SW2", name="SWH2", kind="STORE")
    db.add_all([w1, w2])
    db.flush()
    receive(db, company_id=a.id, item_type=ItemType.CHEMICAL, item_id=ITEM, qty=100,
            unit_cost=10, warehouse_id=w1.id, event_type="GRN")
    receive(db, company_id=a.id, item_type=ItemType.CHEMICAL, item_id=ITEM, qty=40,
            unit_cost=10, warehouse_id=w2.id, event_type="GRN")
    db.commit()
    return a, f1, f2, w1, w2


def test_factory_filter_restricts_position(db):
    a, f1, f2, w1, w2 = _setup(db)
    all_rows = inventory.stock_on_hand(db, a.id, item_type=ItemType.CHEMICAL)
    assert sum(Decimal(str(r["qty"])) for r in all_rows) == Decimal("140")
    # every row is tagged with its factory
    assert all(r["factory_id"] in {str(f1.id), str(f2.id)} for r in all_rows)

    f1_rows = inventory.stock_on_hand(db, a.id, item_type=ItemType.CHEMICAL, factory_id=f1.id)
    assert sum(Decimal(str(r["qty"])) for r in f1_rows) == Decimal("100")
    assert all(r["factory_id"] == str(f1.id) for r in f1_rows)

    f2_rows = inventory.stock_on_hand(db, a.id, item_type=ItemType.CHEMICAL, factory_id=f2.id)
    assert sum(Decimal(str(r["qty"])) for r in f2_rows) == Decimal("40")


def test_api_stock_scoped_by_acting_factory(client, db):
    a, f1, f2, w1, w2 = _setup(db)
    role = db.execute(select(Role).where(Role.code == "AUDITOR")).scalar_one()  # report.read
    u = User(username="stockuser", email="su@demo.local", full_name="SU",
             hashed_password=hash_password("secret12"), is_active=True, is_superuser=False,
             company_id=a.id)
    u.roles.append(role)
    db.add(u)
    db.flush()
    db.add_all([
        UserCompanyMembership(user_id=u.id, company_id=a.id, is_default=True),
        UserFactoryMembership(user_id=u.id, company_id=a.id, branch_id=f1.id),
    ])
    db.commit()
    r = client.post("/api/auth/login", data={"username": "stockuser", "password": "secret12"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # with F1 in scope -> only F1's 100
    resp = client.get("/api/inventory/stock-on-hand?item_type=CHEMICAL",
                      headers={**headers, "X-Factory-Id": str(f1.id)})
    assert resp.status_code == 200, resp.text
    assert sum(Decimal(row["qty"]) for row in resp.json()) == Decimal("100")

    # no factory in scope -> company-wide 140
    resp2 = client.get("/api/inventory/stock-on-hand?item_type=CHEMICAL", headers=headers)
    assert sum(Decimal(row["qty"]) for row in resp2.json()) == Decimal("140")
