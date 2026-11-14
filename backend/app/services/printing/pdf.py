"""A4 PDF renderer for :class:`PrintDocument`.

One professional layout for every document: company letterhead (logo + legal name
+ address + GSTIN + contact), a title band with the document number and date,
party blocks, a line-item table, totals/tax, references, signature areas, a footer
with page numbers, and diagonal watermarks for PREVIEW / CANCELLED / REPRINT.

Rendering is pure: it reads the spec and returns bytes. It never touches the
database or any business transaction.
"""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

from app.services.printing.document import PrintDocument

NAVY = colors.HexColor("#1E293B")
BLUE = colors.HexColor("#1D4ED8")
GREY = colors.HexColor("#64748B")
LIGHT = colors.HexColor("#E2E8F0")


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("Lh", fontSize=14, leading=16, textColor=NAVY, fontName="Helvetica-Bold"))
    ss.add(ParagraphStyle("LhSub", fontSize=8, leading=10, textColor=GREY))
    ss.add(ParagraphStyle("DocTitle", fontSize=15, leading=17, textColor=NAVY,
                          fontName="Helvetica-Bold", alignment=TA_RIGHT))
    ss.add(ParagraphStyle("DocKind", fontSize=8.5, leading=11, textColor=GREY, alignment=TA_RIGHT))
    ss.add(ParagraphStyle("NumR", fontSize=9, leading=12, textColor=NAVY, alignment=TA_RIGHT))
    ss.add(ParagraphStyle("PartyLbl", fontSize=7.5, leading=9, textColor=GREY, fontName="Helvetica-Bold"))
    ss.add(ParagraphStyle("PartyName", fontSize=9.5, leading=12, textColor=NAVY, fontName="Helvetica-Bold"))
    ss.add(ParagraphStyle("PartyLine", fontSize=8, leading=10, textColor=colors.black))
    ss.add(ParagraphStyle("Cell", fontSize=8, leading=10))
    ss.add(ParagraphStyle("CellR", fontSize=8, leading=10, alignment=TA_RIGHT))
    ss.add(ParagraphStyle("CellH", fontSize=8, leading=10, textColor=colors.white, fontName="Helvetica-Bold"))
    ss.add(ParagraphStyle("Sig", fontSize=8, leading=11, textColor=NAVY, alignment=TA_CENTER))
    ss.add(ParagraphStyle("Foot", fontSize=7, leading=9, textColor=GREY, alignment=TA_CENTER))
    ss.add(ParagraphStyle("Note", fontSize=7.5, leading=10, textColor=GREY))
    return ss


def _logo_flowable(lh, max_w=34 * mm, max_h=18 * mm):
    if not lh.logo_bytes:
        return None
    try:
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import Image
        reader = ImageReader(io.BytesIO(lh.logo_bytes))
        iw, ih = reader.getSize()
        if iw <= 0 or ih <= 0:
            return None
        scale = min(max_w / iw, max_h / ih)
        return Image(io.BytesIO(lh.logo_bytes), width=iw * scale, height=ih * scale)
    except Exception:
        return None   # a bad image never breaks the document; text identity is used


