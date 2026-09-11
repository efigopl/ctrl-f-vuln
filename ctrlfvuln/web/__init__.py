"""Flask application factory."""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

from flask import Flask, jsonify, render_template, request

from .. import logging_setup
from ..config import PROJECT_ROOT, Settings
from ..config import settings as default_settings
from ..db import STARS_UNAVAILABLE, STARS_UNKNOWN, initialize_database

log = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or default_settings
    logging_setup.configure(settings.log_level)

    app = Flask(
        __name__,
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    app.config["SETTINGS"] = settings
    # Only used to sign flash messages; a per-process key is acceptable.
    app.secret_key = settings.secret_key or os.urandom(32)
    app.config.update(
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_HTTPONLY=True,
        JSON_SORT_KEYS=False,
    )

    initialize_database(settings.db_path)

    from .api import api
    from .views import pages

    app.register_blueprint(pages)
    app.register_blueprint(api, url_prefix="/api")

    @app.before_request
    def _reject_cross_origin_writes():
        """Block cross-site state changes.

        The viewer binds to localhost and can clone repositories and launch an
        editor, so a page in another tab must not be able to POST to it.
        """
        if request.method in SAFE_METHODS:
            return None
        origin = request.headers.get("Origin") or request.headers.get("Referer")
        if not origin:
            return None  # Non-browser client (tests, curl).
        if urlsplit(origin).netloc != request.host:
            log.warning("Rejected cross-origin %s from %r", request.method, origin)
            return ("Cross-origin request refused", 403)
        return None

    @app.errorhandler(404)
    def _not_found(error):
        if request.path.startswith("/api/"):
            return jsonify(error="Not found"), 404
        return render_template(
            "error.html",
            code=404,
            title="Not found",
            message="That page or record does not exist.",
        ), 404

    @app.errorhandler(500)
    def _server_error(error):  # pragma: no cover - exercised manually
        if request.path.startswith("/api/"):
            return jsonify(error="Internal server error"), 500
        return render_template(
            "error.html",
            code=500,
            title="Something broke",
            message="The server hit an unexpected error. Check the console output.",
        ), 500

    @app.template_filter("stars")
    def _stars(value) -> str:
        """Render a star count, distinguishing 'not fetched' from 'gone'."""
        if value is None or value == STARS_UNKNOWN:
            return "—"
        if value == STARS_UNAVAILABLE:
            return "n/a"
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return "—"

    @app.context_processor
    def _template_globals():
        return {
            "editor_available": bool(settings.editor_command),
            "editor_command": settings.editor_command,
            "token_available": bool(settings.github_token),
        }

    return app
