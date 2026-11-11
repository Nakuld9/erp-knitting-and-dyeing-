"""Banking: receipts/payments/contra postings, CSV statement import, and
bank reconciliation."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.accounting import ChartOfAccount, JournalLine, Voucher, VoucherStatus
from app.models.banking import BankReconciliation, BankStatementLine
from app.models.masters import BankAccount
from app.services.accounting import posting_engine as pe

ZERO = quantize(0, MONEY_SCALE)
_POSTED = [s.value for s in (VoucherStatus.POSTED, VoucherStatus.REVERSED, VoucherStatus.REVERSAL)]


def _bank_coa_code(db: Session, bank_account_id: uuid.UUID) -> tuple[BankAccount, str]:
    ba = db.get(BankAccount, bank_account_id)
    if ba is None or ba.coa_account_id is None:
        raise ValueError("Bank account is not linked to a chart-of-accounts account")
    acct = db.get(ChartOfAccount, ba.coa_account_id)
    return ba, acct.code


# --------------------------- postings ---------------------------------------

def customer_receipt(db, *, company_id, bank_account_id, customer_id, amount, vdate, ref,
                     created_by=None, ar_code="1040"):
    from app.core.tenancy import require_company_record
    from app.models.masters import BankAccount, Customer
    require_company_record(db, BankAccount, bank_account_id, company_id, label="Bank account")
    require_company_record(db, Customer, customer_id, company_id, label="Customer")
    _ba, bank_code = _bank_coa_code(db, bank_account_id)
    amount = quantize(amount, MONEY_SCALE)
    return pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="RV", voucher_date=vdate, posting_reference=ref,
        narration="Customer receipt", source_module="banking", created_by=created_by,
        lines=[pe.PostingLine(bank_code, debit=amount, narration="Bank"),
               pe.PostingLine(ar_code, credit=amount, party_type="customer", party_id=customer_id)],
    ))


def supplier_payment(db, *, company_id, bank_account_id, supplier_id, amount, vdate, ref,
                     created_by=None, ap_code="2010"):
    from app.core.tenancy import require_company_record
    from app.models.masters import BankAccount, Supplier
    require_company_record(db, BankAccount, bank_account_id, company_id, label="Bank account")
    require_company_record(db, Supplier, supplier_id, company_id, label="Supplier")
    _ba, bank_code = _bank_coa_code(db, bank_account_id)
    amount = quantize(amount, MONEY_SCALE)
    return pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="PV", voucher_date=vdate, posting_reference=ref,
        narration="Supplier payment", source_module="banking", created_by=created_by,
        lines=[pe.PostingLine(ap_code, debit=amount, party_type="supplier", party_id=supplier_id),
               pe.PostingLine(bank_code, credit=amount, narration="Bank")],
    ))


def contra(db, *, company_id, from_bank_account_id, to_bank_account_id, amount, vdate, ref,
           created_by=None):
    from app.core.tenancy import require_company_record
    from app.models.masters import BankAccount
    require_company_record(db, BankAccount, from_bank_account_id, company_id, label="From bank account")
    require_company_record(db, BankAccount, to_bank_account_id, company_id, label="To bank account")
    _f, from_code = _bank_coa_code(db, from_bank_account_id)
    _t, to_code = _bank_coa_code(db, to_bank_account_id)
    amount = quantize(amount, MONEY_SCALE)
    return pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="CV", voucher_date=vdate, posting_reference=ref,
        narration="Contra / fund transfer", source_module="banking", created_by=created_by,
        lines=[pe.PostingLine(to_code, debit=amount), pe.PostingLine(from_code, credit=amount)],
    ))


def bank_charge(db, *, company_id, bank_account_id, amount, vdate, ref, created_by=None,
                expense_code="6100"):
    _ba, bank_code = _bank_coa_code(db, bank_account_id)
    amount = quantize(amount, MONEY_SCALE)
    return pe.post_voucher(db, pe.PostingRequest(
        company_id=company_id, voucher_type_code="PV", voucher_date=vdate, posting_reference=ref,
        narration="Bank charges", source_module="banking", created_by=created_by,
        lines=[pe.PostingLine(expense_code, debit=amount), pe.PostingLine(bank_code, credit=amount)],
    ))


# --------------------------- CSV import --------------------------------------

def _parse_date(value: str) -> date:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date '{value}'")


def _dec(value: str | None) -> Decimal:
    if value is None or str(value).strip() == "":
        return ZERO
    try:
        return quantize(Decimal(str(value).replace(",", "").strip()), MONEY_SCALE)
    except InvalidOperation as exc:
        raise ValueError(f"Bad amount '{value}'") from exc


def import_statement_csv(db: Session, *, company_id: uuid.UUID, bank_account_id: uuid.UUID,
                         csv_text: str) -> dict:
    """Import statement lines from CSV. Recognised headers (case-insensitive):
    ``date``, ``description``, ``reference``, and either ``amount`` (signed,
    + = deposit) or ``debit``/``credit`` (debit = withdrawal). Idempotent per row.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    headers = {h.lower().strip(): h for h in (reader.fieldnames or [])}
    if "date" not in headers:
        raise ValueError("CSV must have a 'date' column")

    created = skipped = 0
    for i, row in enumerate(reader):
        def g(key):
            return row.get(headers[key]) if key in headers else None

        txn_date = _parse_date(g("date"))
        description = (g("description") or "").strip() or None
        reference = (g("reference") or "").strip() or None
        if "amount" in headers:
            amount = _dec(g("amount"))
        else:
            amount = _dec(g("credit")) - _dec(g("debit"))

        import_ref = f"{i}|{txn_date.isoformat()}|{amount}|{reference or ''}"
        exists = db.execute(
            select(BankStatementLine).where(
                BankStatementLine.company_id == company_id,
                BankStatementLine.bank_account_id == bank_account_id,
                BankStatementLine.import_ref == import_ref,
            )
        ).scalar_one_or_none()
        if exists:
            skipped += 1
            continue
        db.add(BankStatementLine(
            company_id=company_id, bank_account_id=bank_account_id, txn_date=txn_date,
            description=description, reference=reference, amount=amount, import_ref=import_ref,
        ))
        created += 1
    db.flush()
    return {"created": created, "skipped": skipped}


