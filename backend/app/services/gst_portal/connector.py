"""Pluggable GSTN download connector (Stage 6F-P5 GST).

INVESTIGATION FINDING (official GSTN download route, verified 2026):
  There is NO public, taxpayer-facing REST API to download GSTR-2A / GSTR-2B
  directly. The taxpayer's own route is to download the JSON / Excel from the GST
  portal's Returns Dashboard (GSTR-2B is a static statement generated after the
  14th of the following month; GSTR-2A is dynamic). The only programmatic route is
  through a GSTN-approved **GSP (GST Suvidha Provider)** channel together with an
  **ASP**, after enabling *Manage API Access* on the portal and authenticating with
  an **OTP-based session**. GSTN does not expose its APIs to arbitrary software.

Therefore this module uses a connector abstraction:

* ``ManualImportConnector`` (DEFAULT) — no live connectivity. ``download`` raises
  ``ConnectorUnavailable`` telling the user to download the file from the portal and
  import it manually. This never pretends a download succeeded.
* ``GspConnector`` — the extension point for a real, GSTN-approved GSP/ASP
  integration. It is DISABLED unless explicitly configured, and even when a base
  URL is configured it refuses to fabricate data: without a tested, authorised
  integration + runtime OTP session it raises ``ConnectorUnavailable`` naming the
  GSP/ASP dependency. The GST portal password is never stored (``config.py``).

Nothing here scrapes portal pages, bypasses OTP/CAPTCHA, stores portal passwords
or invents undocumented APIs.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime

from app.core.config import settings


class ConnectorUnavailable(Exception):
    """A live portal download could not be performed (no integration / OTP session).

    This is an honest 'not available' signal — callers fall back to manual import.
    It is NEVER raised in a way that masquerades as a successful download.
    """


@dataclass
class PortalDownloadResult:
    content: bytes
    source: str                      # GstImportSource value
    file_name: str
    generated_at: datetime | None = None


class GstnConnector(abc.ABC):
    """A source that can fetch a GST return file for (gstin, period, return_type)."""

    is_live: bool = False
    name: str = "base"

    @abc.abstractmethod
    def download(self, *, gstin: str, return_period: str, return_type: str) -> PortalDownloadResult:
        ...


class ManualImportConnector(GstnConnector):
    """No live connectivity: the compliant route without a GSP. Always refuses to
    'download', directing the user to the portal + manual import."""

    is_live = False
    name = "manual"

    def download(self, *, gstin: str, return_period: str, return_type: str) -> PortalDownloadResult:
        raise ConnectorUnavailable(
            "No live GSTN connectivity is configured (manual mode). Download the "
            f"{return_type} JSON or Excel for GSTIN {gstin}, period {return_period} from the "
            "GST portal (Returns Dashboard → Download), then import the file here. "
            "GSTR-2B is available after the 14th of the following month.")


class GspConnector(GstnConnector):
    """Extension point for a GSTN-approved GSP/ASP integration.

    Disabled unless a GSP base URL and credentials are configured AND a tested,
    authorised integration with a runtime OTP session is available. This build ships
    no live GSP integration, so it refuses rather than fabricate a download. Wiring a
    real GSP belongs here (auth via OTP session token requested at call time; the
    portal password is never stored)."""

    is_live = True
    name = "gsp"

    def __init__(self, *, base_url: str, client_id: str, client_secret: str,
                 subscription_key: str = ""):
        self.base_url = base_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.subscription_key = subscription_key

    def download(self, *, gstin: str, return_period: str, return_type: str) -> PortalDownloadResult:
        # A real implementation would: (1) ensure a valid OTP-authorised session for
        # the GSTIN, (2) call the GSP's returns-download endpoint, (3) return the raw
        # JSON bytes. We must not fabricate a response or invent an endpoint, so until
        # an authorised, tested integration is wired we refuse honestly.
        raise ConnectorUnavailable(
            "Live GSP download is not available in this build. Direct GSTR-2A/2B "
            "download requires a GSTN-approved GSP/ASP integration with API access "
            "enabled on the portal and a runtime OTP-authorised session. Configure and "
            "test that integration, or use manual JSON/Excel import. (GSP base: "
            f"{self.base_url or 'not set'}.)")


def get_connector() -> GstnConnector:
    """Return the configured connector. Defaults to manual (no live connectivity).

    Never returns a connector that fabricates data. Selecting ``gsp`` requires the
    base URL + credentials; a misconfigured ``gsp`` still refuses honestly."""
    mode = (getattr(settings, "gst_returns_mode", "manual") or "manual").strip().lower()
    if mode == "gsp":
        return GspConnector(
            base_url=getattr(settings, "gst_returns_gsp_base_url", "") or "",
            client_id=getattr(settings, "gst_returns_gsp_client_id", "") or "",
            client_secret=getattr(settings, "gst_returns_gsp_client_secret", "") or "",
            subscription_key=getattr(settings, "gst_returns_gsp_subscription_key", "") or "")
    return ManualImportConnector()


def connector_status() -> dict:
    """Report the live-connectivity status for the UI (honest; no fabrication)."""
    c = get_connector()
    return {
        "mode": c.name,
        "live": bool(c.is_live),
        "message": (
            "Live GSP integration selected but not yet wired/tested in this build; "
            "use manual JSON/Excel import." if c.is_live else
            "Manual mode: download JSON/Excel from the GST portal and import it here. "
            "Direct programmatic download requires a GSTN-approved GSP/ASP channel."),
        "gsp_dependency": (
            "Direct GSTR-2A/2B download requires a GSTN-approved GSP (GST Suvidha "
            "Provider) + ASP with Manage API Access enabled on the portal and OTP-based "
            "authentication. There is no public taxpayer API."),
    }
