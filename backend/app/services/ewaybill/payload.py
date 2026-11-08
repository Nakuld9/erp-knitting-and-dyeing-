"""Build the NIC e-way bill request payload from a sales invoice + transport.

Reuses the e-invoice payload builder for the document, party and tax figures
(so the two stay consistent), then remaps to EWB field names and adds the
transport block. When the invoice already has an IRN, it is carried on the
payload so the GSP/IRP can link the EWB to the e-invoice.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.commercial import SalesInvoice
from app.services.einvoice.payload import EInvoicePayloadError, build_irn_payload


@dataclass
class Transport:
    trans_mode: str = "1"            # 1 Road, 2 Rail, 3 Air, 4 Ship
    vehicle_no: str | None = None
    distance_km: int = 0
    transporter_id: str | None = None
    transporter_name: str | None = None
    trans_doc_no: str | None = None
    trans_doc_date: str | None = None   # dd/mm/yyyy
    sub_supply_type: str = "1"       # 1 Supply
    vehicle_type: str = "R"          # R Regular, O Over-Dimensional Cargo


class EWBPayloadError(EInvoicePayloadError):
    """The invoice/transport is missing data the EWB portal requires."""


def build_ewb_payload(db: Session, invoice: SalesInvoice, transport: Transport,
                      *, irn: str | None = None) -> dict:
    base = build_irn_payload(db, invoice)      # validates GSTIN/state, computes items & totals
    seller, buyer, val = base["SellerDtls"], base["BuyerDtls"], base["ValDtls"]

    if transport.trans_mode == "1" and not (transport.vehicle_no or transport.transporter_id):
        raise EWBPayloadError("Road transport needs a vehicle number or a transporter id")
    if int(transport.distance_km or 0) <= 0:
        raise EWBPayloadError("Transport distance (km) is required")

    items = [{
        "productName": it["PrdDesc"],
        "hsnCode": it["HsnCd"],
        "quantity": it["Qty"],
        "qtyUnit": it["Unit"],
        "taxableAmount": it["AssAmt"],
        "cgstRate": 0 if val["IgstVal"] else it["GstRt"] / 2,
        "sgstRate": 0 if val["IgstVal"] else it["GstRt"] / 2,
        "igstRate": it["GstRt"] if val["IgstVal"] else 0,
    } for it in base["ItemList"]]

    payload = {
        "supplyType": "O",                       # Outward
        "subSupplyType": transport.sub_supply_type,
        "docType": "INV",
        "docNo": base["DocDtls"]["No"],
        "docDate": base["DocDtls"]["Dt"],
        "fromGstin": seller["Gstin"],
        "fromTrdName": seller["LglNm"],
        "fromAddr1": seller["Addr1"],
        "fromPlace": seller["Loc"],
        "fromPincode": int(seller["Pin"]),
        "fromStateCode": int(seller["Stcd"]),
        "actFromStateCode": int(seller["Stcd"]),
        "toGstin": buyer["Gstin"],
        "toTrdName": buyer["LglNm"],
        "toAddr1": buyer["Addr1"],
        "toPlace": buyer["Loc"],
        "toPincode": int(buyer["Pin"]),
        "toStateCode": int(buyer["Stcd"]),
        "actToStateCode": int(buyer["Stcd"]),
        "totalValue": val["AssVal"],
        "cgstValue": val["CgstVal"],
        "sgstValue": val["SgstVal"],
        "igstValue": val["IgstVal"],
        "cessValue": 0,
        "totInvValue": val["TotInvVal"],
        "transMode": transport.trans_mode,
        "transDistance": str(int(transport.distance_km)),
        "transporterId": transport.transporter_id or "",
        "transporterName": transport.transporter_name or "",
        "transDocNo": transport.trans_doc_no or "",
        "transDocDate": transport.trans_doc_date or "",
        "vehicleNo": (transport.vehicle_no or "").replace(" ", "").upper(),
        "vehicleType": transport.vehicle_type,
        "itemList": items,
    }
    if irn:
        payload["Irn"] = irn                     # link to the e-invoice when present
    return payload