def _letterhead_table(doc: PrintDocument, ss):
    lh = doc.letterhead
    contact_bits = []
    if lh.address: contact_bits.append(lh.address)
    line2 = []
    if lh.gstin: line2.append(f"GSTIN: {lh.gstin}")
    if lh.cin: line2.append(f"CIN: {lh.cin}")
    if line2: contact_bits.append(" &nbsp;|&nbsp; ".join(line2))
    line3 = []
    if lh.phone: line3.append(f"Ph: {lh.phone}")
    if lh.email: line3.append(lh.email)
    if lh.website: line3.append(lh.website)
    if line3: contact_bits.append(" &nbsp;|&nbsp; ".join(line3))

    left_cell = []
    logo = _logo_flowable(lh)
    name_block = [Paragraph(lh.legal_name or "—", ss["Lh"])]
    for b in contact_bits:
        name_block.append(Paragraph(b, ss["LhSub"]))
    if logo is not None:
        left = Table([[logo, name_block]], colWidths=[36 * mm, None])
        left.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                  ("RIGHTPADDING", (0, 0), (0, 0), 6)]))
        left_cell = [left]
    else:
        left_cell = name_block

    right_cell = [Paragraph(doc.title, ss["DocTitle"])]
    if doc.document_kind:
        right_cell.append(Paragraph(doc.document_kind, ss["DocKind"]))
    right_cell.append(Spacer(1, 4))
    right_cell.append(Paragraph(f"No: <b>{doc.number}</b>", ss["NumR"]))
    right_cell.append(Paragraph(f"Date: {doc.date}", ss["NumR"]))

    t = Table([[left_cell, right_cell]], colWidths=[112 * mm, 66 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -1), 1.2, NAVY),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _parties_table(doc: PrintDocument, ss):
    if not doc.parties:
        return None
    cells = []
    for p in doc.parties:
        block = [Paragraph(p.label.upper(), ss["PartyLbl"]), Paragraph(p.name or "—", ss["PartyName"])]
        for ln in p.lines:
            block.append(Paragraph(ln, ss["PartyLine"]))
        cells.append(block)
    # up to 3 across
    cols = len(cells)
    width = 178 * mm / cols
    t = Table([cells], colWidths=[width] * cols)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.5, LIGHT),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, LIGHT),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _items_table(doc: PrintDocument, ss):
    if not doc.columns:
        return None
    header = [Paragraph(c.label, ss["CellH"]) for c in doc.columns]
    data = [header]
    for r in doc.rows:
        line = []
        for c in doc.columns:
            style = ss["CellR"] if c.numeric else ss["Cell"]
            line.append(Paragraph("" if r.get(c.key) is None else str(r.get(c.key)), style))
        data.append(line)
    weights = [c.width or (1.0 if not c.numeric else 0.7) for c in doc.columns]
    total = sum(weights) or 1
    widths = [178 * mm * (w / total) for w in weights]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, LIGHT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _totals_table(doc: PrintDocument, ss):
    rows = list(doc.tax_lines) + list(doc.totals)
    if not rows:
        return None
    data = [[Paragraph(lbl, ss["CellR"]), Paragraph(val, ss["CellR"])] for lbl, val in rows]
    t = Table(data, colWidths=[44 * mm, 34 * mm], hAlign="RIGHT")
    style = [("LINEABOVE", (0, 0), (-1, 0), 0.5, LIGHT),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if doc.totals:
        # bold the final total row
        style += [("LINEABOVE", (0, len(data) - 1), (-1, len(data) - 1), 1, NAVY),
                  ("FONTNAME", (0, len(data) - 1), (-1, len(data) - 1), "Helvetica-Bold")]
    t.setStyle(TableStyle(style))
    return t


def _signatures_table(doc: PrintDocument, ss):
    if not doc.signatures:
        return None
    cells = []
    for s in doc.signatures:
        block = [Spacer(1, 16 * mm)]
        if s.for_line:
            block.append(Paragraph(s.for_line, ss["Sig"]))
        block.append(Paragraph("_______________________", ss["Sig"]))
        block.append(Paragraph(s.label, ss["Sig"]))
        cells.append(block)
    width = 178 * mm / len(cells)
    t = Table([cells], colWidths=[width] * len(cells))
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                           ("TOPPADDING", (0, 0), (-1, -1), 6)]))
    return t


def _watermark(canvas, doc_doc: PrintDocument):
    text = None
    if doc_doc.is_cancelled:
        text, col = "CANCELLED", colors.Color(0.86, 0.15, 0.15, alpha=0.18)
    elif doc_doc.is_preview:
        text, col = "PREVIEW", colors.Color(0.4, 0.45, 0.55, alpha=0.16)
    if not text:
        return
    canvas.saveState()
    canvas.translate(A4[0] / 2, A4[1] / 2)
    canvas.rotate(45)
    canvas.setFont("Helvetica-Bold", 90)
    canvas.setFillColor(col)
    canvas.drawCentredString(0, 0, text)
    canvas.restoreState()


def render_pdf(doc: PrintDocument) -> bytes:
    ss = _styles()
    buf = io.BytesIO()

    def on_page(canvas, _doc):
        _watermark(canvas, doc)
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GREY)
        foot = doc.footer_note or "This is a computer-generated document."
        marks = []
        if doc.reprint_count and not doc.is_preview:
            marks.append(f"REPRINT #{doc.reprint_count}")
        if doc.is_preview:
            marks.append("PREVIEW — not issued")
        tag = ("   |   ".join(marks) + "   |   ") if marks else ""
        canvas.drawCentredString(A4[0] / 2, 12 * mm, f"{tag}{foot}")
        canvas.drawRightString(A4[0] - 14 * mm, 12 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    tmpl = BaseDocTemplate(buf, pagesize=A4, topMargin=14 * mm, bottomMargin=18 * mm,
                           leftMargin=16 * mm, rightMargin=16 * mm, title=doc.title)
    frame = Frame(tmpl.leftMargin, tmpl.bottomMargin,
                  tmpl.width, tmpl.height, id="body")
    tmpl.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=on_page)])

    story = [_letterhead_table(doc, ss), Spacer(1, 8)]
    parties = _parties_table(doc, ss)
    if parties is not None:
        story += [parties, Spacer(1, 8)]
    if doc.references:
        ref = " &nbsp;&nbsp; ".join(f"<b>{l}:</b> {v}" for l, v in doc.references)
        story += [Paragraph(ref, ss["Note"]), Spacer(1, 6)]
    items = _items_table(doc, ss)
    if items is not None:
        story += [items, Spacer(1, 6)]
    totals = _totals_table(doc, ss)
    if totals is not None:
        story += [totals, Spacer(1, 8)]
    for n in doc.notes:
        story.append(Paragraph(n, ss["Note"]))
    sigs = _signatures_table(doc, ss)
    if sigs is not None:
        story += [Spacer(1, 10), sigs]

    tmpl.build(story)
    return buf.getvalue()
