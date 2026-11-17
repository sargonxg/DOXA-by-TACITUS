"""Resolve optional application assets in a checkout or a self-contained wheel."""
from __future__ import annotations

import os
from pathlib import Path


def asset_root() -> Path:
    """The explicit external corpus location, or the source checkout root."""
    configured = os.environ.get("DOXA_ASSET_ROOT")
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[1]


def frontend_distribution() -> Path:
    """Prefer an installed wheel's UI; use a checkout build during development."""
    packaged = Path(__file__).with_name("static")
    if (packaged / "index.html").is_file():
        return packaged
    return asset_root() / "frontend" / "dist"
