from __future__ import annotations

import logging

from flask import Flask, render_template, request

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

    # Warm the model cache at startup so the first page load already has a
    # discovered list (no-op if the on-disk cache is still fresh).
    if Config.PROVIDER.strip().lower() == "anthropic":
        refresh_models_in_background()

    @app.get("/")
    def index():
        return render_template(
            "index.html", provider=Config.PROVIDER, models=models_payload(),
        )

    @app.get("/about")
    def about():
        return render_template("about.html", provider=Config.PROVIDER)

    return app
