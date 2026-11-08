"""E-invoice (IRN) generation via the offline simulator adapter."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.commercial import SalesInvoice, SalesInvoiceLine
from app.models.core import Company
from app.models.einvoice import EInvoiceStatus
from app.models.masters import Customer
from app.services.einvoice import service as svc
from app.services.einvoice.payload import EInvoicePayloadError, build_irn_payload
from app.services.sales import post_sales_invoice


def _posted_invoice(db, demo, number="INV-EI-1", qty="250", rate="800"):
    inv = SalesInvoice(company_id=demo["company_id"], customer_id=demo["customer_id"],
                       number=number, invoice_date=date(2026, 11, 7))
    inv.lines.append(SalesInvoiceLine(line_no=1, description="Dyed fabric",
                                      qty=Decimal(qty), rate=Decimal(rate),
                                      tax_code_id=demo["tax_id"]))
    db.add(inv); db.flush()
    post_sales_invoice(db, inv)
    db.flush()
    return inv


# --------------------------------- payload ----------------------------------

def test_payload_intra_state_splits_cgst_sgst(db, demo):
    inv = _posted_invoice(db, demo)                      # seller & buyer both state 03
    payload = build_irn_payload(db, inv)
    val = payload["ValDtls"]
    assert val["CgstVal"] > 0 and val["SgstVal"] > 0 and val["IgstVal"] == 0
    # 250*800 = 200,000 taxable; 18% -> 36,000 tax; total 236,000
    assert val["AssVal"] == 200000.0
    assert round(val["TotInvVal"], 2) == 236000.0
    assert payload["DocDtls"]["No"] == inv.number
    assert payload["SellerDtls"]["Gstin"] and payload["BuyerDtls"]["Gstin"]


def test_payload_inter_state_uses_igst(db, demo):
    cust = db.get(Customer, demo["customer_id"])
    cust.state_code = "27"; cust.gstin = "27YYYYY0000Y1Z5"   # Maharashtra buyer
    db.flush()
    inv = _posted_invoice(db, demo, number="INV-EI-IST")
    payload = build_irn_payload(db, inv)
    val = payload["ValDtls"]
    assert val["IgstVal"] > 0 and val["CgstVal"] == 0 and val["SgstVal"] == 0
    assert payload["ItemList"][0]["IgstAmt"] > 0


def test_payload_requires_buyer_gstin(db, demo):
    cust = db.get(Customer, demo["customer_id"])
    cust.gstin = None; db.flush()
    inv = _posted_invoice(db, demo, number="INV-EI-NOGST")
    with pytest.raises(EInvoicePayloadError):
        build_irn_payload(db, inv)


# ------------------------------ generation ----------------------------------

def test_generate_einvoice_simulator(db, demo):
    inv = _posted_invoice(db, demo)
    rec = svc.generate_einvoice(db, inv.id)
    db.commit()
    assert rec.status == EInvoiceStatus.GENERATED
    assert rec.irn and len(rec.irn) == 64                # IRN is a 64-char hash
    assert rec.ack_no and rec.signed_qr.startswith("SIM.")
    assert rec.gsp == "simulator"


def test_generate_is_idempotent(db, demo):
    inv = _posted_invoice(db, demo)
    first = svc.generate_einvoice(db, inv.id); db.commit()
    second = svc.generate_einvoice(db, inv.id); db.commit()
    assert first.irn == second.irn                        # same IRN, not re-issued
    assert first.id == second.id


def test_generate_rejects_draft_invoice(db, demo):
    inv = SalesInvoice(company_id=demo["company_id"], customer_id=demo["customer_id"],
                       number="INV-EI-DRAFT", invoice_date=date(2026, 11, 7))
    inv.lines.append(SalesInvoiceLine(line_no=1, description="x", qty=Decimal("1"),
                                      rate=Decimal("1"), tax_code_id=demo["tax_id"]))
    db.add(inv); db.flush()
    with pytest.raises(svc.EInvoiceServiceError):
        svc.generate_einvoice(db, inv.id)


def test_cancel_einvoice(db, demo):
    inv = _posted_invoice(db, demo)
    svc.generate_einvoice(db, inv.id); db.commit()
    rec = svc.cancel_einvoice(db, inv.id, reason_code="2", remarks="Data entry error")
    db.commit()
    assert rec.status == EInvoiceStatus.CANCELLED and rec.cancelled_at is not None
    # a cancelled IRN cannot be regenerated on the same invoice
    with pytest.raises(svc.EInvoiceServiceError):
        svc.generate_einvoice(db, inv.id)


# --------------------------------- API --------------------------------------

def test_einvoice_api_generate_status_and_config(client, auth_headers, db, demo):
    inv = _posted_invoice(db, demo, number="INV-EI-API")
    db.commit()

    cfg = client.get("/api/einvoice/config", headers=auth_headers).json()
    assert cfg["mode"] == "simulator" and cfg["live"] is False

    gen = client.post(f"/api/einvoice/{inv.id}/generate", headers=auth_headers)
    assert gen.status_code == 201, gen.text
    body = gen.json()
    assert body["status"] == "GENERATED" and len(body["irn"]) == 64

    got = client.get(f"/api/einvoice/{inv.id}", headers=auth_headers).json()
    assert got["irn"] == body["irn"]
