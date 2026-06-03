"""Delivery-layer wiring tests (CAN-SPAM suppression + List-Unsubscribe).

Offline — no network, no DB. ``is_suppressed`` and the Postmark client are
stubbed via monkeypatch; the webhook suppression policy is tested as a pure
function so it needs neither.
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


class _FakeEmails:
    def __init__(self) -> None:
        self.kwargs = None

    def send(self, **kwargs):
        self.kwargs = kwargs
        return {"MessageID": "fake-123"}


class _FakeClient:
    def __init__(self) -> None:
        self.emails = _FakeEmails()


# ---------------------------------------------------------------------------
# send.postmark.send_brief
# ---------------------------------------------------------------------------


def test_send_brief_skips_suppressed(monkeypatch, tmp_path) -> None:
    """A suppressed recipient short-circuits the send (no API call, empty id)."""
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", "test-secret-32-bytes-xxxxxxxxxxxx")
    from mondaybrief.send import postmark

    fake = _FakeClient()
    monkeypatch.setattr(postmark, "_client", lambda: fake)
    monkeypatch.setattr(postmark, "is_suppressed", lambda email: True)

    pdf = tmp_path / "b.pdf"
    pdf.write_bytes(b"%PDF-1.7 test")

    mid = postmark.send_brief(_bundle(), pdf, to_email="x@y.com")

    assert mid == ""
    assert fake.emails.kwargs is None


def test_send_brief_adds_unsubscribe_and_metadata(monkeypatch, tmp_path) -> None:
    """Non-suppressed send carries List-Unsubscribe headers, Metadata, footer."""
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", "test-secret-32-bytes-xxxxxxxxxxxx")
    from mondaybrief.send import postmark

    fake = _FakeClient()
    monkeypatch.setattr(postmark, "_client", lambda: fake)
    monkeypatch.setattr(postmark, "is_suppressed", lambda email: False)

    pdf = tmp_path / "b.pdf"
    pdf.write_bytes(b"%PDF-1.7 test")

    mid = postmark.send_brief(
        _bundle(), pdf, to_email="Owner@Y.com", client_id="cid-1", pipeline_run_id=7
    )

    assert mid == "fake-123"
    kw = fake.emails.kwargs
    headers = {h["Name"]: h["Value"] for h in kw["Headers"]}
    assert "/unsubscribe/" in headers["List-Unsubscribe"]
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert kw["Metadata"]["client_id"] == "cid-1"
    assert kw["Metadata"]["pipeline_run_id"] == "7"
    # CAN-SPAM footer: unsubscribe link in text, unsubscribe word in html.
    assert "/unsubscribe/" in kw["TextBody"]
    assert "Unsubscribe" in kw["HtmlBody"]


# ---------------------------------------------------------------------------
# observability.postmark_webhook._suppression_for_event  (pure policy)
# ---------------------------------------------------------------------------


def test_suppression_for_spam_complaint() -> None:
    from mondaybrief.observability.postmark_webhook import _suppression_for_event

    ev = {"RecordType": "SpamComplaint", "Email": "a@b.com"}
    assert _suppression_for_event(ev) == ("a@b.com", "spam_complaint")


def test_suppression_for_hard_bounce() -> None:
    from mondaybrief.observability.postmark_webhook import _suppression_for_event

    ev = {"RecordType": "Bounce", "Type": "HardBounce", "Email": "a@b.com"}
    assert _suppression_for_event(ev) == ("a@b.com", "hard_bounce")


def test_no_suppression_for_soft_bounce() -> None:
    from mondaybrief.observability.postmark_webhook import _suppression_for_event

    ev = {"RecordType": "Bounce", "Type": "SoftBounce", "Email": "a@b.com"}
    assert _suppression_for_event(ev) is None


def test_suppression_for_subscription_change() -> None:
    from mondaybrief.observability.postmark_webhook import _suppression_for_event

    ev = {"RecordType": "SubscriptionChange", "Recipient": "a@b.com", "SuppressSending": True}
    assert _suppression_for_event(ev) == ("a@b.com", "unsubscribe")


def test_no_suppression_for_resubscribe() -> None:
    from mondaybrief.observability.postmark_webhook import _suppression_for_event

    ev = {"RecordType": "SubscriptionChange", "Recipient": "a@b.com", "SuppressSending": False}
    assert _suppression_for_event(ev) is None


def test_no_suppression_for_delivery() -> None:
    from mondaybrief.observability.postmark_webhook import _suppression_for_event

    ev = {"RecordType": "Delivery", "Email": "a@b.com"}
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
    """If suppression can't be read, abort the send (no API call) and propagate."""
    monkeypatch.setenv("FEEDBACK_TOKEN_SECRET", "test-secret-32-bytes-xxxxxxxxxxxx")
    from mondaybrief.send import postmark
    from mondaybrief.send.suppression import SuppressionCheckError

    fake = _FakeClient()
    monkeypatch.setattr(postmark, "_client", lambda: fake)

    def _raise(email):
        raise SuppressionCheckError("db down")

    monkeypatch.setattr(postmark, "is_suppressed", _raise)

    pdf = tmp_path / "b.pdf"
    pdf.write_bytes(b"%PDF-1.7 test")

    with pytest.raises(SuppressionCheckError):
        postmark.send_brief(_bundle(), pdf, to_email="x@y.com")

    assert fake.emails.kwargs is None
