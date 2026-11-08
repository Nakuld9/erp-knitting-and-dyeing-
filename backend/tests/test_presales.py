"""Pre-sales pipeline, credit limits, and party statements."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.commercial import DocStatus, SalesInvoice, SalesInvoiceLine
from app.models.masters import Customer
from app.services import order_lifecycle as ol
from app.services import presales, sales
from app.services.accounting import statements
from tests.helpers import L, post


# ------------------------------ quotation flow ------------------------------

def test_enquiry_quote_convert_to_so(db, demo):
    e = presales.create_enquiry(db, company_id=demo["company_id"], customer_id=demo["customer_id"],
                                number="ENQ-1", enquiry_date=date(2026, 11, 8), description="100kg navy SJ")
    q = presales.create_quotation(
        db, company_id=demo["company_id"], customer_id=demo["customer_id"], number="QT-1",
        quote_date=date(2026, 11, 8), enquiry_id=e.id,
        lines=[{"fabric_type_id": demo["fabric_type_id"], "qty_kg": "100", "rate": "300",
                "tax_code_id": demo["tax_id"]}])
    db.commit()
    assert e.status == DocStatus.PARTIAL                 # marked quoted

    res = presales.convert_quotation_to_so(db, company_id=demo["company_id"], quotation=q,
                                           number="SO-FROM-Q", order_date=date(2026, 11, 8))
    db.commit()
    assert res["so_number"] == "SO-FROM-Q"
    db.refresh(q); db.refresh(e)
    assert q.status == DocStatus.COMPLETED and q.sales_order_id is not None
    assert e.status == DocStatus.COMPLETED


def test_quotation_cannot_convert_twice(db, demo):
    q = presales.create_quotation(
        db, company_id=demo["company_id"], customer_id=demo["customer_id"], number="QT-2",
        quote_date=date(2026, 11, 8),
        lines=[{"fabric_type_id": demo["fabric_type_id"], "qty_kg": "50", "rate": "300"}])
    presales.convert_quotation_to_so(db, company_id=demo["company_id"], quotation=q,
                                     number="SO-Q2", order_date=date(2026, 11, 8))
    db.commit()
    try:
        presales.convert_quotation_to_so(db, company_id=demo["company_id"], quotation=q,
                                         number="SO-Q2B", order_date=date(2026, 11, 8))
        assert False, "expected PresalesError"
    except presales.PresalesError:
        db.rollback()


# ------------------------------- credit limit -------------------------------

def _set_limit(db, demo, amount):
    c = db.get(Customer, demo["customer_id"])
    c.credit_limit = Decimal(amount)
    db.flush()


def _so(db, demo, number, qty="100", rate="300"):
    return ol.create_sales_order(db, company_id=demo["company_id"], customer_id=demo["customer_id"],
                                 number=number, order_date=date(2026, 11, 8),
                                 lines=[{"fabric_type_id": demo["fabric_type_id"], "qty_kg": qty,
                                         "rate": rate, "tax_code_id": demo["tax_id"]}])


def test_credit_limit_blocks_over_exposure(db, demo):
    _set_limit(db, demo, "10000")        # 100kg @ 300 + 18% = 35,400 > 10,000
    so = _so(db, demo, "SO-CL-1")
    db.commit()
    try:
        ol.confirm_sales_order(db, so)
        assert False, "expected LifecycleError (credit limit)"
    except ol.LifecycleError as exc:
        assert "Credit limit exceeded" in str(exc)
        db.rollback()


def test_credit_limit_allows_within(db, demo):
    _set_limit(db, demo, "50000")        # 35,400 <= 50,000
    so = _so(db, demo, "SO-CL-2")
    ol.confirm_sales_order(db, so)
    db.commit()
    assert so.status == DocStatus.CONFIRMED


def test_zero_limit_means_unlimited(db, demo):
    _set_limit(db, demo, "0")
    so = _so(db, demo, "SO-CL-3")
    ol.confirm_sales_order(db, so)
    db.commit()
    assert so.status == DocStatus.CONFIRMED


# ------------------------------- statements ---------------------------------

def _post_invoice(db, demo, number, qty="100", rate="300"):
    inv = SalesInvoice(company_id=demo["company_id"], customer_id=demo["customer_id"],
                       number=number, invoice_date=date(2026, 11, 8))
    inv.lines.append(SalesInvoiceLine(line_no=1, qty=Decimal(qty), rate=Decimal(rate),
                                      tax_code_id=demo["tax_id"]))
    db.add(inv); db.flush()
    sales.post_sales_invoice(db, inv)
    db.commit()
    return inv


def test_customer_statement_running_balance(db, demo):
    _post_invoice(db, demo, "SI-STMT-1")          # +35,400 receivable
    # a receipt: Dr Bank 20,000 / Cr Receivable 20,000 (party customer)
    post(db, demo["company_id"], [L("1020", debit="20000"),
         L("1040", credit="20000", party_type="customer", party_id=demo["customer_id"])],
         ref="RCPT-1", vtype="RV")
    st = statements.customer_statement(db, demo["company_id"], demo["customer_id"],
                                       date(2026, 4, 1), date(2027, 3, 31))
    assert len(st["rows"]) == 2
    # closing = 35,400 - 20,000 = 15,400
    assert st["closing"] == Decimal("15400.00")
    assert statements.customer_outstanding(db, demo["company_id"], demo["customer_id"]) == Decimal("15400.00")
