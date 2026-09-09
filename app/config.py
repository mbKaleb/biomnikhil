"""Central config, loaded from .env / environment. Everything path-related
defaults to living inside the disposable box (one level above the repo).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")


class Config:
    PROVIDER: str = os.getenv("BIOMNI_PROVIDER", "anthropic")
    MODEL: str | None = os.getenv("BIOMNI_MODEL") or None

    # Default keeps the ~11 GB data lake in the box, next to the repo,
    # so deleting the box deletes it too.
    DATA_PATH: Path = Path(
        os.getenv("BIOMNI_DATA_PATH", str(REPO_ROOT.parent / "data"))
    ).resolve()

    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))
