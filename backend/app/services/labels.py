"""Printable fabric-roll label (PDF with a QR code).

The QR encodes the roll number so a scan at any station resolves the roll; the
human-readable block carries the key attributes and lineage.
"""
from __future__ import annotations

import uuid

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from sqlalchemy.orm import Session

from app.models.core import Company
from app.models.manufacturing import DyeBatch, FabricRoll
from app.models.masters import FabricType


def _fmt(v, suffix=""):
    if v is None:
        return "-"
    from decimal import Decimal, InvalidOperation
    try:
        d = Decimal(str(v)).normalize()
        s = format(d, "f")  # strip trailing zeros, no exponent
    except (InvalidOperation, ValueError):
        s = str(v)
    return f"{s}{suffix}"


def roll_label_pdf(db: Session, company_id: uuid.UUID, roll_id: uuid.UUID) -> bytes:
    roll = db.get(FabricRoll, roll_id)
    if roll is None or roll.company_id != company_id:
        raise ValueError("Roll not found")
    company = db.get(Company, company_id)
    fabric = db.get(FabricType, roll.fabric_type_id) if roll.fabric_type_id else None
    batch = db.get(DyeBatch, roll.dye_batch_id) if roll.dye_batch_id else None
    from app.models.masters import Machine, YarnLot, YarnMaster
    machine = db.get(Machine, roll.machine_id) if roll.machine_id else None
    yarn = db.get(YarnMaster, roll.yarn_id) if roll.yarn_id else None
    ylot = db.get(YarnLot, roll.yarn_lot_id) if roll.yarn_lot_id else None
    code = roll.barcode or roll.roll_no

    import io
    buf = io.BytesIO()
    W, H = 100 * mm, 72 * mm
    c = canvas.Canvas(buf, pagesize=(W, H))

    # border
    c.setStrokeColor(colors.HexColor("#1E293B"))
    c.setLineWidth(1)
    c.rect(2 * mm, 2 * mm, W - 4 * mm, H - 4 * mm)

    # header
    c.setFillColor(colors.HexColor("#1E293B"))
    c.rect(2 * mm, H - 12 * mm, W - 4 * mm, 10 * mm, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(5 * mm, H - 9 * mm, (company.name if company else "Textile ERP")[:36])

    # QR
    qr = QrCodeWidget(code)
    b = qr.getBounds()
    size = 26 * mm
    d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
    d.add(qr)
    renderPDF.draw(d, c, W - 30 * mm, 6 * mm)

    # text block
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(5 * mm, H - 20 * mm, roll.roll_no)
    c.setFont("Helvetica", 8.5)
    lines = [
        f"Kind: {roll.kind}      Quality: {roll.quality_status}",
        f"Fabric: {fabric.name if fabric else '-'}",
        f"Machine: {machine.code if machine else '-'}   Yarn: {yarn.name if yarn else '-'}",
        f"Yarn lot: {ylot.internal_lot_no if ylot else '-'}",
        f"Weight: {_fmt(roll.weight_kg,' kg')}   Length: {_fmt(roll.length_m,' m')}",
        f"GSM: {_fmt(roll.gsm)}   Width: {_fmt(roll.width_cm,' cm')}",
        f"Dye batch: {batch.batch_code if batch else '-'}",
    ]
    y = H - 26 * mm
    for ln in lines:
        c.drawString(5 * mm, y, ln)
        y -= 5 * mm

    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor("#64748B"))
    c.drawString(5 * mm, 4 * mm, f"Scan code: {code}")

    c.showPage()
    c.save()
    return buf.getvalue()
