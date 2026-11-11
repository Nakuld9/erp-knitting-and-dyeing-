"""E-way bill generation via the offline simulator, with IRN linkage."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.commercial import SalesInvoice, SalesInvoiceLine
from app.models.ewaybill import EWayBillStatus
from app.services.einvoice import service as eisvc
from app.services.ewaybill import service as svc
from app.services.ewaybill.adapter import EWayBillGspAdapter, EWayBillSimulator, get_adapter
from app.services.ewaybill.payload import EWBPayloadError, Transport, build_ewb_payload
from app.services.sales import post_sales_invoice


def _posted_invoice(db, demo, number="INV-EWB-1", qty="250", rate="800"):
    inv = SalesInvoice(company_id=demo["company_id"], customer_id=demo["customer_id"],
                       number=number, invoice_date=date(2026, 11, 7))
    inv.lines.append(SalesInvoiceLine(line_no=1, description="Dyed fabric",
                                      qty=Decimal(qty), rate=Decimal(rate),
                                      tax_code_id=demo["tax_id"]))
    db.add(inv); db.flush()
    post_sales_invoice(db, inv); db.flush()
    return inv


def _road(**kw):
    base = dict(trans_mode="1", vehicle_no="PB10AB1234", distance_km=120)
    base.update(kw)
    return Transport(**base)


# --------------------------------- payload ----------------------------------

def test_payload_has_transport_and_totals(db, demo):
    inv = _posted_invoice(db, demo)
    p = build_ewb_payload(db, inv, _road(), irn="X" * 64)
    assert p["docNo"] == inv.number and p["vehicleNo"] == "PB10AB1234"
    assert p["transDistance"] == "120" and p["Irn"] == "X" * 64
    assert p["totInvValue"] == 236000.0 and p["itemList"][0]["hsnCode"]


def test_payload_requires_distance(db, demo):
    inv = _posted_invoice(db, demo, number="INV-EWB-NODIST")
    with pytest.raises(EWBPayloadError):
        build_ewb_payload(db, inv, _road(distance_km=0))


def test_payload_road_requires_vehicle_or_transporter(db, demo):
    inv = _posted_invoice(db, demo, number="INV-EWB-NOVEH")
    with pytest.raises(EWBPayloadError):
        build_ewb_payload(db, inv, _road(vehicle_no=None, transporter_id=None))


# ------------------------------ generation ----------------------------------

def test_generate_eway_bill_simulator(db, demo):
    inv = _posted_invoice(db, demo)
    rec = svc.generate_eway_bill(db, inv.id, _road())
    db.commit()
    assert rec.status == EWayBillStatus.GENERATED
    assert rec.ewb_no and len(rec.ewb_no) == 12 and rec.valid_until
    assert rec.gsp == "simulator" and rec.vehicle_no == "PB10AB1234"


def test_generate_links_existing_irn(db, demo):
    inv = _posted_invoice(db, demo)
    eisvc.generate_einvoice(db, inv.id); db.commit()      # IRN first
    rec = svc.generate_eway_bill(db, inv.id, _road()); db.commit()
    assert rec.irn and len(rec.irn) == 64                 # EWB carries the IRN link


def test_generate_is_idempotent(db, demo):
    inv = _posted_invoice(db, demo)
    a = svc.generate_eway_bill(db, inv.id, _road()); db.commit()
    b = svc.generate_eway_bill(db, inv.id, _road()); db.commit()
    assert a.id == b.id and a.ewb_no == b.ewb_no


def test_cancel_eway_bill(db, demo):
    inv = _posted_invoice(db, demo)
    svc.generate_eway_bill(db, inv.id, _road()); db.commit()
    rec = svc.cancel_eway_bill(db, inv.id, remarks="Order cancelled")
    db.commit()
    assert rec.status == EWayBillStatus.CANCELLED and rec.cancelled_at is not None
    with pytest.raises(svc.EWayBillServiceError):
        svc.generate_eway_bill(db, inv.id, _road())


def test_generate_rejects_draft(db, demo):
    inv = SalesInvoice(company_id=demo["company_id"], customer_id=demo["customer_id"],
                       number="INV-EWB-DRAFT", invoice_date=date(2026, 11, 7))
    inv.lines.append(SalesInvoiceLine(line_no=1, description="x", qty=Decimal("1"),
                                      rate=Decimal("1"), tax_code_id=demo["tax_id"]))
    db.add(inv); db.flush()
    with pytest.raises(svc.EWayBillServiceError):
        svc.generate_eway_bill(db, inv.id, _road())


# ------------------------------ adapter choice ------------------------------

def test_adapter_defaults_to_simulator():
    class _Cfg:
        einvoice_gsp = "simulator"
    assert isinstance(get_adapter(_Cfg()), EWayBillSimulator)


# --------------------------------- API --------------------------------------

def test_ewaybill_api_generate_and_status(client, auth_headers, db, demo):
    inv = _posted_invoice(db, demo, number="INV-EWB-API")
    db.commit()
    gen = client.post(f"/api/ewaybill/{inv.id}/generate", headers=auth_headers,
                      json={"trans_mode": "1", "vehicle_no": "PB10AB1234", "distance_km": 150})
    assert gen.status_code == 201, gen.text
    body = gen.json()
    assert body["status"] == "GENERATED" and len(body["ewb_no"]) == 12

    got = client.get(f"/api/ewaybill/{inv.id}", headers=auth_headers).json()
    assert got["ewb_no"] == body["ewb_no"]
