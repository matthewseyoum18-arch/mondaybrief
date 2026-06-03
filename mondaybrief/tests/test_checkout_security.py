from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException


CLIENT_UUID = "11111111-1111-1111-1111-111111111111"


def test_checkout_derives_client_and_email_from_authenticated_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mondaybrief.billing import checkout

    captured: dict = {}

    monkeypatch.setattr(checkout, "verify_link", lambda token: CLIENT_UUID if token == "good" else None)
    monkeypatch.setattr(
        checkout,
        "execute",
        lambda _sql, _params: [(CLIENT_UUID, "owner@example.com", True)],
    )
    monkeypatch.setattr(
        checkout,
        "get_settings",
        lambda: SimpleNamespace(app_base_url="https://app.mondaybrief.test"),
    )

    def fake_create_checkout_session(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(url="https://checkout.stripe.test/session", id="cs_test_123")

    monkeypatch.setattr(checkout, "create_checkout_session", fake_create_checkout_session)

    result = checkout.start_checkout(
        checkout.CheckoutRequest(
            success_url="/billing/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="/billing/cancel",
        ),
        authorization="Bearer good",
    )

    assert result.session_id == "cs_test_123"
    assert str(captured["client_id"]) == CLIENT_UUID
    assert captured["client_email"] == "owner@example.com"
    assert captured["success_url"] == (
        "https://app.mondaybrief.test/billing/success?session_id={CHECKOUT_SESSION_ID}"
    )
    assert captured["cancel_url"] == "https://app.mondaybrief.test/billing/cancel"


def test_checkout_rejects_missing_or_invalid_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mondaybrief.billing import checkout

    monkeypatch.setattr(checkout, "verify_link", lambda _token: None)

    with pytest.raises(HTTPException) as exc:
        checkout.start_checkout(checkout.CheckoutRequest(), authorization="Bearer bad")

    assert exc.value.status_code == 401


def test_checkout_rejects_inactive_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from mondaybrief.billing import checkout

    monkeypatch.setattr(checkout, "verify_link", lambda _token: CLIENT_UUID)
    monkeypatch.setattr(
        checkout,
        "execute",
        lambda _sql, _params: [(CLIENT_UUID, "owner@example.com", False)],
    )

    with pytest.raises(HTTPException) as exc:
        checkout.start_checkout(checkout.CheckoutRequest(), authorization="Bearer good")

    assert exc.value.status_code == 403


def test_checkout_rejects_attacker_controlled_return_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mondaybrief.billing import checkout

    monkeypatch.setattr(checkout, "verify_link", lambda _token: CLIENT_UUID)
    monkeypatch.setattr(
        checkout,
        "execute",
        lambda _sql, _params: [(CLIENT_UUID, "owner@example.com", True)],
    )
    monkeypatch.setattr(
        checkout,
        "get_settings",
        lambda: SimpleNamespace(app_base_url="https://app.mondaybrief.test"),
    )
    monkeypatch.setattr(
        checkout,
        "create_checkout_session",
        lambda **_kwargs: pytest.fail("invalid redirect must not create Stripe session"),
    )

    with pytest.raises(HTTPException) as exc:
        checkout.start_checkout(
            checkout.CheckoutRequest(success_url="https://evil.test/steal"),
            authorization="Bearer good",
        )

    assert exc.value.status_code == 400


def test_checkout_rejects_non_allowlisted_same_origin_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mondaybrief.billing import checkout

    monkeypatch.setattr(checkout, "verify_link", lambda _token: CLIENT_UUID)
    monkeypatch.setattr(
        checkout,
        "execute",
        lambda _sql, _params: [(CLIENT_UUID, "owner@example.com", True)],
    )
    monkeypatch.setattr(
        checkout,
        "get_settings",
        lambda: SimpleNamespace(app_base_url="https://app.mondaybrief.test"),
    )

    with pytest.raises(HTTPException) as exc:
        checkout.start_checkout(
            checkout.CheckoutRequest(cancel_url="https://app.mondaybrief.test/other"),
            authorization="Bearer good",
        )

    assert exc.value.status_code == 400
