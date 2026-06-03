"""LLM narrative — the *why* and the cold-call *opener*, nothing else.

In the rebuilt scoring system the number is produced deterministically by
:mod:`score.engine`. The LLM's only job is prose: a one-sentence ``why`` that
cites the trigger signal, and a three-sentence ``opener`` in the cleaner's
voice. It receives the finished score + components + nearest customer as
context so the tone matches the score (an honest opener for a thin lead, an
eager one for a strong lead) — but it never changes the score.

This is cheaper than the old approach (smaller output, runs only on the top-N
survivors) and auditable (the number isn't hidden inside a model call).

When ``ANTHROPIC_API_KEY`` is absent (offline / CI / dev) we return a
deterministic template narrative with zero cost, so the whole pipeline — and
the per-client e2e proof — runs without a key.

Repos:
  - instructor: https://github.com/567-labs/instructor (MIT)
  - anthropic-sdk-python: https://github.com/anthropics/anthropic-sdk-python (MIT)
  - langfuse-python: https://github.com/langfuse/langfuse-python (MIT)
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import anthropic
import instructor
from pydantic import BaseModel, Field

from ..config import get_settings
from ..models import Customer, EnrichedLead
from ..observability.langfuse_setup import wrap_claude_call

MODEL_ID = "claude-haiku-4-5-20251001"

# Haiku 4.5 pricing (USD per 1M tokens), 2026-05. Cache reads 10% of input;
# cache writes 125%.
HAIKU_INPUT_PER_M = 1.00
HAIKU_OUTPUT_PER_M = 5.00
HAIKU_CACHE_READ_PER_M = HAIKU_INPUT_PER_M * 0.10
HAIKU_CACHE_WRITE_PER_M = HAIKU_INPUT_PER_M * 1.25


class LeadNarrative(BaseModel):
    """The only two fields the LLM produces now."""

    why: str = Field(
        description="One sentence on why THIS Monday — cite the trigger (license issued / "
        "permit filed / liquor app). No score talk, no SaaS words, never the word AI."
    )
    opener: str = Field(
        description="A 3-sentence cold-call opener in the cleaner's voice, ready to read off "
        "the page. Sentence 1 references the trigger. Sentence 2 cites a nearby existing "
        "customer the cleaner already serves. Sentence 3 proposes a 15-minute walk-through "
        "this week."
    )


NARRATIVE_SYSTEM = """You write the prose for a commercial cleaning company's weekly prospect brief.
You do NOT score leads — the score is already decided. You write exactly two things per lead:
a one-sentence `why` and a three-sentence `opener`, in the voice of the cleaning company's owner.

Voice rules:
  - Plain, direct, owner-to-owner. No SaaS language. Never use the word "AI".
  - The opener is three sentences: (1) reference the trigger signal (license, permit, liquor
    application) with its date; (2) cite a nearby existing customer the cleaner already serves;
    (3) propose a specific 15-minute walk-through this week.
  - Match the tone to the lead's strength. A strong lead earns an eager opener. A thin lead earns
    an honest one — do not oversell a weak fit. Cleaners trust briefs that admit "this one is a
    stretch" more than briefs full of forced enthusiasm.

## Tone calibration by tier

### Strong (tier A, score >= 70) — eager, specific
why: "Brand-new dental license filed last Tuesday — they're fitting out now and haven't signed a
janitorial vendor yet."
opener: "I saw your new dental license post on May 26 and figured you're staring down a build-out
punch list. We already clean Lincoln Park Pediatrics three blocks over, so we know the after-hours
rhythm a dental suite needs. Could I swing by for a 15-minute walk-through this week before you lock
in a vendor?"

### Borderline (tier B/C) — warm but honest
why: "Retail food license filed last week — the cafe is fitting out, but it's a cafe not an office
and the closest account is half a mile away."
opener: "Saw your retail food license go through on May 22 — congrats on getting the roaster open.
We clean Halsted Professional Plaza about a half-mile up the street, so we're already in the
neighborhood Monday through Friday. If it helps, I can stop in for 15 minutes this week and price a
floor and equipment program."

### Weak (near drop) — candid, low-pressure
why: "Residential renovation permit — not a business and well off any existing route."
opener: "I noticed the building permit on Paulina filed last week — looks residential rather than a
commercial fit-out. We mostly serve offices and clinics nearby, so this one may be outside our lane.
If you know a neighboring business breaking ground, I'd gladly take a 15-minute look this week
instead."
"""


@dataclass
class NarrativeResult:
    narrative: LeadNarrative
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def _build_anthropic_client() -> anthropic.Anthropic:
    """Anthropic client, wrapped by Langfuse when keys are present. Falls back
    to the raw client if the optional integration import fails."""
    api_key = get_settings().anthropic_api_key
    if os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip():
        try:  # pragma: no cover - optional install
            from langfuse.anthropic import Anthropic as LangfuseAnthropic  # type: ignore

            return LangfuseAnthropic(api_key=api_key)
        except Exception:
            pass
    return anthropic.Anthropic(api_key=api_key)


def _anthropic_client() -> instructor.Instructor:
    return instructor.from_anthropic(
        _build_anthropic_client(), mode=instructor.Mode.ANTHROPIC_TOOLS
    )


def customer_context(customers: list[Customer], max_rows: int = 25) -> str:
    """Cache-friendly serialization of the cleaner's book (top accounts by rev)."""
    rows = sorted(customers, key=lambda c: -(c.monthly_rev or 0))[:max_rows]
    return "\n".join(
        f"- {c.name} | {c.category or 'unknown'} | {c.address} | ${(c.monthly_rev or 0):.0f}/mo"
        for c in rows
    )


