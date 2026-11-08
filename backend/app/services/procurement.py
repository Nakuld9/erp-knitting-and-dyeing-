"""Procurement posting: a chemical/dye GRN creates stock layers and a GST
purchase voucher, atomically and idempotently."""
from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.commercial import DocStatus, GoodsReceiptNote
from app.models.core import Company
from app.models.masters import Chemical, Supplier, TaxCode
from app.models.inventory import ItemType
from app.services import inventory
from app.services.accounting import posting_engine as pe
from app.services.accounting.posting_rules import resolve_account
from app.services.tax import compute_gst


def _intra_state(company: Company, supplier: Supplier) -> bool:
    if company.state_code and supplier.state_code:
        return company.state_code == supplier.state_code
    return True  # default to intra-state when unknown


def post_chemical_grn(db: Session, grn: GoodsReceiptNote, *, created_by: uuid.UUID | None = None) -> object:
    ref = grn.posting_reference or f"GRN:{grn.number}"
    existing = pe.find_existing(db, grn.company_id, ref)
    if existing is not None:
        return existing

    company = db.get(Company, grn.company_id)
    supplier = db.get(Supplier, grn.supplier_id)
    intra = _intra_state(company, supplier)

    debits_by_account: dict[str, Decimal] = defaultdict(lambda: quantize(0, MONEY_SCALE))
    cgst = sgst = igst = quantize(0, MONEY_SCALE)

    for line in grn.lines:
        chem = db.get(Chemical, line.chemical_id)
        line_taxable = quantize(Decimal(str(line.qty)) * Decimal(str(line.unit_cost)), MONEY_SCALE)
        inv_acct = "1090" if (chem and chem.is_dye) else resolve_account(
            db, grn.company_id, "CHEMICAL_GRN", "inventory"
        )
        debits_by_account[inv_acct] += line_taxable

        inventory.receive(
            db, company_id=grn.company_id, item_type=ItemType.CHEMICAL, item_id=line.chemical_id,
            qty=line.qty, unit_cost=line.unit_cost, lot_id=line.chemical_lot_id,
            warehouse_id=line.warehouse_id, event_type="GRN",
            source_module="grn", source_id=str(grn.id), posting_reference=ref,
        )

        tax_code = db.get(TaxCode, line.tax_code_id) if line.tax_code_id else None
        g = compute_gst(tax_code, line_taxable, intra_state=intra)
        cgst += g["cgst"]; sgst += g["sgst"]; igst += g["igst"]

    lines = [pe.PostingLine(account_code=acct, debit=amt, narration="GRN inventory")
             for acct, amt in debits_by_account.items()]
    if cgst > 0:
        lines.append(pe.PostingLine(account_code=resolve_account(db, grn.company_id, "CHEMICAL_GRN", "input_cgst"), debit=cgst, narration="Input CGST"))
    if sgst > 0:
        lines.append(pe.PostingLine(account_code=resolve_account(db, grn.company_id, "CHEMICAL_GRN", "input_sgst"), debit=sgst, narration="Input SGST"))
    if igst > 0:
        lines.append(pe.PostingLine(account_code=resolve_account(db, grn.company_id, "CHEMICAL_GRN", "input_igst"), debit=igst, narration="Input IGST"))

    gross = sum((l.debit for l in lines), quantize(0, MONEY_SCALE))
    lines.append(pe.PostingLine(
        account_code=resolve_account(db, grn.company_id, "CHEMICAL_GRN", "payable"),
        credit=gross, narration="Supplier payable", party_type="supplier", party_id=grn.supplier_id,
    ))

    voucher = pe.post_voucher(db, pe.PostingRequest(
        company_id=grn.company_id, voucher_type_code="PI", voucher_date=grn.grn_date,
        posting_reference=ref, narration=f"Chemical GRN {grn.number}",
        source_module="grn", source_id=str(grn.id), created_by=created_by,
        lines=lines,
    ))
    grn.posting_reference = ref
    grn.status = DocStatus.POSTED
    db.flush()

    # Roll the linked purchase order up to PARTIAL / COMPLETED on receipt.
    if grn.purchase_order_id:
        from app.models.commercial import PurchaseOrder
        from app.services import order_lifecycle
        po = db.get(PurchaseOrder, grn.purchase_order_id)
        if po is not None and po.company_id == grn.company_id:
            order_lifecycle.refresh_po_status(db, po)

    return voucher
