"""Stripe Checkout + recurring billing + dunning for MondayBrief.

v1 wires a single price ($149/mo, ``STRIPE_PRICE_ID_MONTHLY``) to a
per-client Stripe Customer/Subscription pair. Webhook events keep the
local ``clients`` + ``subscriptions`` tables in sync so the pipeline can
gate brief delivery on ``clients.active`` without ever hitting Stripe at
send time. Dunning is intentionally thin: Stripe Smart Retries owns the
retry schedule; we only fire a Postmark notification on each
``invoice.payment_failed`` event.
"""
from __future__ import annotations

from .stripe_client import (
    create_checkout_session,
    create_or_get_customer,
    get_subscription_status,
)
from .dunning import send_dunning_email

__all__ = [
    "create_checkout_session",
    "create_or_get_customer",
    "get_subscription_status",
    "send_dunning_email",
]