def _usage_cost(usage: object) -> tuple[float, int, int, int, int]:
    if usage is None:
        return 0.0, 0, 0, 0, 0
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cost = (
        input_tokens * HAIKU_INPUT_PER_M / 1_000_000
        + output_tokens * HAIKU_OUTPUT_PER_M / 1_000_000
        + cache_read * HAIKU_CACHE_READ_PER_M / 1_000_000
        + cache_write * HAIKU_CACHE_WRITE_PER_M / 1_000_000
    )
    return cost, input_tokens, output_tokens, cache_read, cache_write


def template_narrative(
    lead: EnrichedLead,
    nearest_customer: Customer | None,
    tier: str,
) -> LeadNarrative:
    """Deterministic offline narrative — no API key required. Keeps the same
    voice shape so an offline brief is still presentable (and the e2e proof can
    run free)."""
    name = lead.dba or lead.name
    trigger = lead.source.replace("-", " ").replace("nyc:", "NYC ")
    when = lead.date_issued.isoformat() if lead.date_issued else "this week"
    near = nearest_customer.name if nearest_customer else "an account a few blocks away"
    if tier in ("A", "B"):
        why = f"New {trigger} filing dated {when} — fitting out now and likely shopping for a vendor."
    else:
        why = f"{trigger} filing dated {when} — worth a look but a softer fit for your route."
    opener = (
        f"I noticed {name} filed {trigger} on {when}. "
        f"We already clean {near}, so we're in the neighborhood on a regular rhythm. "
        f"Could I stop by for a 15-minute walk-through this week?"
    )
    return LeadNarrative(why=why, opener=opener)


@wrap_claude_call("write_narrative")
def write_narrative(
    lead: EnrichedLead,
    *,
    score: int,
    tier: str,
    category: str,
    signal_class: str,
    margin_est_monthly: float,
    drive_minutes: float | None,
    customers: list[Customer],
    nearest_customer: Customer | None = None,
    client_id: str | None = None,
    pipeline_run_id: int | None = None,
) -> NarrativeResult:
    """Write the why + opener for one already-scored lead.

    Offline (no API key) returns a deterministic template narrative at zero
    cost. Online, the system block (voice rules + tone calibration) and the
    customer book are prompt-cache-eligible across the week's batch.
    """
    if not get_settings().anthropic_api_key:
        return NarrativeResult(narrative=template_narrative(lead, nearest_customer, tier))

    book = customer_context(customers)
    nearest_block = ""
    if nearest_customer:
        nearest_block = (
            f"\nNearest existing customer:\n"
            f"  Name: {nearest_customer.name}\n"
            f"  Category: {nearest_customer.category}\n"
            f"  Monthly margin proxy: ${(nearest_customer.monthly_rev or 0):.0f}\n"
        )

    user = (
        f"Lead (already scored — write prose only):\n"
        f"  Name: {lead.dba or lead.name}\n"
        f"  Address: {lead.address}\n"
        f"  Category: {category}\n"
        f"  Trigger source: {lead.source}\n"
        f"  Trigger date: {lead.date_issued or 'this week'}\n"
        f"  Signal class: {signal_class}\n"
        f"  Score: {score} (tier {tier})\n"
        f"  Estimated monthly margin: ${margin_est_monthly:.0f}\n"
        f"  Drive minutes off route: {drive_minutes if drive_minutes is not None else '—'}\n"
        f"{nearest_block}\n"
        f"Write the `why` (1 sentence) and `opener` (3 sentences) matching the tier tone."
    )

    client = _anthropic_client()
    narrative, raw = client.messages.create_with_completion(
        model=MODEL_ID,
        max_tokens=400,
        response_model=LeadNarrative,
        system=[
            {"type": "text", "text": NARRATIVE_SYSTEM, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"CLIENT_CUSTOMER_BOOK:\n{book}", "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": user}],
    )

    cost, in_tok, out_tok, cache_r, cache_w = _usage_cost(getattr(raw, "usage", None))
    return NarrativeResult(
        narrative=narrative,
        cost_usd=cost,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_tokens=cache_r,
        cache_write_tokens=cache_w,
    )
