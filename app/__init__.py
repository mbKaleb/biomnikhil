from __future__ import annotations

import logging
import os
import secrets

from flask import Flask, abort, redirect, render_template, request

from .config import Config

_access_log = logging.getLogger("biomnikhil.access")


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    # waitress (used by run-mac.sh / run.ps1) doesn't print per-request
    # access lines the way Flask's dev server does — without this, the
    # server looks silent/hung for every successful request even though
    # it's working fine. Mirrors the "127.0.0.1 - - [date] "GET ..." 200"
    # format everyone's used to seeing.
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    # The agent executes arbitrary Python, so the API is a remote-code-
    # execution endpoint. Listening on 127.0.0.1 alone doesn't protect it:
    #  - DNS rebinding lets any website point its own hostname at
    #    127.0.0.1 and call the API same-origin — so reject any Host that
    #    isn't a loopback name (extend via BIOMNI_ALLOWED_HOSTS if you
    #    deliberately expose it on a LAN name);
    #  - cross-site forms and sandboxed agent-written HTML can still send
    #    "simple" POSTs — so every state-changing request must carry a
    #    per-process token that only this app's own page knows (embedded in
    #    index.html, added to every fetch by app.js). Custom headers also
    #    force a CORS preflight, which this app never approves.
    allowed_hosts = {"127.0.0.1", "localhost", "::1"} | {
        h.strip().lower()
        for h in os.getenv("BIOMNI_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    }
    app.config["CSRF_TOKEN"] = secrets.token_urlsafe(32)
    # Per-process, like the CSRF token: sign-ins live in memory (app/auth.py)
    # and end with the process anyway.
    app.secret_key = secrets.token_hex(32)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    from . import auth

    @app.before_request
    def _guard_request():
        hostname = (request.host or "").rsplit(":", 1)[0].strip("[]").lower()
        if hostname not in allowed_hosts:
            abort(403, description=f"Host '{request.host}' is not allowed.")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            sent = request.headers.get("X-Biomnikhil-Token", "")
            if not secrets.compare_digest(sent, app.config["CSRF_TOKEN"]):
                abort(403, description="Missing or invalid request token.")
        if (auth.enabled() and request.endpoint not in auth.PUBLIC_ENDPOINTS
                and auth.current_user() is None):
            if request.path.startswith("/api/"):
                abort(401, description="Sign in required.")
            return redirect("/login")

    @app.after_request
    def _log_request(response):
        _access_log.info(
            '%s - - "%s %s %s" %s',
            request.remote_addr, request.method, request.full_path.rstrip("?"),
            request.environ.get("SERVER_PROTOCOL", "HTTP/1.1"), response.status_code,
        )
        return response

    from .api.routes import bp as api_bp, models_payload
    from .providers import refresh_models_in_background

    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(auth.bp)

    # Warm the model cache at startup so the first page load already has a
    # discovered list (no-op if the on-disk cache is still fresh).
    refresh_models_in_background(Config.PROVIDER)

    @app.get("/")
    def index():
        return render_template(
            "index.html", provider=Config.PROVIDER, models=models_payload(),
            csrf_token=app.config["CSRF_TOKEN"], user=auth.current_user(),
        )

    @app.get("/about")
    def about():
        return render_template("about.html", provider=Config.PROVIDER)

    return app
