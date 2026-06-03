"""Environment-backed settings loaded once at process start."""
from __future__ import annotations
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(default="postgresql://localhost/mondaybrief")

    socrata_app_token: str = Field(default="")
    geocodio_api_key: str = Field(default="")
    mapbox_api_key: str = Field(default="")
    twilio_account_sid: str = Field(default="")
    twilio_auth_token: str = Field(default="")
    anthropic_api_key: str = Field(default="")

    # Resend transactional email (https://resend.com). One verified sending
    # domain; resend_webhook_secret is the Svix signing secret (whsec_...) from
    # the Resend dashboard webhook config.
    resend_api_key: str = Field(default="")
    resend_from_email: str = Field(default="brief@mondaybrief.app")
    resend_webhook_secret: str = Field(default="")

    embedding_model: str = Field(default="BAAI/bge-small-en-v1.5")
    h3_resolution: int = Field(default=9)
    service_area_isochrone_minutes: int = Field(default=15)
    top_leads_per_brief: int = Field(default=5)

    # v2 signal-fusion layer. When enabled, leads are scored on a calibrated,
    # corroborated, decay-aware fused confidence and suppressed below the floor;
    # when disabled, scoring falls back byte-identically to the v1 fixed
    # per-class strength. Floor override lets a thin-week client lower the bar.
    signal_layer_enabled: bool = Field(default=True)
    confidence_floor: float = Field(default=0.40)

    # Public base URL of the HTTP surface (mondaybrief.app). Used to build
    # absolute unsubscribe + Checkout return links inside emails/routes.
    app_base_url: str = Field(default="http://localhost:8000")

    # CAN-SPAM requires a real physical postal address + honest sender name in
    # every commercial email footer. Override both before the first live send.
    company_name: str = Field(default="MondayBrief")
    company_postal_address: str = Field(default="<set COMPANY_POSTAL_ADDRESS for CAN-SPAM>")


@lru_cache
def get_settings() -> Settings:
    return Settings()