# --------------------------- reconciliation ----------------------------------

def _book_bank_entries(db, company_id, bank_code, as_of):
    """Signed bank-ledger entries (debit-credit = money into bank)."""
    acct = db.execute(
        select(ChartOfAccount).where(ChartOfAccount.company_id == company_id, ChartOfAccount.code == bank_code)
    ).scalar_one()
    entries = []
    for line, voucher in db.execute(
        select(JournalLine, Voucher).join(Voucher, JournalLine.voucher_id == Voucher.id).where(
            Voucher.company_id == company_id, Voucher.status.in_(_POSTED),
            JournalLine.account_id == acct.id, Voucher.voucher_date <= as_of,
        )
    ):
        entries.append({"id": line.id, "date": voucher.voucher_date, "number": voucher.number,
                        "amount": (line.debit or ZERO) - (line.credit or ZERO)})
    return entries


def reconcile(db: Session, *, company_id: uuid.UUID, bank_account_id: uuid.UUID, as_of: date,
              date_window_days: int = 7, save: bool = False) -> dict:
    ba, bank_code = _bank_coa_code(db, bank_account_id)
    book = _book_bank_entries(db, company_id, bank_code, as_of)
    stmt_lines = db.execute(
        select(BankStatementLine).where(
            BankStatementLine.company_id == company_id,
            BankStatementLine.bank_account_id == bank_account_id,
            BankStatementLine.txn_date <= as_of,
        ).order_by(BankStatementLine.txn_date)
    ).scalars().all()

    used_book: set = set()
    matched = []
    for line in stmt_lines:
        # Eligible book entries: same signed amount, within the date window, unused.
        eligible = [
            b for b in book
            if b["id"] not in used_book and b["amount"] == line.amount
            and abs((b["date"] - line.txn_date).days) <= date_window_days
        ]
        text = f"{line.reference or ''} {line.description or ''}"
        # Prefer an entry whose voucher number is quoted on the statement line;
        # otherwise the nearest by date.
        ref_hits = [b for b in eligible if b["number"] and b["number"] in text]
        if ref_hits:
            candidate = ref_hits[0]
        elif eligible:
            candidate = min(eligible, key=lambda b: abs((b["date"] - line.txn_date).days))
        else:
            candidate = None
        if candidate is not None:
            used_book.add(candidate["id"])
            line.is_reconciled = True
            line.matched_journal_line_id = candidate["id"]
            matched.append({"statement_ref": line.reference, "amount": line.amount,
                            "voucher": candidate["number"]})
        else:
            line.is_reconciled = False
            line.matched_journal_line_id = None

    unmatched_statement = [
        {"date": s.txn_date, "description": s.description, "reference": s.reference, "amount": s.amount}
        for s in stmt_lines if not s.is_reconciled
    ]
    unmatched_book = [
        {"date": b["date"], "voucher": b["number"], "amount": b["amount"]}
        for b in book if b["id"] not in used_book
    ]

    book_balance = sum((b["amount"] for b in book), ZERO)
    statement_balance = sum((s.amount for s in stmt_lines), ZERO)
    # Difference between the two must equal the net of the unreconciled items.
    unmatched_book_total = sum((b["amount"] for b in unmatched_book), ZERO)
    unmatched_stmt_total = sum((s["amount"] for s in unmatched_statement), ZERO)
    explained = (book_balance - statement_balance) == (unmatched_book_total - unmatched_stmt_total)
    is_reconciled = not unmatched_statement and not unmatched_book

    db.flush()
    if save:
        db.add(BankReconciliation(
            company_id=company_id, bank_account_id=bank_account_id, as_of=as_of,
            book_balance=book_balance, statement_balance=statement_balance,
            matched_count=len(matched), unmatched_statement_count=len(unmatched_statement),
            unmatched_book_count=len(unmatched_book), is_reconciled=is_reconciled,
        ))
        db.flush()

    return {
        "bank_account": ba.name, "as_of": as_of,
        "book_balance": book_balance, "statement_balance": statement_balance,
        "matched": matched, "matched_count": len(matched),
        "unmatched_statement": unmatched_statement, "unmatched_book": unmatched_book,
        "difference_explained": explained, "is_reconciled": is_reconciled,
    }
