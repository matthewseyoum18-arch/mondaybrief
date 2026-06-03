"""Delivery-layer tests (Resend): CAN-SPAM suppression + List-Unsubscribe.

Offline — no network, no DB. ``send_email`` and ``is_suppressed`` are stubbed
via monkeypatch; the webhook suppression policy and the suppression fail-closed
logic are tested as pure / connection-stubbed functions so they need neither.
"""
from __future__ import annotations

import contextlib
from datetime import date, datetime, timezone

import pytest


def _bundle():
    from mondaybrief.models import BriefBundle

    return BriefBundle(
        client_id="ek",
        client_name="E&K",
        metro="Chicago, IL",
        week_of=date(2026, 6, 1),
        generated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        leads=[],
        customer_count=3,
        permits_pulled=8,
        leads_inside_area=1,
        leads_after_dedup=1,
    )


# ---------------------------------------------------------------------------
# send.brief.send_brief
# ---------------------------------------------------------------------------


def test_send_brief_skips_suppressed(monkeypatch, tmp_path) -> None:
    """A suppressed recipient short-circuits the send (no Resend call, empty id)."""
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", "test-secret-32-bytes-xxxxxxxxxxxx")
    from mondaybrief.send import brief

    calls: list[dict] = []
    monkeypatch.setattr(brief, "send_email", lambda **kw: calls.append(kw) or "x")
    monkeypatch.setattr(brief, "is_suppressed", lambda email: True)

    pdf = tmp_path / "b.pdf"
    pdf.write_bytes(b"%PDF-1.7 test")

    mid = brief.send_brief(_bundle(), pdf, to_email="x@y.com")

    assert mid == ""
    assert calls == []


def test_send_brief_adds_unsubscribe_and_tags(monkeypatch, tmp_path) -> None:
    """Non-suppressed send carries List-Unsubscribe headers, tags, footer, PDF."""
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", "test-secret-32-bytes-xxxxxxxxxxxx")
    from mondaybrief.send import brief

    captured: dict = {}

    def fake_send_email(**kw):
        captured.update(kw)
        return "resend-123"

    monkeypatch.setattr(brief, "send_email", fake_send_email)
    monkeypatch.setattr(brief, "is_suppressed", lambda email: False)

    pdf = tmp_path / "b.pdf"
    pdf.write_bytes(b"%PDF-1.7 test")

    mid = brief.send_brief(
        _bundle(), pdf, to_email="Owner@Y.com", client_id="cid-1", pipeline_run_id=7
    )

    assert mid == "resend-123"
    headers = captured["headers"]
    assert "/unsubscribe/" in headers["List-Unsubscribe"]
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    tags = {t["name"]: t["value"] for t in captured["tags"]}
    assert tags["client_id"] == "cid-1"
    assert tags["pipeline_run_id"] == "7"
    # CAN-SPAM footer: unsubscribe link in text, unsubscribe word in html.
    assert "/unsubscribe/" in captured["text"]
    assert "Unsubscribe" in captured["html"]
    # PDF attached.
    assert captured["attachments"][0]["filename"].endswith(".pdf")


# ---------------------------------------------------------------------------
# observability.resend_webhook._suppression_for_event  (pure policy)
# ---------------------------------------------------------------------------


def test_suppression_for_spam_complaint() -> None:
    from mondaybrief.observability.resend_webhook import _suppression_for_event

    ev = {"type": "email.complained", "data": {"to": ["a@b.com"]}}
    assert _suppression_for_event(ev) == ("a@b.com", "spam_complaint")


def test_suppression_for_hard_bounce() -> None:
    from mondaybrief.observability.resend_webhook import _suppression_for_event

    ev = {"type": "email.bounced", "data": {"to": ["a@b.com"], "bounce": {"type": "Permanent"}}}
    assert _suppression_for_event(ev) == ("a@b.com", "hard_bounce")


def test_no_suppression_for_soft_bounce() -> None:
    from mondaybrief.observability.resend_webhook import _suppression_for_event

    ev = {"type": "email.bounced", "data": {"to": ["a@b.com"], "bounce": {"type": "Transient"}}}
    assert _suppression_for_event(ev) is None


def test_no_suppression_for_delivered() -> None:
    from mondaybrief.observability.resend_webhook import _suppression_for_event

    ev = {"type": "email.delivered", "data": {"to": ["a@b.com"]}}
    assert _suppression_for_event(ev) is None


# ---------------------------------------------------------------------------
# send.suppression.is_suppressed — fail CLOSED with one retry
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, row) -> None:
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row) -> None:
        self._row = row

    def execute(self, *args, **kwargs):
        return _FakeCursor(self._row)


def _make_connect(behaviors):
    """Fake ``connect()`` context manager driven by a behavior script.

    Each call consumes the next behavior: an Exception instance is raised on
    __enter__ (simulating a DB error); anything else is treated as the row
    that ``SELECT 1`` returns (``None`` -> not suppressed). The last behavior
    repeats if called more times than the list length.
    """
    state = {"i": 0}

    @contextlib.contextmanager
    def cm():
        i = state["i"]
        state["i"] += 1
        b = behaviors[min(i, len(behaviors) - 1)]
        if isinstance(b, Exception):
            raise b
        yield _FakeConn(b)

    return cm


def test_is_suppressed_false_when_absent(monkeypatch) -> None:
    from mondaybrief.send import suppression

    monkeypatch.setattr(suppression, "connect", _make_connect([None]))
    assert suppression.is_suppressed("a@b.com") is False


def test_is_suppressed_true_when_present(monkeypatch) -> None:
    from mondaybrief.send import suppression

    monkeypatch.setattr(suppression, "connect", _make_connect([(1,)]))
    assert suppression.is_suppressed("a@b.com") is True


def test_is_suppressed_retries_once_then_succeeds(monkeypatch) -> None:
    """A single transient DB error is retried, not surfaced."""
    from mondaybrief.send import suppression

    monkeypatch.setattr(
        suppression, "connect", _make_connect([RuntimeError("blip"), (1,)])
    )
    assert suppression.is_suppressed("a@b.com") is True


def test_is_suppressed_fails_closed_after_retry(monkeypatch) -> None:
    """Persistent DB failure raises instead of silently returning False."""
    from mondaybrief.send import suppression

    monkeypatch.setattr(
        suppression,
        "connect",
        _make_connect([RuntimeError("db down"), RuntimeError("db down")]),
    )
    with pytest.raises(suppression.SuppressionCheckError):
        suppression.is_suppressed("a@b.com")


def test_send_brief_aborts_when_suppression_check_fails(monkeypatch, tmp_path) -> None:
    """If suppression can't be read, abort the send (no Resend call) and propagate."""
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", "test-secret-32-bytes-xxxxxxxxxxxx")
    from mondaybrief.send import brief
    from mondaybrief.send.suppression import SuppressionCheckError

    calls: list[dict] = []
    monkeypatch.setattr(brief, "send_email", lambda **kw: calls.append(kw) or "x")

    def _raise(email):
        raise SuppressionCheckError("db down")

    monkeypatch.setattr(brief, "is_suppressed", _raise)

    pdf = tmp_path / "b.pdf"
    pdf.write_bytes(b"%PDF-1.7 test")

    with pytest.raises(SuppressionCheckError):
        brief.send_brief(_bundle(), pdf, to_email="x@y.com")

    assert calls == []
