"""Pre-sales pipeline: enquiries, quotations, and converting a quote to an SO."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.commercial import (
    DocStatus,
    Enquiry,
    Quotation,
    QuotationLine,
)
from app.services import order_lifecycle as ol


class PresalesError(Exception):
    """An invalid pre-sales operation."""


def create_enquiry(db: Session, *, company_id: uuid.UUID, customer_id: uuid.UUID, number: str,
                   enquiry_date: date, description: str | None = None,
                   created_by: uuid.UUID | None = None) -> Enquiry:
    e = Enquiry(company_id=company_id, customer_id=customer_id, number=number,
                enquiry_date=enquiry_date, description=description, status=DocStatus.DRAFT,
                created_by=created_by)
    db.add(e)
    db.flush()
    return e


def create_quotation(db: Session, *, company_id: uuid.UUID, customer_id: uuid.UUID, number: str,
                     quote_date: date, lines: list[dict], enquiry_id: uuid.UUID | None = None,
                     valid_until: date | None = None, created_by: uuid.UUID | None = None) -> Quotation:
    q = Quotation(company_id=company_id, customer_id=customer_id, enquiry_id=enquiry_id, number=number,
                  quote_date=quote_date, valid_until=valid_until, status=DocStatus.DRAFT, created_by=created_by)
    for i, ln in enumerate(lines, start=1):
        q.lines.append(QuotationLine(
            line_no=i, fabric_type_id=ln.get("fabric_type_id"), description=ln.get("description"),
            qty_kg=Decimal(str(ln.get("qty_kg", 0))), rate=Decimal(str(ln.get("rate", 0))),
            tax_code_id=ln.get("tax_code_id")))
    db.add(q)
    db.flush()
    if enquiry_id:
        e = db.get(Enquiry, enquiry_id)
        if e is not None and e.company_id == company_id:
            e.status = DocStatus.PARTIAL   # quoted
    return q


def convert_quotation_to_so(db: Session, *, company_id: uuid.UUID, quotation: Quotation, number: str,
                            order_date: date, created_by: uuid.UUID | None = None) -> dict:
    """Create a sales order from a quotation and mark the quote converted."""
    if quotation.status == DocStatus.COMPLETED or quotation.sales_order_id is not None:
        raise PresalesError("Quotation is already converted")
    if quotation.status == DocStatus.CANCELLED:
        raise PresalesError("Quotation is cancelled")
    if not quotation.lines:
        raise PresalesError("Quotation has no lines")

    so = ol.create_sales_order(
        db, company_id=company_id, customer_id=quotation.customer_id, number=number,
        order_date=order_date,
        lines=[{"fabric_type_id": ln.fabric_type_id, "qty_kg": ln.qty_kg, "rate": ln.rate,
                "tax_code_id": ln.tax_code_id, "description": ln.description} for ln in quotation.lines],
        created_by=created_by,
    )
    quotation.status = DocStatus.COMPLETED
    quotation.sales_order_id = so.id
    if quotation.enquiry_id:
        e = db.get(Enquiry, quotation.enquiry_id)
        if e is not None:
            e.status = DocStatus.COMPLETED
    db.flush()
    return {"sales_order_id": str(so.id), "so_number": so.number, "quotation_status": quotation.status}
