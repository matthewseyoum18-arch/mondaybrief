"""Repo-root shim. The real FastAPI app lives in ``mondaybrief.app``.

Kept only so ``python main.py`` and older deploy configs that reference a
top-level ``main:app`` keep working. New configs should target the in-package
path directly::

    uvicorn mondaybrief.app:app --host 0.0.0.0 --port $PORT

Defining the app in one place (``mondaybrief.app``) avoids the drift this shim
previously had, where the root copy silently lacked the checkout + unsubscribe
routers.
"""
from __future__ import annotations

from mondaybrief.app import app  # noqa: F401  (re-exported for `main:app`)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("mondaybrief.app:app", host="0.0.0.0", port=8000)
