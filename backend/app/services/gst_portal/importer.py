"""GST return import: GSTR-2A/2B JSON, canonical Excel, and electronic ledgers.

Imports a portal file (downloaded manually, or via an authorised GSP connector) and
stores:

* the ORIGINAL bytes + SHA-256 (retained for audit) as a ``GstReturnImport``;
* parsed ``GstPortalDocument`` rows (for GSTR-2A / GSTR-2B), or
* parsed ``GstElectronicLedgerBalance`` rows (for credit/cash/liability ledgers).

Identical re-imports (same company + return type + period + file hash) are rejected.
A different file for the same period supersedes the previous import but both are
retained. GSTR-2A (dynamic) and GSTR-2B (static) are stored separately and never
merged. The JSON parser is tolerant of the two common GSTN item shapes
(``items``/``txval,igst,...`` and ``itms[].itm_det``/``iamt,camt,...``).
"""
from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import MONEY_SCALE, quantize
from app.models.core import Company
from app.models.gst_itc import (
    GstElectronicLedgerBalance,
    GstImportSource,
    GstImportStatus,
    GstPortalDocument,
    GstReturnImport,
    GstReturnType,
    PortalDocCategory,
    PortalDocType,
)
from app.models.tenancy import GSTRegistration
from app.services.audit import record_audit
from app.services.gst_portal import connector as conn

MZERO = quantize(0, MONEY_SCALE)
GST_IMPORT_TEMPLATE_VERSION = "1"
_MARKER = "GST_IMPORT_TEMPLATE"

GST_DOC_COLUMNS = [
    "category", "doc_type", "supplier_gstin", "supplier_name", "doc_number", "doc_date",
    "taxable_value", "igst", "cgst", "sgst", "cess", "total_value", "rate", "place_of_supply",
    "reverse_charge", "itc_available", "is_amendment", "original_doc_number", "original_doc_date",
]

# docdata category -> (PortalDocCategory, default doc_type, is_amendment)
_CATEGORY_MAP = {
    "b2b": (PortalDocCategory.B2B, PortalDocType.INV, False),
    "b2ba": (PortalDocCategory.B2BA, PortalDocType.INV, True),
    "cdnr": (PortalDocCategory.CDNR, PortalDocType.CN, False),
    "cdnra": (PortalDocCategory.CDNRA, PortalDocType.CN, True),
    "impg": (PortalDocCategory.IMPG, PortalDocType.BOE, False),
    "impgsez": (PortalDocCategory.IMPGSEZ, PortalDocType.BOE, False),
    "isd": (PortalDocCategory.ISD, PortalDocType.ISD, False),
    "isda": (PortalDocCategory.ISDA, PortalDocType.ISD, True),
}
_LEDGER_RETURN_TYPES = {
    GstReturnType.ECL_CREDIT.value: "CREDIT",
    GstReturnType.ECL_CASH.value: "CASH",
    GstReturnType.ELIABILITY.value: "LIABILITY",
}


class GstPortalError(ValueError):
    """An invalid GST import/portal request."""


class DuplicateImport(GstPortalError):
    """This exact file was already imported for this company/type/period."""


class GstParseError(GstPortalError):
    """The uploaded file could not be parsed as the requested return type."""


# ------------------------------- helpers ------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _m(raw):
    if raw is None or str(raw).strip() == "":
        return MZERO
    try:
        return quantize(Decimal(str(raw).strip()), MONEY_SCALE)
    except (InvalidOperation, ValueError):
        return MZERO


def _s(raw, n=255):
    if raw is None:
        return None
    t = str(raw).strip()
    return t[:n] if t else None


def _as_date(raw):
    if raw is None or str(raw).strip() == "":
        return None
    if isinstance(raw, (date, datetime)):
        return raw.date() if isinstance(raw, datetime) else raw
    s = str(raw).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _as_bool(raw):
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "t") if raw is not None else False


def resolve_company_gstin(db: Session, company_id: uuid.UUID, gstin: str | None) -> str:
    """Return a GSTIN that belongs to the company. If ``gstin`` is given it must be
    the company's own (Company.gstin or a GSTRegistration); otherwise the company's
    primary GSTIN is used. Prevents mixing another company's GST data."""
    company = db.get(Company, company_id)
    if company is None:
        raise GstPortalError("unknown company")
    owned = set()
    if getattr(company, "gstin", None):
        owned.add(company.gstin.strip().upper())
    for reg in db.execute(select(GSTRegistration).where(
            GSTRegistration.company_id == company_id)).scalars().all():
        if reg.gstin:
            owned.add(reg.gstin.strip().upper())
    if gstin:
        g = gstin.strip().upper()
        if owned and g not in owned:
            raise GstPortalError(
                f"GSTIN {g} is not registered to this company; refusing cross-company GST data")
        return g
    if not owned:
        raise GstPortalError("company has no GSTIN configured; set one or pass an explicit gstin")
    # Prefer the Company primary GSTIN if present.
    return (getattr(company, "gstin", None) or sorted(owned)[0]).strip().upper()


