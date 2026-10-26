"""Central config, loaded from data/.env / environment. Everything
path-related defaults to living inside the install's own data/ folder
(gitignored), so the whole install — code, Python runtime, data lake,
uploads, chat history — is one deletable tree:

    app/              this package, plus the launch scripts
    runtime/          Python environment (created by the launch scripts)
    data/
      .env            settings (created from app/.env.example)
      biomni_data/    biomni's own data lake (it nests this dir name itself
                       under whatever path A1 is given — see biomni_runner)
      user/           our application data: uploads, chat history
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
_DATA_ROOT = REPO_ROOT / "data"
load_dotenv(_DATA_ROOT / ".env")


def _env(name: str, default: str) -> str:
    """The env var, or the default when it's unset *or empty* — .env.example
    ships blank lines like `BIOMNI_DATA_PATH=`, and os.getenv's own default
    only covers unset (an empty path would resolve to the app folder)."""
    return os.getenv(name) or default


class Config:
    # Optional: the Bedrock model to start on (else app/providers.py's default).
    MODEL: str | None = os.getenv("BIOMNI_MODEL") or None

    # Passed straight to A1(path=...); A1 creates/expects a "biomni_data"
    # subfolder under this path, so this is data/, not data/biomni_data.
    DATA_PATH: Path = Path(_env("BIOMNI_DATA_PATH", str(_DATA_ROOT))).resolve()

    # User-uploaded files the agent can read.
    UPLOAD_PATH: Path = Path(
        _env("BIOMNI_UPLOAD_PATH", str(_DATA_ROOT / "user" / "uploads"))
    ).resolve()
    # Raw instrument files (mzML mass spec, etc.) routinely run several
    # hundred MB to a few GB — default well above typical small attachments.
    MAX_CONTENT_LENGTH: int = int(_env("MAX_UPLOAD_MB", "2048")) * 1024 * 1024

    # Persistent chat history.
    CHAT_DB: Path = Path(
        _env("BIOMNI_CHAT_DB", str(_DATA_ROOT / "user" / "chats.db"))
    ).resolve()

    # Files the agent generates during a run (reports, CSVs, plots) — each
    # task gets its own subfolder, named by task id, so outputs from
    # different runs never collide and can be served back to the UI.
    OUTPUT_PATH: Path = Path(
        _env("BIOMNI_OUTPUT_PATH", str(_DATA_ROOT / "user" / "outputs"))
    ).resolve()

    # Generated harness state: cached environment-capability probes (so the
    # agent doesn't re-discover the same pyopenms API surface by trial and
    # error every run) and verified working-code recipes (see services/hints.py).
    HARNESS_PATH: Path = Path(
        _env("BIOMNI_HARNESS_PATH", str(_DATA_ROOT / "user" / "harness"))
    ).resolve()

    HOST: str = _env("HOST", "127.0.0.1")
    PORT: int = int(_env("PORT", "8000"))

    # Cognito sign-in, whose credentials sign the Bedrock calls (see app/auth.py). These
    # are public identifiers, not secrets — a browser app would embed them.
    # One region for the Cognito pools and Bedrock (Mantle's Claude models are
    # in us-east-1). BEDROCK_REGION can split them: the identity pool's
    # credentials work in any region the role's policy allows.
    AWS_REGION: str = _env("AWS_REGION", "us-east-1")
    BEDROCK_REGION: str = _env("BEDROCK_REGION", AWS_REGION)
    COGNITO_DOMAIN: str = os.getenv("COGNITO_DOMAIN", "").rstrip("/")
    COGNITO_CLIENT_ID: str = os.getenv("COGNITO_CLIENT_ID", "")
    COGNITO_USER_POOL_ID: str = os.getenv("COGNITO_USER_POOL_ID", "")
    COGNITO_IDENTITY_POOL_ID: str = os.getenv("COGNITO_IDENTITY_POOL_ID", "")
    # Must exactly match an "Allowed callback URL" on the app client.
    COGNITO_REDIRECT_URI: str = _env("COGNITO_REDIRECT_URI", f"http://localhost:{PORT}/callback")
