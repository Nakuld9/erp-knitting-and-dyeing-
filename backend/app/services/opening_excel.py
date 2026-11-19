"""Opening-stock Excel templates + parser (Stage 6F-P2).

Builds the downloadable yarn / trading-fabric opening-stock templates and parses
an uploaded workbook back into raw rows. openpyxl is already a project
dependency. The parser is deliberately strict:

* Row 1, cell A1 carries a metadata marker ``OPENING_TEMPLATE|VERSION=..|TYPE=..``
  which pins the template version and category.
* Row 2 is the header row; its column set must match the expected columns
  exactly (no missing and no unexpected headers).
* Data starts at row 3. Fully empty rows are skipped.

Parsing only reads cells into a dict; all business validation (masters, company /
factory ownership, quantities, dedup, legacy-lot policy) is done later by the
opening-stock service against the live database.
"""
from __future__ import annotations

import io
from datetime import date, datetime

from openpyxl import Workbook, load_workbook

from app.models.opening_stock import OPENING_TEMPLATE_VERSION, OpeningLineType

_MARKER_PREFIX = "OPENING_TEMPLATE"

YARN_COLUMNS: list[str] = [
    "yarn_code", "internal_lot_no", "supplier_code", "supplier_lot_no",
    "warehouse_code", "factory_code", "quantity", "uom_code", "weight_kg",
    "unit_cost", "total_value", "effective_date", "source_document",
    "source_reference", "legacy_unknown",
]

TRADING_COLUMNS: list[str] = [
    "trading_sku_code", "warehouse_code", "factory_code", "quantity", "uom_code",
    "total_weight_kg", "unit_cost", "total_value", "effective_date",
    "source_document", "source_reference",
]

MANUFACTURED_ROLL_COLUMNS: list[str] = [
    "erp_roll_no", "legacy_label", "roll_kind", "origin_stream",
    "fabric_sku_or_structure", "gsm", "width_cm", "colour", "shade_code",
    "net_weight_kg", "uom_code", "warehouse_code", "factory_code", "quality_status",
    "dye_lot", "job_ref", "processing_ref", "unit_cost", "total_value",
    "valuation_source", "effective_date", "source_document", "source_reference",
    "physical_verification_status", "disputed_unknown", "notes",
]

_COLUMNS = {
    OpeningLineType.YARN.value: YARN_COLUMNS,
    OpeningLineType.TRADING_FABRIC.value: TRADING_COLUMNS,
    OpeningLineType.MANUFACTURED_ROLLS.value: MANUFACTURED_ROLL_COLUMNS,
}


class OpeningTemplateError(ValueError):
    """The uploaded workbook is not a valid opening-stock template."""


def _marker(line_type: str) -> str:
    return f"{_MARKER_PREFIX}|VERSION={OPENING_TEMPLATE_VERSION}|TYPE={line_type}"


def _build(line_type: str, title: str) -> bytes:
    cols = _COLUMNS[line_type]
    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws["A1"] = _marker(line_type)
    for idx, col in enumerate(cols, start=1):
        ws.cell(row=2, column=idx, value=col)
    # Widen columns a little for readability.
    for idx in range(1, len(cols) + 1):
        ws.column_dimensions[ws.cell(row=2, column=idx).column_letter].width = 20
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_yarn_template() -> bytes:
    return _build(OpeningLineType.YARN.value, "Yarn Opening Stock")


def build_trading_template() -> bytes:
    return _build(OpeningLineType.TRADING_FABRIC.value, "Trading Fabric Opening Stock")


def build_manufactured_rolls_template() -> bytes:
    return _build(OpeningLineType.MANUFACTURED_ROLLS.value, "Manufactured Roll Opening")


def template_for(line_type: str) -> bytes:
    if line_type == OpeningLineType.YARN.value:
        return build_yarn_template()
    if line_type == OpeningLineType.TRADING_FABRIC.value:
        return build_trading_template()
    if line_type == OpeningLineType.MANUFACTURED_ROLLS.value:
        return build_manufactured_rolls_template()
    raise OpeningTemplateError(f"No opening template for line type '{line_type}'")


def _norm(value) -> str | None:
    """Normalise a cell value to a trimmed string (or None when blank). Dates are
    returned ISO; numbers keep full precision via ``repr`` of the Decimal-free
    float only when needed — but we return the raw string form here and let the
    service coerce types."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # Avoid 1.0 -> "1.0" noise for whole numbers while keeping precision.
        return repr(value).rstrip("0").rstrip(".") if "." in repr(value) else repr(value)
    text = str(value).strip()
    return text or None


def parse_workbook(content: bytes, *, expected_type: str) -> tuple[dict, list[dict]]:
    """Parse an uploaded opening-stock workbook.

    Returns ``(meta, rows)`` where ``meta`` holds the detected template version
    and type, and ``rows`` is a list of ``{column: str|None, "_row": int}`` for
    each non-empty data row (row numbers are the worksheet's own 1-based index).
    Raises :class:`OpeningTemplateError` on a bad marker, version mismatch, wrong
    type or header-set mismatch.
    """
    expected_cols = _COLUMNS.get(expected_type)
    if expected_cols is None:
        raise OpeningTemplateError(f"Unknown opening template type '{expected_type}'")
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - any openpyxl failure means a bad file
        raise OpeningTemplateError(f"File is not a readable .xlsx workbook: {exc}")
    ws = wb.active

    marker = _norm(ws.cell(row=1, column=1).value)
    if not marker or not marker.startswith(_MARKER_PREFIX):
        raise OpeningTemplateError(
            "Missing opening-template marker in cell A1; download a fresh template")
    parts = dict(p.split("=", 1) for p in marker.split("|")[1:] if "=" in p)
    version = parts.get("VERSION")
    detected_type = parts.get("TYPE")
    if version != OPENING_TEMPLATE_VERSION:
        raise OpeningTemplateError(
            f"Template version '{version}' is not supported (expected {OPENING_TEMPLATE_VERSION}); "
            "download the current template")
    if detected_type != expected_type:
        raise OpeningTemplateError(
            f"This is a '{detected_type}' template but a '{expected_type}' import was requested")

    header = [_norm(c.value) for c in ws[2]]
    header = [h for h in header if h is not None]
    if set(header) != set(expected_cols):
        missing = sorted(set(expected_cols) - set(header))
        unexpected = sorted(set(header) - set(expected_cols))
        raise OpeningTemplateError(
            f"Header mismatch. missing={missing} unexpected={unexpected}")
    col_index = {name: i for i, name in enumerate(header)}

    rows: list[dict] = []
    for r_idx, row in enumerate(ws.iter_rows(min_row=3), start=3):
        values = {name: _norm(row[col_index[name]].value) if col_index[name] < len(row) else None
                  for name in expected_cols}
        if all(v is None for v in values.values()):
            continue  # skip fully empty rows
        values["_row"] = r_idx
        rows.append(values)
    wb.close()
    return {"template_version": version, "type": detected_type}, rows