# ------------------------------ JSON parsing --------------------------------

def _item_tax(item: dict) -> dict:
    det = item.get("itm_det") if isinstance(item.get("itm_det"), dict) else item
    return {
        "txval": _m(det.get("txval")),
        "igst": _m(det.get("igst", det.get("iamt"))),
        "cgst": _m(det.get("cgst", det.get("camt"))),
        "sgst": _m(det.get("sgst", det.get("samt"))),
        "cess": _m(det.get("cess", det.get("csamt"))),
        "rate": _m(det.get("rt")),
    }


def _doc_from_record(rec: dict, *, ctin, trdnm, cat_enum, default_type, is_amendment) -> dict:
    items = rec.get("items") or rec.get("itms") or []
    tax = {"txval": MZERO, "igst": MZERO, "cgst": MZERO, "sgst": MZERO, "cess": MZERO}
    rate = MZERO
    for it in items:
        t = _item_tax(it)
        for k in ("txval", "igst", "cgst", "sgst", "cess"):
            tax[k] = quantize(tax[k] + t[k], MONEY_SCALE)
        rate = rate or t["rate"]
    doc_number = _s(rec.get("inum") or rec.get("nt_num") or rec.get("ntnum")
                    or rec.get("boenum") or rec.get("doc_num"), 40)
    doc_date = _as_date(rec.get("idt") or rec.get("nt_dt") or rec.get("ntdt")
                        or rec.get("boedt") or rec.get("doc_dt"))
    # Note type (credit/debit) overrides the default doc type.
    ntty = (rec.get("typ") or rec.get("ntty") or "").strip().upper()
    doc_type = default_type
    if cat_enum in (PortalDocCategory.CDNR, PortalDocCategory.CDNRA):
        doc_type = PortalDocType.DN if ntty == "D" else PortalDocType.CN
    itcavl = rec.get("itcavl")
    itc_available = None
    if itcavl is not None and str(itcavl).strip() != "":
        itc_available = str(itcavl).strip().upper() in ("Y", "YES", "1", "TRUE")
    total = _m(rec.get("val"))
    if total == MZERO:
        total = quantize(tax["txval"] + tax["igst"] + tax["cgst"] + tax["sgst"] + tax["cess"],
                         MONEY_SCALE)
    return {
        "category": cat_enum.value,
        "doc_type": doc_type.value,
        "supplier_gstin": _s(ctin, 15),
        "supplier_name": _s(trdnm, 160),
        "doc_number": doc_number,
        "doc_date": doc_date,
        "taxable_value": tax["txval"],
        "igst": tax["igst"], "cgst": tax["cgst"], "sgst": tax["sgst"], "cess": tax["cess"],
        "total_value": total,
        "rate": rate,
        "place_of_supply": _s(rec.get("pos"), 40),
        "reverse_charge": _as_bool(rec.get("rev")),
        "itc_available": itc_available,
        "itc_unavailable_reason": _s(rec.get("rsn"), 120),
        "is_amendment": is_amendment,
        "original_doc_number": _s(rec.get("oinum") or rec.get("ont_num"), 40),
        "original_doc_date": _as_date(rec.get("oidt") or rec.get("ont_dt")),
        "raw": rec,
    }


