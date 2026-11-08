"""E-way bill adapter: offline simulator + a GSP adapter.

The GSP adapter reuses the e-invoice GSP connection (``einvoice.adapter.GspAdapter``)
— same base URL, credentials and subscription key — and only swaps the endpoint
paths, because an e-way bill goes to the same GST Suvidha Provider.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.services.einvoice.adapter import EInvoiceError, GspAdapter

EWBError = EInvoiceError


@dataclass
class EWBResult:
    ewb_no: str
    ewb_date: str
    valid_until: str
    raw: dict = field(default_factory=dict)


class EWayBillAdapter(Protocol):
    name: str
    def generate_ewb(self, payload: dict) -> EWBResult: ...
    def cancel_ewb(self, ewb_no: str, *, reason_code: str, remarks: str) -> dict: ...


class EWayBillSimulator:
    """Non-production adapter. Deterministic 12-digit EWB number from the payload,
    with a validity window derived from the declared transport distance."""

    name = "simulator"

    def generate_ewb(self, payload: dict) -> EWBResult:
        basis = f"{payload.get('fromGstin')}|{payload.get('docNo')}|{payload.get('docDate')}"
        digest = hashlib.sha256(basis.encode()).hexdigest()
        ewb_no = str(int(digest[:12], 16))[:12].rjust(12, "0")
        now = datetime.now(timezone.utc)
        # EWB validity: 1 day per 200 km (NIC rule of thumb), minimum 1 day.
        km = int(payload.get("transDistance") or 0)
        days = max(1, -(-km // 200))
        valid = now + timedelta(days=days)
        return EWBResult(
            ewb_no=ewb_no, ewb_date=now.strftime("%Y-%m-%d %H:%M:%S"),
            valid_until=valid.strftime("%Y-%m-%d %H:%M:%S"),
            raw={"simulator": True, "ewayBillNo": ewb_no, "validDays": days})

    def cancel_ewb(self, ewb_no: str, *, reason_code: str, remarks: str) -> dict:
        return {"ewayBillNo": ewb_no, "status": "CANCELLED", "simulator": True,
                "cancelDate": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}


class EWayBillGspAdapter(GspAdapter):
    """Live adapter: authenticate like the e-invoice GSP, then post to the EWB
    endpoints. Response field names follow the NIC EWB API; adjust here if your
    GSP differs (the single integration seam, as for e-invoice)."""

    name = "gsp"

    def generate_ewb(self, payload: dict) -> EWBResult:
        try:
            with self._client() as client:
                token = self._auth_token(client)
                resp = client.post(self.s.ewb_path, json=payload,
                                   headers=self._auth_headers(token))
                resp.raise_for_status()
                body = resp.json()
        except EInvoiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EWBError(f"GSP e-way bill request failed: {exc}") from exc

        data = body.get("data", body)
        ewb_no = data.get("ewayBillNo") or data.get("EwbNo") or data.get("ewbNo")
        if not ewb_no:
            raise EWBError(f"EWB portal rejected the request: {body}")
        return EWBResult(
            ewb_no=str(ewb_no),
            ewb_date=str(data.get("ewayBillDate") or data.get("EwbDt") or ""),
            valid_until=str(data.get("validUpto") or data.get("ValidUpto") or ""),
            raw=body)

    def cancel_ewb(self, ewb_no: str, *, reason_code: str, remarks: str) -> dict:
        try:
            with self._client() as client:
                token = self._auth_token(client)
                resp = client.post(self.s.ewb_cancel_path, headers=self._auth_headers(token),
                                   json={"ewbNo": int(ewb_no), "cancelRsnCode": reason_code,
                                         "cancelRmrk": remarks})
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise EWBError(f"GSP e-way bill cancel failed: {exc}") from exc


def get_adapter(settings) -> EWayBillAdapter:
    if (settings.einvoice_gsp or "simulator").lower() == "gsp":
        return EWayBillGspAdapter(settings)
    return EWayBillSimulator()
