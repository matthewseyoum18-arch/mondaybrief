"""Pin the signal-layer feature-flag contract.

`signal_layer_enabled=False` must reproduce v1 behaviour exactly; `True` (the
default) suppresses below-floor leads. This makes the two paths' divergence
intentional and regression-guarded rather than accidental.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mondaybrief.config import Settings  # noqa: E402
from mondaybrief.models import EnrichedLead  # noqa: E402
from mondaybrief.score import claude_score  # noqa: E402

AS_OF = date(2026, 6, 1)


def _unknown_lead():
    # An unmatched source -> classify_signal == 'unknown'. v1 keeps it (only
    # 'disqualified' is dropped); v2 suppresses it (fused 0.05 < 0.40 floor).
    return EnrichedLead(
        source="scaffolding-x", source_id="s1", name="Mystery Holdings LLC",
        address="9 Nowhere St", city="Chicago", state="IL",
        date_issued=AS_OF, raw_json={}, drive_minutes=6.0,
    )


def test_flag_off_keeps_unknown_lead_like_v1(monkeypatch):
    monkeypatch.setattr(claude_score, "get_settings", lambda: Settings(signal_layer_enabled=False))
    scored, cost = claude_score.score_many([_unknown_lead()], [], as_of=AS_OF, top_n=0)
    assert cost == 0.0
    assert any("Mystery" in s.name for s in scored), "v1 path must keep the unknown lead"
    # Legacy path leaves the v2 confidence fields unset.
    assert scored[0].signal_confidence is None


def test_flag_on_suppresses_unknown_lead(monkeypatch):
    monkeypatch.setattr(claude_score, "get_settings", lambda: Settings(signal_layer_enabled=True))
    scored, _ = claude_score.score_many([_unknown_lead()], [], as_of=AS_OF, top_n=0)
    assert not any("Mystery" in s.name for s in scored), "v2 floor must suppress the weak unknown lead"
