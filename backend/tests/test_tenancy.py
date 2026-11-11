"""Company-ownership guard: a caller cannot reference another company's records."""
from __future__ import annotations

from datetime import date

import pytest

from app.core.tenancy import CompanyOwnershipError, require_company_record
from app.models.core import Company
from app.models.masters import Supplier
from app.services import order_lifecycle as ol


def _other_company_supplier(db):
    other = Company(code="OTHER", name="Other Mills Pvt Ltd", state_code="06")
    db.add(other); db.flush()
    sup = Supplier(company_id=other.id, code="O-SUP", name="Outsider Yarns", state_code="06")
    db.add(sup); db.flush()
    return other, sup


# ------------------------------- the helper ---------------------------------

def test_require_company_record_accepts_own(db, demo):
    sup = db.get(Supplier, demo["supplier_id"])
    got = require_company_record(db, Supplier, sup.id, demo["company_id"], label="Supplier")
    assert got.id == sup.id


def test_require_company_record_rejects_foreign(db, demo):
    _other, foreign_sup = _other_company_supplier(db)
    with pytest.raises(CompanyOwnershipError):
        require_company_record(db, Supplier, foreign_sup.id, demo["company_id"], label="Supplier")


def test_required_none_raises_optional_none_ok(db, demo):
    with pytest.raises(CompanyOwnershipError):
        require_company_record(db, Supplier, None, demo["company_id"], label="Supplier")
    assert require_company_record(db, Supplier, None, demo["company_id"],
                                  label="Supplier", optional=True) is None


# --------------------------- through a real flow ----------------------------

def test_cannot_raise_po_for_foreign_supplier(db, demo):
    _other, foreign_sup = _other_company_supplier(db)
    with pytest.raises(CompanyOwnershipError):
        ol.create_purchase_order(
            db, company_id=demo["company_id"], supplier_id=foreign_sup.id, number="PO-X",
            order_date=date(2026, 11, 11),
            lines=[{"chemical_id": demo["chemicals"]["RB-01"], "qty": "10", "rate": "100"}])


def test_own_supplier_po_still_works(db, demo):
    po = ol.create_purchase_order(
        db, company_id=demo["company_id"], supplier_id=demo["supplier_id"], number="PO-OK",
        order_date=date(2026, 11, 11),
        lines=[{"chemical_id": demo["chemicals"]["RB-01"], "qty": "10", "rate": "100"}])
    assert po.number == "PO-OK"


def test_api_returns_422_for_foreign_supplier(client, auth_headers, db, demo):
    _other, foreign_sup = _other_company_supplier(db); db.commit()
    r = client.post("/api/procurement/purchase-orders", headers=auth_headers, json={
        "supplier_id": str(foreign_sup.id), "number": "PO-FGN", "order_date": "2026-11-11",
        "lines": [{"chemical_id": str(demo["chemicals"]["RB-01"]), "qty": "10", "rate": "100"}]})
    assert r.status_code == 422, r.text
