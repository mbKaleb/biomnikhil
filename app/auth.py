"""Cognito sign-in and per-user AWS credentials for Bedrock.

Flow: Cognito hosted login (authorization code + PKCE) → /callback trades
the code for tokens → the ID token goes to the identity pool, which returns
short-lived AWS credentials for the bedrock-app-access role. Each run signs its
Bedrock calls with the credentials of the user who started it (see
_patch_cognito_client in services/biomni_runner.py), so the app holds no
long-lived AWS key and every model call is attributable to a Cognito user.

Tokens stay server-side, in memory, keyed by a random id in the Flask
session cookie — the browser never sees them, and a restart signs everyone
out. Credentials refresh themselves from the refresh token as they near
expiry, so a run longer than an hour keeps going; once Cognito refuses the
refresh token (user disabled, token revoked or expired), the next refresh
fails and the user is signed out.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit

import requests
from flask import Blueprint, abort, redirect, request, session

from .config import Config

bp = Blueprint("auth", __name__)

# Endpoints that work signed out (everything else redirects to /login).
PUBLIC_ENDPOINTS = {"static", "ping", "auth.login", "auth.callback", "auth.logout"}


class SignInRequired(RuntimeError):
    pass


def _claims(jwt: str) -> dict:
    """The JWT's payload, unverified: it came straight from Cognito's token
    endpoint over TLS, and the identity pool verifies the signature itself
    before it issues any credentials."""
    payload = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def _token_request(form: dict) -> dict:
    r = requests.post(
        f"{Config.COGNITO_DOMAIN}/oauth2/token",
        data={"client_id": Config.COGNITO_CLIENT_ID, **form},
        timeout=10,
    )
    if r.status_code == 400 and r.json().get("error") == "invalid_grant":
        raise SignInRequired("Cognito refused the sign-in — sign in again.")
    r.raise_for_status()
    return r.json()


class User:
    def __init__(self, tokens: dict):
        from botocore.credentials import RefreshableCredentials

        self._refresh_token = tokens["refresh_token"]
        self._set_id_token(tokens["id_token"])
        self._identity_id: str | None = None
        self.signed_out = False
        # Fetched now so a broken identity pool or role trust fails at
        # sign-in, not partway through someone's run.
        self.credentials = RefreshableCredentials.create_from_metadata(
            metadata=self._fetch_credentials(),
            refresh_using=self._fetch_credentials,
            method="cognito-identity",
        )

    def _set_id_token(self, id_token: str) -> None:
        claims = _claims(id_token)
        self._id_token = id_token
        self._id_token_exp = float(claims["exp"])
        self.sub = claims["sub"]
        self.email = claims.get("email") or claims.get("cognito:username") or self.sub

    def sign_out(self) -> None:
        """Ends this sign-in for good: a run still going stops at its next
        model call (run_credentials), and Cognito revokes the refresh token,
        so it can't mint new ID tokens or credentials. Best effort on the
        revoke — the token is dropped from memory either way."""
        self.signed_out = True
        try:
            requests.post(
                f"{Config.COGNITO_DOMAIN}/oauth2/revoke",
                data={"token": self._refresh_token, "client_id": Config.COGNITO_CLIENT_ID},
                timeout=10,
            )
        except requests.RequestException:
            pass

    def still_valid(self) -> bool:
        """False once Cognito refuses this sign-in (user disabled, tokens
        revoked, refresh token expired). Asks Cognito only when the ID token
        has expired — about hourly — so most requests cost nothing. An
        unreachable Cognito counts as valid: offline isn't signed out."""
        if self.signed_out:
            return False
        if time.time() < self._id_token_exp:
            return True
        try:
            self._current_id_token()
        except SignInRequired:
            return False
        except Exception:  # noqa: BLE001 — Cognito unreachable or odd reply
            pass
        return not self.signed_out

    def _current_id_token(self) -> str:
        if self.signed_out:
            raise SignInRequired("You signed out — sign in again.")
        if self._id_token_exp - time.time() < 300:
            try:
                tokens = _token_request({
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                })
            except SignInRequired:
                self.signed_out = True
                raise
            self._refresh_token = tokens.get("refresh_token", self._refresh_token)
            self._set_id_token(tokens["id_token"])
        return self._id_token

    def _fetch_credentials(self) -> dict:
        """botocore's refresh callback (it serialises calls with its own
        lock): identity pool credentials for this user's current ID token."""
        import botocore.session
        from botocore import UNSIGNED
        from botocore.config import Config as BotoConfig

        # GetId / GetCredentialsForIdentity are unauthenticated calls — the
        # ID token in Logins is the proof of identity.
        client = botocore.session.get_session().create_client(
            "cognito-identity",
            region_name=Config.AWS_REGION,
            config=BotoConfig(signature_version=UNSIGNED),
        )
        logins = {
            f"cognito-idp.{Config.AWS_REGION}.amazonaws.com/{Config.COGNITO_USER_POOL_ID}":
                self._current_id_token(),
        }
        if self._identity_id is None:
            self._identity_id = client.get_id(
                IdentityPoolId=Config.COGNITO_IDENTITY_POOL_ID, Logins=logins,
            )["IdentityId"]
        creds = client.get_credentials_for_identity(
            IdentityId=self._identity_id, Logins=logins,
        )["Credentials"]
        return {
            "access_key": creds["AccessKeyId"],
            "secret_key": creds["SecretKey"],
            "token": creds["SessionToken"],
            "expiry_time": creds["Expiration"].isoformat(),
        }


