"""Pydantic models — the typed shapes flowing through the pipeline."""
from __future__ import annotations
from datetime import date, datetime
from typing import Any, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator


class Customer(BaseModel):
    """A row from the cleaner's uploaded customer book."""
    id: Optional[int] = None
    client_id: str
    name: str
    address: str
    city: str
    state: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    h3_cell: Optional[str] = None
    category: Optional[str] = None
    sqft: Optional[int] = None
    monthly_rev: Optional[float] = None
    status: str = "active"


class RawLead(BaseModel):
    """A new permit/license row before any enrichment."""
    source: str  # 'r5kz-chrr' | 'ydr8-5enu' | '4ijn-s7e5'
    source_id: str
    name: str
    dba: Optional[str] = None
    address: str
    city: str = "Chicago"
    state: str = "IL"
    zip: Optional[str] = None
    date_issued: Optional[date] = None
    raw_json: dict


class EnrichedLead(RawLead):
    """RawLead plus geocoding + territory + nearest customer."""
    lat: Optional[float] = None
    lng: Optional[float] = None
    h3_cell: Optional[str] = None
    nearest_customer_id: Optional[int] = None
    nearest_customer_distance_mi: Optional[float] = None
    drive_minutes: Optional[float] = None
    owner_name: Optional[str] = None
    owner_phone: Optional[str] = None
    owner_phone_valid: Optional[bool] = None


class ScoredLead(BaseModel):
    """The Instructor-typed Claude output. Forced shape on every LLM call."""
    name: str = Field(description="Business name as it should appear on the brief")
    address: str
    category: str = Field(description="One of: dental clinic, vet clinic, medical office, cafe, restaurant, gym, fitness studio, retail, office, other")
    score: int = Field(ge=0, le=100, description="0-100 lead fit score")
    margin_est_monthly: float = Field(description="Estimated monthly margin to the cleaner if won, in USD")
    margin_uplift_pct: float = Field(description="Percentage points above the cleaner's portfolio average margin")
    why: str = Field(description="One sentence explaining why THIS Monday — cite the trigger signal (license issued / permit filed / liquor application)")
    opener: str = Field(description="A 3-sentence cold-call opener written in the cleaner's voice, ready to read off the page")
    signal_class: Optional[str] = Field(
        default=None,
        description="One of: new_opening | expansion | churn_intent | unknown. Set by signals.classify_signal before scoring; not produced by Claude.",
    )
    tier: Optional[str] = Field(
        default=None,
        description="A | B | C | drop — bucketed from score by score.engine.to_tier.",
    )
    # Component sub-scores (0-10) from the deterministic engine. Carried on the
    # lead so the brief and any audit can show WHY a lead scored what it did.
    margin_score: Optional[float] = None
    route_score: Optional[float] = None
    category_score: Optional[float] = None
    timing_score: Optional[float] = None
    signal_score: Optional[float] = None

    # v2 signal-fusion outputs. signal_confidence is the fused 0-1 probability
    # that drives the signal component + the suppression floor; corroboration_count
    # is how many independent source-families agreed; signal_evidence is the
    # plain-words list shown on the brief (e.g. ["new business license issued",
    # "new-construction permit"]) — never the raw number (product decision #4).
    signal_confidence: Optional[float] = None
    corroboration_count: Optional[int] = None
    signal_evidence: list[str] = Field(default_factory=list)

    @field_validator("score", mode="before")
    @classmethod
    def round_score(cls, v: int) -> int:
        # mode="before" so we clamp into range *before* the ge/le constraints
        # run — otherwise an out-of-range score raises instead of clamping.
        return max(0, min(100, int(v)))


class BriefBundle(BaseModel):
    """The final payload that becomes the Monday PDF."""
    client_id: str
    client_name: str
    metro: str
    week_of: date
    generated_at: datetime
    leads: list[ScoredLead]
    customer_count: int
    permits_pulled: int
    leads_inside_area: int
    leads_after_dedup: int


# ===== v1 e2e models (2026-05-31) =====
# Multi-tenant registry, Stripe subscriptions, feedback, delivery telemetry.
# Mirror the SQL added in `schema.sql` under "v1 e2e migration".


class Client(BaseModel):
    """A paying cleaner-tenant. Row in the `clients` table."""
    id: Optional[UUID] = None
    slug: str = Field(description="Stable URL-safe identifier, e.g. 'spotless', 'ek'")
    name: str
    contact_email: str
    postmark_stream: str = "outbound"
    metros: list[str] = Field(default_factory=lambda: ["chicago"])
    stripe_customer_id: Optional[str] = None
    active: bool = True
    created_at: Optional[datetime] = None


class Subscription(BaseModel):
    """Stripe subscription mirrored locally for billing state checks."""
    id: Optional[UUID] = None
    client_id: UUID
    stripe_subscription_id: str
    status: str = Field(
        description="Stripe status: active, past_due, canceled, incomplete, etc."
    )
    current_period_end: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class LeadFeedback(BaseModel):
    """Thumbs-up/down on a brief lead, captured via PDF feedback link."""
    id: Optional[UUID] = None
    scored_lead_id: int
    client_id: UUID
    thumbs: Literal["up", "down"]
    note: Optional[str] = None
    created_at: Optional[datetime] = None


class EmailEvent(BaseModel):
    """Postmark delivery telemetry: delivered / bounced / opened / etc."""
    id: Optional[UUID] = None
    scored_lead_id: Optional[int] = None
    pipeline_run_id: Optional[int] = None
    client_id: UUID
    event_type: str = Field(
        description="delivered | bounced | opened | spam_complaint | dunning_sent | ..."
    )
    postmark_message_id: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None
