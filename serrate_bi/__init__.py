"""Serrate Paz & Asociados — Inteligencia comercial y prospección (construcción, inmobiliario, infraestructura)."""
import os

from flask import Flask, g

from .db import connect, default_db_path, init_db
from .jobs import JobRunner

__version__ = "1.0.0"


def create_app(db_path=None, start_jobs=True, testing=False):
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or default_db_path()
    app.config["TESTING"] = testing
    app.secret_key = os.environ.get("SERRATE_BI_SECRET", "serrate-bi-local")
    init_db(app.config["DB_PATH"])
    app.jobs = JobRunner(app.config["DB_PATH"])
    if start_jobs:
        app.jobs.start(scheduler=not testing)

    @app.before_request
    def _open_db():
        g.db = connect(app.config["DB_PATH"])

    @app.teardown_request
    def _close_db(exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    from .web import bp, register_filters
    register_filters(app)
    app.register_blueprint(bp)
    return app
