"""Application configuration.

All settings are environment-driven. Nothing security- or tax-sensitive is
hardcoded: secret keys, database URLs and GST behaviour all come from the
environment so the same image runs in dev, test and production.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Application ---
    app_name: str = "Integrated Textile Manufacturing ERP"
    environment: str = "development"
    debug: bool = True

    # --- Observability ---
    log_level: str = "INFO"
    # Emit one JSON object per log line (for log shippers); plain text otherwise.
    log_json: bool = False

    # --- Database ---
    # Production: postgresql+psycopg2://user:pass@host:5432/erp
    # Tests default to in-memory SQLite (see tests/conftest.py).
    database_url: str = "postgresql+psycopg2://erp:erp@localhost:5432/erp"

    # --- Security ---
    secret_key: str = "CHANGE-ME-IN-PRODUCTION-use-a-32+-byte-random-value"
    access_token_expire_minutes: int = 60 * 8  # one working day
    jwt_algorithm: str = "HS256"

    # --- Locale / fiscal ---
    default_timezone: str = "Asia/Kolkata"
    default_currency: str = "INR"
    # Indian financial year starts in April.
    fiscal_year_start_month: int = 4

    # --- E-invoice (IRN via a GST Suvidha Provider) ---
    # Which adapter to use. "simulator" (default) issues deterministic test IRNs
    # offline; "gsp" calls a real GSP/IRP using the credentials below. Set these
    # from the environment / secrets — never commit real values.
    einvoice_gsp: str = "simulator"            # "simulator" | "gsp"
    einvoice_base_url: str = ""                # GSP REST base, e.g. https://api.gsp.example/einvoice
    einvoice_auth_path: str = "/auth"          # token endpoint (relative to base)
    einvoice_irn_path: str = "/invoice"        # IRN generate endpoint
    einvoice_cancel_path: str = "/invoice/cancel"
    einvoice_client_id: str = ""
    einvoice_client_secret: str = ""
    einvoice_username: str = ""                # e-invoice portal API user for the GSTIN
    einvoice_password: str = ""
    einvoice_gstin: str = ""                   # the registered supplier GSTIN
    # Many GSPs (Webtel, Pinnacle, MasterGST, TaxPro) require an API subscription
    # key on every call, sent in a GSP-specific header. Set both when yours does.
    einvoice_subscription_key: str = ""
    einvoice_subscription_header: str = "subscription-key"
    einvoice_timeout_seconds: int = 20
    # E-way bill shares the same GSP connection/credentials, different endpoints.
    ewb_path: str = "/ewaybill"
    ewb_cancel_path: str = "/ewaybill/cancel"
    # EWB is mandatory above this consignment value (₹50,000); below it is a warning.
    ewb_threshold: int = 50000

    # --- Rounding ---
    # Number of decimal places kept for monetary amounts and quantities.
    money_scale: int = 2
    quantity_scale: int = 4

    @model_validator(mode="after")
    def _validate_production_security(self):
        """Fail closed in production: no debug, no weak/placeholder secret, no
        default database credentials. Dev/test are unaffected."""
        if (self.environment or "").strip().lower() == "production":
            if self.debug:
                raise ValueError("DEBUG must be false in production")
            if len(self.secret_key.encode("utf-8")) < 32 or self.secret_key.startswith("CHANGE-ME"):
                raise ValueError(
                    "SECRET_KEY must be a strong, unique value of at least 32 bytes in production")
            url = (self.database_url or "").lower()
            if "erp:erp@" in url or "user:pass@" in url or "password@" in url:
                raise ValueError("Default or placeholder database credentials are not allowed in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
