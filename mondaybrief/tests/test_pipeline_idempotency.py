from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest


CLIENT_UUID = "11111111-1111-1111-1111-111111111111"


def _client_row() -> dict:
    return {
        "id": CLIENT_UUID,
        "slug": "ek",
        "name": "E&K",
        "contact_email": "owner@example.com",
        "postmark_stream": "outbound",
        "metros": ["chicago"],
        "stripe_customer_id": "cus_123",
        "active": True,
    }


def test_online_run_skips_already_shipped_week(monkeypatch: pytest.MonkeyPatch) -> None:
    from mondaybrief import pipeline

    monkeypatch.setattr(pipeline, "_load_client_row", lambda _client_id: _client_row())
    monkeypatch.setattr(pipeline, "_can_run", lambda _client_id, _row: True)
    monkeypatch.setattr(
        pipeline,
        "_acquire_pipeline_run_start",
        lambda **_kwargs: pipeline.PipelineRunLease(
            run_id=42,
            acquired=False,
            status="shipped",
            pdf_path="out/brief.pdf",
            postmark_delivery_id="postmark-1",
            cost_usd=1.25,
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "send_brief",
        lambda *_args, **_kwargs: pytest.fail("already-shipped retry must not send"),
    )

    result = pipeline.run(client_id=CLIENT_UUID)

    assert result["status"] == "shipped"
    assert result["skipped"] == "already_shipped"
    assert result["run_id"] == 42
    assert result["postmark_message_id"] == "postmark-1"


def test_online_run_keeps_shipped_status_when_cost_rollup_fails_after_send(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mondaybrief import pipeline

    events: list[object] = []

    monkeypatch.setattr(pipeline, "_load_client_row", lambda _client_id: _client_row())
    monkeypatch.setattr(pipeline, "_can_run", lambda _client_id, _row: True)
    monkeypatch.setattr(
        pipeline,
        "_acquire_pipeline_run_start",
        lambda **_kwargs: pipeline.PipelineRunLease(
            run_id=7, acquired=True, status="running"
        ),
    )
    monkeypatch.setattr(pipeline, "_load_customers", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pipeline, "_pull_permits", lambda **_kwargs: [])
    monkeypatch.setattr(pipeline, "_geocode_leads", lambda leads, **_kwargs: [])
    monkeypatch.setattr(pipeline, "_filter_to_service_area", lambda leads, _customers: [])
    monkeypatch.setattr(pipeline, "drop_existing_customers", lambda _customers, leads: (leads, {"out": 0}))
    monkeypatch.setattr(pipeline, "annotate_drive_times", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "_build_profile", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(pipeline, "_score", lambda *_args, **_kwargs: ([], 0.0))

    def fake_render_pdf(_bundle, pdf_path, **_kwargs):
        Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)
        Path(pdf_path).write_bytes(b"%PDF test")
        return Path(pdf_path)

    def fake_send(*_args, **_kwargs):
        events.append("send")
        return "postmark-1"

    def fake_finish(run_id, *, status, postmark_delivery_id, error=None, **_kwargs):
        events.append(("finish", run_id, status, postmark_delivery_id, error))

    def fake_cost(*_args, **_kwargs):
        events.append("cost")
        raise RuntimeError("cost db write failed")

    monkeypatch.setattr(pipeline, "render_pdf", fake_render_pdf)
    monkeypatch.setattr(pipeline, "send_brief", fake_send)
    monkeypatch.setattr(pipeline, "_update_pipeline_run_finish", fake_finish)
    monkeypatch.setattr(pipeline, "update_run_cost", fake_cost)

    result = pipeline.run(client_id=CLIENT_UUID, out_dir=tmp_path)

    assert result["status"] == "shipped"
    assert result["postmark_message_id"] == "postmark-1"
    assert events[0] == "send"
    assert events[1] == ("finish", 7, "shipped", "postmark-1", None)
    assert events[2] == "cost"
    assert events[3][0:4] == ("finish", 7, "shipped", "postmark-1")
    assert "post_send_error" in events[3][4]


def test_schema_has_database_enforced_weekly_send_lock() -> None:
    schema = Path(__file__).resolve().parents[1].joinpath("schema.sql").read_text()

    assert "uniq_pipeline_runs_client_week_active_send" in schema
    assert "ON pipeline_runs (client_uuid, week_of)" in schema
    assert "WHERE status IN ('running', 'shipped')" in schema

