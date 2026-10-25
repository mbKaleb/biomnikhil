"""Central config, loaded from .env / environment. Everything path-related
defaults to living inside the repo's own data/ folder (gitignored), so the
whole box — code, data lake, uploads, chat history — is one deletable tree.

    data/
      biomni_data/    biomni's own data lake (it nests this dir name itself
                       under whatever path A1 is given — see biomni_runner)
      user/           our application data: uploads, chat history
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

_DATA_ROOT = REPO_ROOT / "data"


class Config:
    PROVIDER: str = os.getenv("BIOMNI_PROVIDER", "anthropic")
    MODEL: str | None = os.getenv("BIOMNI_MODEL") or None

    # Passed straight to A1(path=...); A1 creates/expects a "biomni_data"
    # subfolder under this path, so this is data/, not data/biomni_data.
    DATA_PATH: Path = Path(os.getenv("BIOMNI_DATA_PATH", str(_DATA_ROOT))).resolve()

    # User-uploaded files the agent can read.
    UPLOAD_PATH: Path = Path(
        os.getenv("BIOMNI_UPLOAD_PATH", str(_DATA_ROOT / "user" / "uploads"))
    ).resolve()
    # Raw instrument files (mzML mass spec, etc.) routinely run several
    # hundred MB to a few GB — default well above typical small attachments.
    MAX_CONTENT_LENGTH: int = int(os.getenv("MAX_UPLOAD_MB", "2048")) * 1024 * 1024

    # Persistent chat history.
    CHAT_DB: Path = Path(
        os.getenv("BIOMNI_CHAT_DB", str(_DATA_ROOT / "user" / "chats.db"))
    ).resolve()

    # Files the agent generates during a run (reports, CSVs, plots) — each
    # task gets its own subfolder, named by task id, so outputs from
    # different runs never collide and can be served back to the UI.
    OUTPUT_PATH: Path = Path(
        os.getenv("BIOMNI_OUTPUT_PATH", str(_DATA_ROOT / "user" / "outputs"))
    ).resolve()

    # Generated harness state: cached environment-capability probes (so the
    # agent doesn't re-discover the same pyopenms API surface by trial and
    # error every run) and verified working-code recipes (see services/hints.py).
    HARNESS_PATH: Path = Path(
        os.getenv("BIOMNI_HARNESS_PATH", str(_DATA_ROOT / "user" / "harness"))
    ).resolve()

    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))

    # Cognito sign-in for the "bedrock" provider (see app/auth.py). These
    # are public identifiers, not secrets — a browser app would embed them.
    AWS_REGION: str = os.getenv("AWS_REGION", "us-west-2")
    COGNITO_DOMAIN: str = os.getenv("COGNITO_DOMAIN", "").rstrip("/")
    COGNITO_CLIENT_ID: str = os.getenv("COGNITO_CLIENT_ID", "")
    COGNITO_USER_POOL_ID: str = os.getenv("COGNITO_USER_POOL_ID", "")
    COGNITO_IDENTITY_POOL_ID: str = os.getenv("COGNITO_IDENTITY_POOL_ID", "")
    # Must exactly match an "Allowed callback URL" on the app client.
    COGNITO_REDIRECT_URI: str = (
        os.getenv("COGNITO_REDIRECT_URI") or f"http://localhost:{PORT}/callback"
    )
