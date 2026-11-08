"""E-invoice adapter protocol, an offline simulator, and a generic GSP adapter.

The simulator issues deterministic IRNs with no network, so the whole pipeline
(payload → store IRN/QR → cancel) is testable offline. The GSP adapter calls a
real GST Suvidha Provider's REST wrapper (WeP, Tele-electrosoft, Pinnacle, ...):
authenticate with the GSTIN's API credentials, POST the canonical payload, read
back the IRN / Ack / signed QR. It is used only when ``einvoice_gsp == "gsp"``
and the credentials are configured — never in tests.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


class EInvoiceError(Exception):
    """An e-invoice operation failed (validation, auth, IRP rejection)."""


@dataclass
class EInvoiceResult:
    irn: str
    ack_no: str
    ack_date: str
    signed_qr: str
    signed_invoice: str = ""
    ewb_no: str | None = None
    raw: dict = field(default_factory=dict)


class EInvoiceAdapter(Protocol):
    name: str
    def generate_irn(self, payload: dict) -> EInvoiceResult: ...
    def cancel_irn(self, irn: str, *, reason_code: str, remarks: str) -> dict: ...


# ------------------------------- simulator ----------------------------------

class EInvoiceSimulator:
    """Non-production adapter. Deterministically derives an IRN from the payload
    so a replay returns the same IRN; mimics the IRP's Ack + signed-QR shape."""

    name = "simulator"

    def generate_irn(self, payload: dict) -> EInvoiceResult:
        doc = payload.get("DocDtls", {})
        seller = payload.get("SellerDtls", {}).get("Gstin", "")
        basis = f"{seller}|{doc.get('No')}|{doc.get('Dt')}"
        irn = hashlib.sha256(basis.encode()).hexdigest()  # 64 hex chars, like a real IRN
        now = datetime.now(timezone.utc)
        ack_no = str(int(now.timestamp()))[-10:]
        ack_date = now.strftime("%Y-%m-%d %H:%M:%S")
        qr = {"irn": irn, "ackNo": ack_no, "ackDt": ack_date,
              "sellerGstin": seller, "buyerGstin": payload.get("BuyerDtls", {}).get("Gstin"),
              "docNo": doc.get("No"), "totInvVal": payload.get("ValDtls", {}).get("TotInvVal")}
        signed_qr = "SIM." + hashlib.sha256(json.dumps(qr, sort_keys=True).encode()).hexdigest()
        return EInvoiceResult(irn=irn, ack_no=ack_no, ack_date=ack_date, signed_qr=signed_qr,
                              signed_invoice="", raw={"simulator": True, **qr})

    def cancel_irn(self, irn: str, *, reason_code: str, remarks: str) -> dict:
        return {"irn": irn, "status": "CANCELLED", "cancelDate": datetime.now(timezone.utc)
                .strftime("%Y-%m-%d %H:%M:%S"), "simulator": True}


# ------------------------------- GSP adapter --------------------------------

class GspAdapter:
    """Generic REST adapter for a GSP that wraps the NIC IRP. Endpoints and
    credentials come from configuration; nothing is hardcoded. The exact auth
    and field names vary by GSP, so :meth:`_auth_headers` and the response keys
    are the single place to adapt for WeP / Tele-electrosoft / Pinnacle."""

    name = "gsp"

    def __init__(self, settings) -> None:
        self.s = settings
        if not settings.einvoice_base_url:
            raise EInvoiceError("EINVOICE_BASE_URL is not configured")
        for field_name in ("einvoice_username", "einvoice_password", "einvoice_gstin"):
            if not getattr(settings, field_name):
                raise EInvoiceError(f"{field_name.upper()} is not configured")

    def _client(self):
        import httpx  # local import: only needed for the live path
        return httpx.Client(base_url=self.s.einvoice_base_url,
                            timeout=self.s.einvoice_timeout_seconds)

    def _auth_token(self, client) -> str:
        resp = client.post(self.s.einvoice_auth_path, json={
            "client_id": self.s.einvoice_client_id,
            "client_secret": self.s.einvoice_client_secret,
            "username": self.s.einvoice_username,
            "password": self.s.einvoice_password,
            "gstin": self.s.einvoice_gstin,
        })
        resp.raise_for_status()
        body = resp.json()
        token = body.get("token") or body.get("AuthToken") or body.get("access_token")
        if not token:
            raise EInvoiceError(f"GSP auth returned no token: {body}")
        return token

    def _auth_headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "gstin": self.s.einvoice_gstin}

    def generate_irn(self, payload: dict) -> EInvoiceResult:
        try:
            with self._client() as client:
                token = self._auth_token(client)
                resp = client.post(self.s.einvoice_irn_path, json=payload,
                                   headers=self._auth_headers(token))
                resp.raise_for_status()
                body = resp.json()
        except EInvoiceError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface a clean integration error
            raise EInvoiceError(f"GSP request failed: {exc}") from exc

        data = body.get("data", body)
        irn = data.get("Irn") or data.get("irn")
        if not irn:
            raise EInvoiceError(f"IRP rejected the invoice: {body}")
        return EInvoiceResult(
            irn=irn,
            ack_no=str(data.get("AckNo") or data.get("ackNo") or ""),
            ack_date=str(data.get("AckDt") or data.get("ackDt") or ""),
            signed_qr=data.get("SignedQRCode") or data.get("signedQRCode") or "",
            signed_invoice=data.get("SignedInvoice") or data.get("signedInvoice") or "",
            ewb_no=data.get("EwbNo") or data.get("ewbNo"),
            raw=body,
        )

    def cancel_irn(self, irn: str, *, reason_code: str, remarks: str) -> dict:
        try:
            with self._client() as client:
                token = self._auth_token(client)
                resp = client.post(self.s.einvoice_cancel_path, headers=self._auth_headers(token),
                                   json={"Irn": irn, "CnlRsn": reason_code, "CnlRem": remarks})
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise EInvoiceError(f"GSP cancel failed: {exc}") from exc


def get_adapter(settings) -> EInvoiceAdapter:
    """Select the adapter from configuration. Defaults to the simulator, so the
    feature is safe out of the box and only talks to a real IRP when asked."""
    if (settings.einvoice_gsp or "simulator").lower() == "gsp":
        return GspAdapter(settings)
    return EInvoiceSimulator()
