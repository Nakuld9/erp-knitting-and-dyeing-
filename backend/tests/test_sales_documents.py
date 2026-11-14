"""Stage 3: Sales GST Invoice + Delivery Challan printing.

Builds the real chain (yarn -> knit -> grey -> dye -> dyed rolls -> sales order ->
partial dispatch -> GST invoice) and verifies the printed documents read the posted
data, that printing posts nothing, GST intra/inter split, multi-roll + partial +
multiple dispatch, cross references, dyed-roll traceability, reprint/cancel, and
company isolation.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.models.accounting import Voucher
from app.models.commercial import DeliveryNote, DocStatus, SalesInvoice, SalesInvoiceLine
from app.models.core import Company
from app.models.dyeing_cost import DyeingOutput
from app.models.inventory import InventoryTransaction
from app.services import order_lifecycle as ol
from app.services.bom import production, service as bom
from app.services.dyeing import service as dyeing
from app.services.printing import sales_documents as sdoc
from app.services.printing.pdf import render_pdf
from app.services.sales import post_sales_invoice

VD = date(2026, 6, 15)


def _dyed_rolls(db, demo, *, code, lots):
    cid = demo["company_id"]
    b = bom.create_bom(db, company_id=cid, code=code, name=code)
    v = b.versions[0]
    bom.set_yarns(db, v.id, [{"yarn_id": demo["yarn_id"], "blend_percent": Decimal("100"),
                              "std_consumption_per_kg": Decimal("1.0"), "std_unit_cost": Decimal("240")}])
    bom.approve_version(db, version_id=v.id, approved_by=None)
    o = production.create_order(db, company_id=cid, bom_version_id=v.id,
                                planned_qty_kg=Decimal("100"), number="PO-" + code, order_date=VD)
    r = o.reservations[0]
    production.issue_material(db, order_id=o.id, reservation_id=r.id, qty=r.planned_qty, voucher_date=VD)
    production.complete_order(db, order_id=o.id, produced_qty_kg=Decimal("100"))
    production.record_actual_knitting_conversion_cost(db, order_id=o.id, amount=Decimal("600"))
    production.value_grey_output(db, order_id=o.id, voucher_date=VD)
    job = dyeing.create_job(db, company_id=cid, number="DJ-" + code, mode="INTERNAL",
                            grey_production_order_id=o.id, shade_id=demo["shade_id"])
    dyeing.issue_grey(db, job_id=job.id, source_production_order_id=o.id, qty_kg=Decimal("100"), voucher_date=VD)
    dyeing.record_costs(db, job_id=job.id, dyeing_charge=Decimal("3000"), chemical_cost=Decimal("1500"))
    dyeing.record_output(db, job_id=job.id, lots=lots)
    dyeing.complete_job(db, job_id=job.id, voucher_date=VD)
    outs = {x.lot_code: x for x in db.execute(
        select(DyeingOutput).where(DyeingOutput.job_id == job.id)).scalars()}
    return o, job, outs


def _invoice(db, demo, *, number, qty, rate, state_code=None):
    """Post a GST sales invoice. state_code lets us force inter-state."""
    cid = demo["company_id"]
    customer_id = demo["customer_id"]
    if state_code is not None:
        from app.models.masters import Customer
        c = db.get(Customer, customer_id); c.state_code = state_code; db.flush()
    inv = SalesInvoice(company_id=cid, customer_id=customer_id, number=number, invoice_date=VD)
    inv.lines.append(SalesInvoiceLine(line_no=1, description="Single Jersey 160 — Navy",
                                      qty=Decimal(qty), rate=Decimal(rate), tax_code_id=demo["tax_id"]))
    db.add(inv)
    post_sales_invoice(db, inv)
    db.commit()
    return inv


# ---- invoice: intra-state (CGST+SGST) ----

def test_invoice_intrastate_cgst_sgst(client, auth_headers, db, demo):
    inv = _invoice(db, demo, number="INV-INTRA", qty="100", rate="400")   # customer state 03 == company 03
    doc = sdoc.build_sales_invoice_document(db, demo["company_id"], inv.id)
    labels = [l for l, _ in doc.tax_lines]
    assert "CGST" in labels and "SGST" in labels and "IGST" not in labels
    assert doc.title == "TAX INVOICE" and doc.number == "INV-INTRA"
    # grand total 40000 + 18% = 47200
    grand = dict(doc.totals)["Grand Total"]
    assert grand == "47,200.00"
    assert any("in words" in n.lower() and "forty-seven thousand two hundred" in n.lower() for n in doc.notes)


def test_invoice_interstate_igst(client, auth_headers, db, demo):
    inv = _invoice(db, demo, number="INV-INTER", qty="100", rate="400", state_code="27")  # different state
    doc = sdoc.build_sales_invoice_document(db, demo["company_id"], inv.id)
    labels = [l for l, _ in doc.tax_lines]
    assert "IGST" in labels and "CGST" not in labels
    assert dict(doc.totals)["Grand Total"] == "47,200.00"


def test_invoice_pdf_has_branding_and_renders(client, auth_headers, db, demo):
    inv = _invoice(db, demo, number="INV-PDF", qty="60", rate="400")
    r = client.get(f"/api/print/sales-invoice/{inv.id}", headers=auth_headers)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert r.headers["content-disposition"].startswith("inline")
    # company legal name resolved into the letterhead
    doc = sdoc.build_sales_invoice_document(db, demo["company_id"], inv.id)
    assert doc.letterhead.legal_name == "Demo Textile Mills Private Limited"


# ---- challan: multi-roll, partial + multiple dispatch, traceability ----

def test_challan_multiroll_partial_and_traceability(client, auth_headers, db, demo):
    cid = demo["company_id"]
    o, job, outs = _dyed_rolls(db, demo, code="CH1",
                               lots=[{"lot_code": "A", "qty_kg": Decimal("60")},
                                     {"lot_code": "B", "qty_kg": Decimal("40")}])
    so = ol.create_sales_order(db, company_id=cid, customer_id=demo["customer_id"], number="SO-CH1",
                               order_date=VD, lines=[{"fabric_type_id": demo["fabric_type_id"],
                                                      "shade_id": demo["shade_id"], "qty_kg": "100", "rate": "400"}])
    # partial dispatch: lot A only (60 of 100)
    ol.create_delivery(db, company_id=cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                       number="DN-CH1-A", delivery_date=VD, roll_ids=[outs["A"].dyed_item_id])
    db.commit()
    dn_a = db.execute(select(DeliveryNote).where(DeliveryNote.number == "DN-CH1-A")).scalar_one()
    doc = sdoc.build_delivery_challan_document(db, cid, dn_a.id)
    assert doc.title == "DELIVERY CHALLAN"
    # printed qty is the ACTUAL dispatched (60), not the SO qty (100)
    assert dict(doc.totals)["Total dispatched (kg)"] == "60.000"
    assert dict(doc.totals)["Rolls"] == "1"
    # traceability: roll carries shade + dye lot from the dyeing output
    row = doc.rows[0]
    assert row["lot"] == "A" and row["shade"] != "—"
    # references include the sales order
    assert any(l == "Sales Order" and v == "SO-CH1" for l, v in doc.references)

    # second dispatch against the SAME sales order (lot B)
    ol.create_delivery(db, company_id=cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                       number="DN-CH1-B", delivery_date=VD, roll_ids=[outs["B"].dyed_item_id])
    db.commit()
    dn_b = db.execute(select(DeliveryNote).where(DeliveryNote.number == "DN-CH1-B")).scalar_one()
    doc_b = sdoc.build_delivery_challan_document(db, cid, dn_b.id)
    assert dict(doc_b.totals)["Total dispatched (kg)"] == "40.000"   # only lot B on this challan


def test_challan_lists_multiple_rolls(client, auth_headers, db, demo):
    cid = demo["company_id"]
    o, job, outs = _dyed_rolls(db, demo, code="CH2",
                               lots=[{"lot_code": "A", "qty_kg": Decimal("60")},
                                     {"lot_code": "B", "qty_kg": Decimal("40")}])
    so = ol.create_sales_order(db, company_id=cid, customer_id=demo["customer_id"], number="SO-CH2",
                               order_date=VD, lines=[{"fabric_type_id": demo["fabric_type_id"],
                                                      "shade_id": demo["shade_id"], "qty_kg": "100", "rate": "400"}])
    ol.create_delivery(db, company_id=cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                       number="DN-CH2", delivery_date=VD,
                       roll_ids=[outs["A"].dyed_item_id, outs["B"].dyed_item_id])
    db.commit()
    dn = db.execute(select(DeliveryNote).where(DeliveryNote.number == "DN-CH2")).scalar_one()
    doc = sdoc.build_delivery_challan_document(db, cid, dn.id)
    assert len(doc.rows) == 2
    assert dict(doc.totals)["Total dispatched (kg)"] == "100.000"


def test_challan_references_invoice_for_same_so(client, auth_headers, db, demo):
    cid = demo["company_id"]
    o, job, outs = _dyed_rolls(db, demo, code="CH3", lots=[{"lot_code": "A", "qty_kg": Decimal("95")}])
    so = ol.create_sales_order(db, company_id=cid, customer_id=demo["customer_id"], number="SO-CH3",
                               order_date=VD, lines=[{"fabric_type_id": demo["fabric_type_id"],
                                                      "shade_id": demo["shade_id"], "qty_kg": "95", "rate": "400"}])
    ol.create_delivery(db, company_id=cid, customer_id=demo["customer_id"], sales_order_id=so.id,
                       number="DN-CH3", delivery_date=VD, roll_ids=[outs["A"].dyed_item_id])
    inv = SalesInvoice(company_id=cid, customer_id=demo["customer_id"], number="INV-CH3",
                       invoice_date=VD, sales_order_id=so.id)
    inv.lines.append(SalesInvoiceLine(line_no=1, description="Dyed fabric", qty=Decimal("95"),
                                      rate=Decimal("400"), tax_code_id=demo["tax_id"]))
    db.add(inv); post_sales_invoice(db, inv); db.commit()
    dn = db.execute(select(DeliveryNote).where(DeliveryNote.number == "DN-CH3")).scalar_one()
    doc = sdoc.build_delivery_challan_document(db, cid, dn.id)
    assert any(l == "Invoice" and v == "INV-CH3" for l, v in doc.references)


# ---- printing posts nothing ----

def test_printing_posts_no_transaction(client, auth_headers, db, demo):
    cid = demo["company_id"]
    inv = _invoice(db, demo, number="INV-NOTX", qty="50", rate="400")
    v0 = db.execute(select(func.count()).select_from(Voucher).where(Voucher.company_id == cid)).scalar()
    i0 = db.execute(select(func.count()).select_from(InventoryTransaction).where(
        InventoryTransaction.company_id == cid)).scalar()
    inv0 = db.execute(select(func.count()).select_from(SalesInvoice).where(SalesInvoice.company_id == cid)).scalar()
    for _ in range(3):
        client.get(f"/api/print/sales-invoice/{inv.id}", headers=auth_headers)
        client.get(f"/api/print/sales-invoice/{inv.id}/preview", headers=auth_headers)
    assert db.execute(select(func.count()).select_from(Voucher).where(Voucher.company_id == cid)).scalar() == v0
    assert db.execute(select(func.count()).select_from(InventoryTransaction).where(
        InventoryTransaction.company_id == cid)).scalar() == i0
    assert db.execute(select(func.count()).select_from(SalesInvoice).where(
        SalesInvoice.company_id == cid)).scalar() == inv0


# ---- reprint reuses number; cancel shows watermark ----

def test_reprint_tracks_and_reuses_number(client, auth_headers, db, demo):
    inv = _invoice(db, demo, number="INV-RP", qty="10", rate="400")
    first = client.get(f"/api/print/sales-invoice/{inv.id}", headers=auth_headers)
    second = client.get(f"/api/print/sales-invoice/{inv.id}", headers=auth_headers)
    assert first.status_code == second.status_code == 200
    from app.models.printing import PrintedDocument
    row = db.execute(select(PrintedDocument).where(PrintedDocument.source_id == str(inv.id))).scalar_one()
    assert row.number == "INV-RP" and row.reprint_count >= 1   # same number, reprint recorded


def test_cancelled_invoice_renders_watermark(client, auth_headers, db, demo):
    inv = _invoice(db, demo, number="INV-CANC", qty="10", rate="400")
    inv.status = DocStatus.CANCELLED; db.commit()
    doc = sdoc.build_sales_invoice_document(db, demo["company_id"], inv.id)
    assert doc.is_cancelled is True
    # still renders a valid PDF
    assert render_pdf(doc)[:4] == b"%PDF"


# ---- company isolation ----

def test_invoice_not_visible_across_companies(client, auth_headers, db, demo):
    inv = _invoice(db, demo, number="INV-ISO", qty="10", rate="400")
    b = db.execute(select(Company).where(Company.code == "UATB")).scalar_one_or_none()
    if b is None:
        b = Company(code="UATB", name="UAT Weaves", legal_name="UAT Weaves Private Limited",
                    gstin="27ABCDE9999F1Z2", state_code="27", currency="INR")
        db.add(b); db.commit()
    hb = {**auth_headers, "X-Company-Id": str(b.id)}
    # company B cannot print company A's invoice -> 404 (existence not leaked)
    assert client.get(f"/api/print/sales-invoice/{inv.id}", headers=hb).status_code == 404