_users: dict[str, User] = {}


def current_user() -> User | None:
    """The signed-in user for this request, or None."""
    sid = session.get("sid")
    user = _users.get(sid) if sid else None
    if user is not None and not user.still_valid():
        _users.pop(sid, None)
        return None
    return user


# The user whose run holds the run slot (runs execute one at a time — see
# biomni_runner._run_lock). Module-level rather than thread-local because
# biomni may call the model from threads it starts itself.
_run_user: User | None = None


def set_run_user(user: User | None) -> None:
    global _run_user
    _run_user = user


def run_credentials():
    """Frozen AWS credentials for the current run's user, refreshed first if
    they're near expiry."""
    user = _run_user
    if user is None:
        raise SignInRequired("No signed-in user for this run — sign in and start it again.")
    if user.signed_out:
        raise SignInRequired("You signed out, which stopped this run — sign in and start it again.")
    return user.credentials.get_frozen_credentials()


def _origin() -> str:
    parts = urlsplit(Config.COGNITO_REDIRECT_URI)
    return f"{parts.scheme}://{parts.netloc}"


@bp.get("/login")
def login():
    # The session cookie is per host: start on the callback's host, so the
    # state and PKCE verifier are there when Cognito redirects back.
    if request.host_url.rstrip("/") != _origin():
        return redirect(f"{_origin()}/login")
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(24)
    session["oauth"] = {"state": state, "verifier": verifier}
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return redirect(f"{Config.COGNITO_DOMAIN}/oauth2/authorize?" + urlencode({
        "response_type": "code",
        "client_id": Config.COGNITO_CLIENT_ID,
        "redirect_uri": Config.COGNITO_REDIRECT_URI,
        "scope": "openid email",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }))


@bp.get("/callback")
def callback():
    pending = session.pop("oauth", None)
    if request.args.get("error"):
        abort(400, description="Cognito sign-in failed: "
              + (request.args.get("error_description") or request.args["error"]))
    if not pending or not secrets.compare_digest(request.args.get("state", ""), pending["state"]):
        abort(400, description="Sign-in state mismatch — start again from /login.")
    try:
        user = User(_token_request({
            "grant_type": "authorization_code",
            "code": request.args.get("code", ""),
            "redirect_uri": Config.COGNITO_REDIRECT_URI,
            "code_verifier": pending["verifier"],
        }))
    except Exception as exc:  # noqa: BLE001 — show the cause, not a bare 500
        abort(502, description=f"Signed in, but getting AWS credentials failed: {exc}")
    sid = secrets.token_urlsafe(32)
    _users[sid] = user
    session.clear()
    session["sid"] = sid
    return redirect("/")


@bp.get("/logout")
def logout():
    user = _users.pop(session.get("sid"), None)
    if user is not None:
        user.sign_out()
    session.clear()
    # Also ends the hosted-login session; logout_uri must be one of the app
    # client's "Allowed sign-out URLs".
    return redirect(f"{Config.COGNITO_DOMAIN}/logout?" + urlencode({
        "client_id": Config.COGNITO_CLIENT_ID,
        "logout_uri": f"{_origin()}/",
    }))