def parse_gstn_json(content: bytes, return_type: str) -> tuple[dict, list[dict]]:
    """Parse a GSTR-2A or GSTR-2B JSON (tolerant of both GSTN item shapes)."""
    try:
        payload = json.loads(content.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise GstParseError(f"not valid JSON: {exc}")
    if not isinstance(payload, dict):
        raise GstParseError("JSON root must be an object")
    # GSTR-2B wraps the document data under data.docdata; GSTR-2A is top-level.
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    docdata = data.get("docdata") if isinstance(data.get("docdata"), dict) else data
    period = _s(data.get("rtnprd") or data.get("fp") or payload.get("rtnprd"), 6)
    gen_raw = data.get("gendt") or payload.get("gendt")
    generated_at = None
    gd = _as_date(gen_raw)
    if gd:
        generated_at = datetime(gd.year, gd.month, gd.day, tzinfo=timezone.utc)

    docs: list[dict] = []
    for key, (cat_enum, default_type, is_amd) in _CATEGORY_MAP.items():
        section = docdata.get(key)
        if not section:
            continue
        if isinstance(section, dict):
            section = [section]
        for group in section:
            ctin = group.get("ctin") or group.get("gstin")
            trdnm = group.get("trdnm") or group.get("nm")
            records = (group.get("inv") or group.get("nt") or group.get("items")
                       or (group if isinstance(group, list) else None))
            # IMPG/ISD groups may themselves be the record (no supplier wrapper).
            if records is None and ("inum" in group or "boenum" in group or "ntnum" in group):
                records = [group]
            for rec in (records or []):
                if not isinstance(rec, dict):
                    continue
                docs.append(_doc_from_record(
                    rec, ctin=ctin, trdnm=trdnm, cat_enum=cat_enum,
                    default_type=default_type, is_amendment=is_amd))
    meta = {"return_period": period, "generated_at": generated_at,
            "gstin_in_file": _s(data.get("gstin") or payload.get("gstin"), 15)}
    return meta, docs


def parse_ledger_json(content: bytes, return_type: str) -> tuple[dict, list[dict]]:
    """Parse a canonical electronic-ledger JSON: {"period":.., "as_of":.., "balances":
    [{"head":"IGST","opening":..,"credit":..,"debit":..,"closing":..}, ...]}."""
    try:
        payload = json.loads(content.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise GstParseError(f"not valid JSON: {exc}")
    if not isinstance(payload, dict):
        raise GstParseError("JSON root must be an object")
    rows = payload.get("balances") or payload.get("bal") or []
    if not isinstance(rows, list):
        raise GstParseError("'balances' must be a list")
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append({
            "head": (_s(r.get("head"), 12) or "").upper(),
            "opening_balance": _m(r.get("opening", r.get("op_bal"))),
            "credit": _m(r.get("credit", r.get("cr"))),
            "debit": _m(r.get("debit", r.get("dr"))),
            "closing_balance": _m(r.get("closing", r.get("cl_bal"))),
            "raw": r,
        })
    meta = {"return_period": _s(payload.get("period") or payload.get("rtnprd"), 6),
            "as_of_date": _as_date(payload.get("as_of") or payload.get("as_on"))}
    return meta, out


# ------------------------------ Excel import --------------------------------

def build_gst_doc_template(return_type: str = "GSTR2B") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = f"{return_type} documents"
    ws["A1"] = f"{_MARKER}|VERSION={GST_IMPORT_TEMPLATE_VERSION}|TYPE={return_type}"
    for idx, col in enumerate(GST_DOC_COLUMNS, start=1):
        ws.cell(row=2, column=idx, value=col)
        ws.column_dimensions[ws.cell(row=2, column=idx).column_letter].width = 18
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def parse_gst_excel(content: bytes, return_type: str) -> tuple[dict, list[dict]]:
    """Parse the canonical GST-document Excel template (our documented layout).

    This is the manual Excel fallback; it does NOT attempt to read the portal's raw
    multi-tab Excel (whose layout is unstable)."""
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise GstParseError(f"not a readable .xlsx workbook: {exc}")
    ws = wb.active
    marker = ws.cell(row=1, column=1).value
    if not marker or not str(marker).startswith(_MARKER):
        raise GstParseError("missing GST_IMPORT_TEMPLATE marker in A1; download a fresh template")
    header = [(_s(c.value, 40) or "") for c in ws[2]]
    header = [h for h in header if h]
    if set(header) != set(GST_DOC_COLUMNS):
        missing = sorted(set(GST_DOC_COLUMNS) - set(header))
        unexpected = sorted(set(header) - set(GST_DOC_COLUMNS))
        raise GstParseError(f"header mismatch. missing={missing} unexpected={unexpected}")
    idx = {name: i for i, name in enumerate(header)}
    docs = []
    for row in ws.iter_rows(min_row=3):
        vals = {name: (row[idx[name]].value if idx[name] < len(row) else None) for name in header}
        if all(v is None or str(v).strip() == "" for v in vals.values()):
            continue
        cat = (_s(vals.get("category"), 10) or "B2B").upper()
        tx = _m(vals.get("taxable_value"))
        igst, cgst, sgst, cess = (_m(vals.get("igst")), _m(vals.get("cgst")),
                                  _m(vals.get("sgst")), _m(vals.get("cess")))
        total = _m(vals.get("total_value")) or quantize(tx + igst + cgst + sgst + cess, MONEY_SCALE)
        docs.append({
            "category": cat,
            "doc_type": (_s(vals.get("doc_type"), 4) or "INV").upper(),
            "supplier_gstin": _s(vals.get("supplier_gstin"), 15),
            "supplier_name": _s(vals.get("supplier_name"), 160),
            "doc_number": _s(vals.get("doc_number"), 40),
            "doc_date": _as_date(vals.get("doc_date")),
            "taxable_value": tx, "igst": igst, "cgst": cgst, "sgst": sgst, "cess": cess,
            "total_value": total, "rate": _m(vals.get("rate")),
            "place_of_supply": _s(vals.get("place_of_supply"), 40),
            "reverse_charge": _as_bool(vals.get("reverse_charge")),
            "itc_available": (None if vals.get("itc_available") in (None, "")
                              else _as_bool(vals.get("itc_available"))),
            "itc_unavailable_reason": None,
            "is_amendment": _as_bool(vals.get("is_amendment")),
            "original_doc_number": _s(vals.get("original_doc_number"), 40),
            "original_doc_date": _as_date(vals.get("original_doc_date")),
            "raw": {k: (v.isoformat() if isinstance(v, (date, datetime)) else v)
                    for k, v in vals.items()},
        })
    wb.close()
    return {"return_period": None, "generated_at": None}, docs


# -------------------------------- import ------------------------------------

def _supersede_prior(db, company_id, return_type, return_period):
    for imp in db.execute(select(GstReturnImport).where(
            GstReturnImport.company_id == company_id,
            GstReturnImport.return_type == return_type,
            GstReturnImport.return_period == return_period,
            GstReturnImport.status == GstImportStatus.IMPORTED.value)).scalars().all():
        imp.status = GstImportStatus.SUPERSEDED.value


def import_return(db: Session, company_id: uuid.UUID, *, gstin: str | None, financial_year: str,
                  return_period: str, return_type: str, content: bytes, file_name: str,
                  source: str, actor: uuid.UUID | None = None, notes: str | None = None) -> dict:
    """Import one portal file. Retains the original bytes + SHA-256; rejects an
    identical re-import; supersedes any prior import for the same type+period."""
    if not content:
        raise GstPortalError("empty file")
    if return_type not in {t.value for t in GstReturnType}:
        raise GstPortalError(f"unknown return_type '{return_type}'")
    gstin = resolve_company_gstin(db, company_id, gstin)
    return_period = (return_period or "").strip()
    if len(return_period) != 6 or not return_period.isdigit():
        raise GstPortalError("return_period must be MMYYYY (6 digits)")
    file_hash = hashlib.sha256(content).hexdigest()

    dup = db.execute(select(GstReturnImport).where(
        GstReturnImport.company_id == company_id, GstReturnImport.return_type == return_type,
        GstReturnImport.return_period == return_period,
        GstReturnImport.file_hash == file_hash)).scalar_one_or_none()
    if dup is not None:
        raise DuplicateImport(
            "this exact file was already imported for this company, return type and period")

    is_ledger = return_type in _LEDGER_RETURN_TYPES
    is_excel = source == GstImportSource.MANUAL_EXCEL.value
    if is_ledger:
        meta, parsed = parse_ledger_json(content, return_type)
    elif is_excel:
        meta, parsed = parse_gst_excel(content, return_type)
    else:
        meta, parsed = parse_gstn_json(content, return_type)

    # A file whose embedded period disagrees with the requested period is rejected
    # (prevents mis-filing a wrong-month file); Excel template carries no period.
    file_period = meta.get("return_period")
    if file_period and file_period != return_period:
        raise GstPortalError(
            f"file is for period {file_period} but {return_period} was requested")

    _supersede_prior(db, company_id, return_type, return_period)
    imp = GstReturnImport(
        company_id=company_id, gstin=gstin, financial_year=financial_year,
        return_period=return_period, return_type=return_type, source=source,
        file_name=(file_name or "upload")[:255], file_hash=file_hash, raw_content=content,
        generated_at=meta.get("generated_at"), doc_count=len(parsed),
        status=GstImportStatus.IMPORTED.value, notes=_s(notes, 255),
        imported_by=actor, imported_at=_now(), created_by=actor, updated_by=actor)
    db.add(imp)
    db.flush()

    if is_ledger:
        ltype = _LEDGER_RETURN_TYPES[return_type]
        for b in parsed:
            db.add(GstElectronicLedgerBalance(
                company_id=company_id, import_id=imp.id, ledger_type=ltype,
                return_period=return_period, as_of_date=meta.get("as_of_date"),
                head=b["head"], opening_balance=b["opening_balance"], credit=b["credit"],
                debit=b["debit"], closing_balance=b["closing_balance"],
                raw=json.dumps(b["raw"], default=str), created_by=actor, updated_by=actor))
        summary = {"return_type": return_type, "ledger_type": ltype, "rows": len(parsed)}
    else:
        by_cat: dict = {}
        tot = {"taxable": MZERO, "igst": MZERO, "cgst": MZERO, "sgst": MZERO, "cess": MZERO}
        for d in parsed:
            db.add(GstPortalDocument(
                company_id=company_id, import_id=imp.id, return_type=return_type,
                return_period=return_period, category=d["category"], doc_type=d["doc_type"],
                supplier_gstin=d["supplier_gstin"], supplier_name=d["supplier_name"],
                doc_number=d["doc_number"], doc_date=d["doc_date"],
                taxable_value=d["taxable_value"], igst=d["igst"], cgst=d["cgst"], sgst=d["sgst"],
                cess=d["cess"], total_value=d["total_value"], rate=d["rate"],
                place_of_supply=d["place_of_supply"], reverse_charge=d["reverse_charge"],
                itc_available=d["itc_available"], itc_unavailable_reason=d["itc_unavailable_reason"],
                is_amendment=d["is_amendment"], original_doc_number=d["original_doc_number"],
                original_doc_date=d["original_doc_date"],
                raw=json.dumps(d["raw"], default=str), created_by=actor, updated_by=actor))
            by_cat[d["category"]] = by_cat.get(d["category"], 0) + 1
            tot["taxable"] = quantize(tot["taxable"] + d["taxable_value"], MONEY_SCALE)
            for h in ("igst", "cgst", "sgst", "cess"):
                tot[h] = quantize(tot[h] + d[h], MONEY_SCALE)
        summary = {"return_type": return_type, "documents": len(parsed),
                   "by_category": by_cat, "totals": {k: str(v) for k, v in tot.items()}}
    imp.summary = json.dumps(summary, default=str)
    db.flush()
    record_audit(db, action="GST_RETURN_IMPORTED", entity="gst_return_import",
                 entity_id=str(imp.id), actor_id=actor,
                 detail={"company_id": str(company_id), "gstin": gstin, "return_type": return_type,
                         "return_period": return_period, "source": source,
                         "file_name": imp.file_name, "file_hash": file_hash,
                         "doc_count": len(parsed)})
    return {"import_id": str(imp.id), "return_type": return_type, "return_period": return_period,
            "gstin": gstin, "source": source, "file_hash": file_hash, "doc_count": len(parsed),
            "imported_at": imp.imported_at.isoformat(), "summary": summary}


def download_and_import(db: Session, company_id: uuid.UUID, *, gstin: str | None,
                        financial_year: str, return_period: str, return_type: str,
                        actor: uuid.UUID | None = None) -> dict:
    """Attempt a live portal download via the configured connector, then import.

    In manual mode (default) or when no authorised GSP integration exists this raises
    ``connector.ConnectorUnavailable`` — callers surface that and use manual import.
    It never fabricates a successful download."""
    gstin = resolve_company_gstin(db, company_id, gstin)
    c = conn.get_connector()
    result = c.download(gstin=gstin, return_period=return_period, return_type=return_type)
    return import_return(
        db, company_id, gstin=gstin, financial_year=financial_year, return_period=return_period,
        return_type=return_type, content=result.content, file_name=result.file_name,
        source=result.source, actor=actor)


def list_imports(db: Session, company_id: uuid.UUID, *, return_type: str | None = None,
                 return_period: str | None = None, limit: int = 100, offset: int = 0):
    stmt = select(GstReturnImport).where(GstReturnImport.company_id == company_id)
    if return_type:
        stmt = stmt.where(GstReturnImport.return_type == return_type)
    if return_period:
        stmt = stmt.where(GstReturnImport.return_period == return_period)
    rows = db.execute(stmt.order_by(GstReturnImport.imported_at.desc())
                      .limit(max(1, min(limit, 500))).offset(max(0, offset))).scalars().all()
    return rows


def get_import(db: Session, company_id: uuid.UUID, import_id: uuid.UUID) -> GstReturnImport:
    imp = db.get(GstReturnImport, import_id)
    if imp is None or imp.company_id != company_id:
        raise GstPortalError("import not found")
    return imp


def latest_import(db: Session, company_id, return_type, return_period) -> GstReturnImport | None:
    return db.execute(select(GstReturnImport).where(
        GstReturnImport.company_id == company_id, GstReturnImport.return_type == return_type,
        GstReturnImport.return_period == return_period,
        GstReturnImport.status == GstImportStatus.IMPORTED.value)
        .order_by(GstReturnImport.imported_at.desc())).scalars().first()
