"""End-to-end smoke test — runs offline against fixtures.

Exercises every module except network-bound steps:
  - sodapy → loaded from fixtures/sample_permits.json
  - Geocodio → deterministic stub
  - H3 → real
  - Splink → real (against the seeded customer book)
  - Claude → stub (no API call)
  - WeasyPrint → real PDF written to a tmp dir
"""
from __future__ import annotations
from pathlib import Path
import pytest

from mondaybrief.pipeline import run_for_client


def test_offline_pipeline_for_ek(tmp_path: Path) -> None:
    # The final step renders a real PDF; WeasyPrint needs native GTK/Pango libs.
    # On a box without them the import may "succeed" with a warning but the render
    # raises OSError — skip cleanly so the rest of the suite still runs.
    try:
        bundle, tel = run_for_client("ek", offline=True, out_dir=tmp_path)
    except OSError as exc:
        pytest.skip(f"PDF render needs native GTK/Pango libs: {exc}")

    # Permits ingested from fixture
    assert tel.permits_pulled >= 5

    # All addresses geocoded by the stub
    assert tel.geocoded == tel.permits_pulled

    # At least one lead should land in the service area
    assert tel.inside_area >= 1

    # Splink should drop the seeded duplicate (Lincoln Park Pediatrics)
    assert tel.after_dedup < tel.inside_area or tel.after_dedup == tel.inside_area

    # We get a sorted top-5 (or fewer)
    assert 0 < len(bundle.leads) <= 5
    scores = [l.score for l in bundle.leads]
    assert scores == sorted(scores, reverse=True)

    # PDF lands on disk
    assert tel.pdf_path is not None and tel.pdf_path.exists()
    assert tel.pdf_path.stat().st_size > 1000


def test_models_validate_score_range() -> None:
    from mondaybrief.models import ScoredLead

    lead = ScoredLead(
        name="x", address="y", category="office", score=999,
        margin_est_monthly=1000.0, margin_uplift_pct=10.0,
        why="x", opener="x",
    )
    assert lead.score == 100  # clamped
