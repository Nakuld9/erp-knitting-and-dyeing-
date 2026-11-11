"""Same-company factory-to-factory stock transfer (goods-in-transit) lifecycle."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.core import Branch, Company, User
from app.models.inventory import ItemType, StockLayer
from app.models.accounting import Voucher
from app.models.masters import Warehouse
from app.models.transfers import FactoryStockTransfer, TransferStatus
from app.services import factory_transfer as ft
from app.services.factory_transfer import TransferError
from app.services.inventory.service import receive

ITEM = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _company(db):
    return db.execute(select(Company).where(Company.code == "DEMO")).scalar_one()


def _setup(db, *, same_factory=False):
    a = _company(db)
    f1 = Branch(company_id=a.id, code="TF1", name="Factory T1")
    f2 = Branch(company_id=a.id, code="TF2", name="Factory T2")
    db.add_all([f1, f2])
    db.flush()
    src_branch = f1
    dst_branch = f1 if same_factory else f2
    w1 = Warehouse(company_id=a.id, branch_id=src_branch.id, code="W1", name="WH1", kind="STORE")
    w2 = Warehouse(company_id=a.id, branch_id=dst_branch.id, code="W2", name="WH2", kind="STORE")
    db.add_all([w1, w2])
    db.flush()
    # stock in the source warehouse: 100 @ 20
    receive(db, company_id=a.id, item_type=ItemType.CHEMICAL, item_id=ITEM, qty=100,
            unit_cost=20, warehouse_id=w1.id, event_type="GRN")
    db.commit()
    return a, w1, w2


def _onhand(db, company_id, warehouse_id):
    layers = db.execute(select(StockLayer).where(
        StockLayer.company_id == company_id, StockLayer.warehouse_id == warehouse_id,
        StockLayer.item_id == ITEM, StockLayer.qty_remaining > 0)).scalars().all()
    return sum(Decimal(str(l.qty_remaining)) for l in layers)


def test_full_lifecycle_moves_stock(db):
    a, w1, w2 = _setup(db)
    requester, checker = uuid.uuid4(), uuid.uuid4()
    t = ft.create_transfer_request(db, company_id=a.id, item_type=ItemType.CHEMICAL, item_id=ITEM,
                                   from_warehouse_id=w1.id, to_warehouse_id=w2.id, qty=60,
                                   requested_by=requester)
    assert TransferStatus(t.status) == TransferStatus.REQUESTED

    # maker-checker: requester cannot authorize their own
    with pytest.raises(TransferError):
        ft.authorize_transfer(db, t.id, a.id, requester)
    ft.authorize_transfer(db, t.id, a.id, checker)
    assert TransferStatus(db.get(FactoryStockTransfer, t.id).status) == TransferStatus.AUTHORIZED

    ft.dispatch_transfer(db, t.id, a.id, checker)
    t2 = db.get(FactoryStockTransfer, t.id)
    assert TransferStatus(t2.status) == TransferStatus.DISPATCHED
    assert Decimal(str(t2.dispatched_qty)) == Decimal("60")
    assert Decimal(str(t2.unit_cost)) == Decimal("20.00")
    # source reduced, destination not yet credited (goods in transit)
    assert _onhand(db, a.id, w1.id) == Decimal("40")
    assert _onhand(db, a.id, w2.id) == Decimal("0")

    ft.receive_transfer(db, t.id, a.id, checker, received_qty=60)
    t3 = db.get(FactoryStockTransfer, t.id)
    assert TransferStatus(t3.status) == TransferStatus.RECEIVED
    assert _onhand(db, a.id, w2.id) == Decimal("60")
    assert Decimal(str(t3.shortage_qty)) == Decimal("0")
    assert t3.needs_reconciliation is False


def test_no_invoice_posted(db):
    """A same-company factory transfer is GL-neutral: it posts no voucher."""
    a, w1, w2 = _setup(db)
    r, c = uuid.uuid4(), uuid.uuid4()
    t = ft.create_transfer_request(db, company_id=a.id, item_type=ItemType.CHEMICAL, item_id=ITEM,
                                   from_warehouse_id=w1.id, to_warehouse_id=w2.id, qty=60, requested_by=r)
    ft.authorize_transfer(db, t.id, a.id, c)
    ft.dispatch_transfer(db, t.id, a.id, c)
    ft.receive_transfer(db, t.id, a.id, c, received_qty=60)
    vouchers = db.execute(select(Voucher).where(Voucher.source_module == "factory_transfer")).scalars().all()
    assert vouchers == []


def test_shortage_flags_reconciliation(db):
    a, w1, w2 = _setup(db)
    r, c = uuid.uuid4(), uuid.uuid4()
    t = ft.create_transfer_request(db, company_id=a.id, item_type=ItemType.CHEMICAL, item_id=ITEM,
                                   from_warehouse_id=w1.id, to_warehouse_id=w2.id, qty=60, requested_by=r)
    ft.authorize_transfer(db, t.id, a.id, c)
    ft.dispatch_transfer(db, t.id, a.id, c)
    ft.receive_transfer(db, t.id, a.id, c, received_qty=50)  # 10 short
    t2 = db.get(FactoryStockTransfer, t.id)
    assert Decimal(str(t2.shortage_qty)) == Decimal("10")
    assert t2.needs_reconciliation is True
    assert _onhand(db, a.id, w2.id) == Decimal("50")


def test_rejection_flags_reconciliation(db):
    a, w1, w2 = _setup(db)
    r, c = uuid.uuid4(), uuid.uuid4()
    t = ft.create_transfer_request(db, company_id=a.id, item_type=ItemType.CHEMICAL, item_id=ITEM,
                                   from_warehouse_id=w1.id, to_warehouse_id=w2.id, qty=60, requested_by=r)
    ft.authorize_transfer(db, t.id, a.id, c)
    ft.dispatch_transfer(db, t.id, a.id, c)
    ft.receive_transfer(db, t.id, a.id, c, received_qty=60, rejected_qty=5)  # 55 accepted
    t2 = db.get(FactoryStockTransfer, t.id)
    assert Decimal(str(t2.rejected_qty)) == Decimal("5")
    assert t2.needs_reconciliation is True
    assert _onhand(db, a.id, w2.id) == Decimal("55")


def test_same_factory_rejected(db):
    a, w1, w2 = _setup(db, same_factory=True)
    with pytest.raises(TransferError):
        ft.create_transfer_request(db, company_id=a.id, item_type=ItemType.CHEMICAL, item_id=ITEM,
                                   from_warehouse_id=w1.id, to_warehouse_id=w2.id, qty=10,
                                   requested_by=uuid.uuid4())


def test_cannot_skip_states(db):
    a, w1, w2 = _setup(db)
    r, c = uuid.uuid4(), uuid.uuid4()
    t = ft.create_transfer_request(db, company_id=a.id, item_type=ItemType.CHEMICAL, item_id=ITEM,
                                   from_warehouse_id=w1.id, to_warehouse_id=w2.id, qty=60, requested_by=r)
    # cannot dispatch before authorize
    with pytest.raises(TransferError):
        ft.dispatch_transfer(db, t.id, a.id, c)
    # cannot receive before dispatch
    ft.authorize_transfer(db, t.id, a.id, c)
    with pytest.raises(TransferError):
        ft.receive_transfer(db, t.id, a.id, c, received_qty=60)


# ---- API ----

def test_api_lifecycle(client, db):
    from app.core.security import hash_password
    from app.models.core import Role
    a, w1, w2 = _setup(db)
    # a production manager (transfer.manage) makes & moves; admin (checker) authorizes
    mgr_role = db.execute(select(Role).where(Role.code == "PRODUCTION_MANAGER")).scalar_one()
    mgr = User(username="tmgr", email="tmgr@demo.local", full_name="TM",
               hashed_password=hash_password("secret12"), is_active=True, is_superuser=False,
               company_id=a.id)
    mgr.roles.append(mgr_role)
    db.add(mgr)
    db.commit()

    def login(u, p):
        r = client.post("/api/auth/login", data={"username": u, "password": p})
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    mgr_h = login("tmgr", "secret12")
    admin_h = login("admin", "admin123")

    cr = client.post("/api/transfers/factory", headers=mgr_h, json={
        "item_type": "CHEMICAL", "item_id": str(ITEM),
        "from_warehouse_id": str(w1.id), "to_warehouse_id": str(w2.id), "qty": 60})
    assert cr.status_code == 201, cr.text
    tid = cr.json()["id"]
    # admin (different actor, has '*') authorizes; mgr dispatches & receives
    assert client.post(f"/api/transfers/factory/{tid}/authorize", headers=admin_h).status_code == 200
    assert client.post(f"/api/transfers/factory/{tid}/dispatch", headers=mgr_h).status_code == 200
    rec = client.post(f"/api/transfers/factory/{tid}/receive", headers=mgr_h, json={"received_qty": 60})
    assert rec.status_code == 200, rec.text
    assert rec.json()["status"] == "RECEIVED"
