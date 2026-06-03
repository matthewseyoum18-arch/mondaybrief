"""Langfuse wiring for Claude calls.

Repos:
  - langfuse-python: https://github.com/langfuse/langfuse-python (MIT)

Langfuse is optional — pipeline must keep working in offline / dev mode
without ``LANGFUSE_PUBLIC_KEY`` set. ``init_langfuse()`` returns ``None``
when keys are missing and ``wrap_claude_call`` becomes a no-op
passthrough decorator. This lets us keep one code path in production
and tests without spraying ``if langfuse:`` branches through the
scoring module.

Usage::

    from .observability.langfuse_setup import init_langfuse, wrap_claude_call

    langfuse = init_langfuse()

    @wrap_claude_call("score_lead")
    def score_lead(lead, customers, *, client_id=None, pipeline_run_id=None):
        ...

The decorator threads ``client_id`` and ``pipeline_run_id`` kwargs into
the trace metadata when present, so every Claude call is correlated to
the cleaner and the pipeline run it belongs to.
"""
from __future__ import annotations

import os
from functools import wraps
from typing import Any, Callable, Optional

try:  # pragma: no cover - import guard
    from langfuse import Langfuse  # type: ignore
except Exception:  # pragma: no cover - keep pipeline runnable without langfuse
    Langfuse = None  # type: ignore[assignment]

try:  # pragma: no cover - import guard, observe may live under a few paths
    from langfuse.decorators import observe as _langfuse_observe  # type: ignore
except Exception:  # pragma: no cover
    try:
        from langfuse import observe as _langfuse_observe  # type: ignore
    except Exception:
        _langfuse_observe = None  # type: ignore[assignment]


_DEFAULT_HOST = "https://cloud.langfuse.com"
_client: Optional[Any] = None


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def init_langfuse() -> Optional[Any]:
    """Initialise (and memoise) the Langfuse client.

    Returns ``None`` when ``LANGFUSE_PUBLIC_KEY`` is not set so callers can
    short-circuit. Safe to call repeatedly — only the first call hits the
    network.
    """
    global _client
    if _client is not None:
        return _client

    public_key = _env("LANGFUSE_PUBLIC_KEY")
    secret_key = _env("LANGFUSE_SECRET_KEY")
    host = _env("LANGFUSE_HOST") or _DEFAULT_HOST

    if not public_key or not secret_key or Langfuse is None:
        return None

    _client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
    return _client


def _noop_decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)
    def _inner(*args: Any, **kwargs: Any) -> Any:
        # Observability-only kwargs (client_id, pipeline_run_id) are left in
        # kwargs so the wrapped function can use them for personalization
        # (e.g. per-client feedback context). The function must accept them
        # as explicit kwargs.
        return fn(*args, **kwargs)

    return _inner


def wrap_claude_call(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator factory that tags a function's Langfuse trace.

    Tags ``client_id`` and ``pipeline_run_id`` (read from kwargs) onto
    the trace metadata. The kwargs are NOT stripped — they remain in
    kwargs so the wrapped function can use them for personalization
    (e.g. per-client feedback context). The wrapped function must
    accept ``client_id`` and ``pipeline_run_id`` as explicit kwargs.
    """

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        client = init_langfuse()
        if client is None or _langfuse_observe is None:
            return _noop_decorator(fn)

        observed = _langfuse_observe(name=name)(fn)

        @wraps(fn)
        def _inner(*args: Any, **kwargs: Any) -> Any:
            client_id = kwargs.get("client_id", None)
            pipeline_run_id = kwargs.get("pipeline_run_id", None)
            metadata: dict[str, Any] = {}
            if client_id is not None:
                metadata["client_id"] = client_id
            if pipeline_run_id is not None:
                metadata["pipeline_run_id"] = pipeline_run_id
            try:
                # Newer Langfuse SDKs expose update_current_trace; older
                # ones use context.update. Both raise if no active trace.
                if metadata:
                    update = getattr(client, "update_current_trace", None)
                    if callable(update):
                        update(metadata=metadata, tags=[f"client:{client_id}"] if client_id else None)
            except Exception:
                # Never let observability break the pipeline
                pass
            return observed(*args, **kwargs)

        return _inner

    return _decorator
