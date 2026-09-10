from __future__ import annotations

from flask import Flask, render_template

from .config import Config


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    from .api.routes import bp as api_bp

    app.register_blueprint(api_bp, url_prefix="/api")

    @app.get("/")
    def index():
        return render_template("index.html", provider=Config.PROVIDER)

    @app.get("/about")
    def about():
        return render_template("about.html", provider=Config.PROVIDER)

    return app
