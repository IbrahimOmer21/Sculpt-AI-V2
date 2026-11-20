import logging
import os

from flask import Flask, jsonify, redirect, request, url_for
from flask_login import LoginManager
from werkzeug.exceptions import HTTPException

from app.config import Config
from app.db import db
from app.services.meshy_service import MeshyError

login_manager = LoginManager()


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config.update(overrides or {})
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY is not set. Add it to your .env file (see .env.example).")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import User
    from app.routes.auth_routes import auth_bp
    from app.routes.generation_routes import generation_bp
    from app.routes.iteration_routes import iteration_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(generation_bp)
    app.register_blueprint(iteration_bp)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @login_manager.unauthorized_handler
    def unauthorized():
        if is_api_request():
            return jsonify(error="Please log in first."), 401
        return redirect(url_for("auth.login"))

    @app.before_request
    def json_only_api():
        # Second CSRF guard on top of SameSite=Lax: plain HTML forms on other sites can't send JSON.
        # The upload route takes multipart form data, so it relies on SameSite alone.
        if (request.path.startswith("/api/") and request.method in ("POST", "PATCH", "DELETE")
                and not request.path.endswith("/upload") and not request.is_json):
            return jsonify(error="Requests must be sent as JSON."), 415

    @app.errorhandler(MeshyError)
    def meshy_failed(e):
        app.logger.error("Meshy failure on %s %s: %s", request.method, request.path, e)
        return jsonify(error="The AI service failed or took too long. Please try again."), 502

    @app.errorhandler(HTTPException)
    def http_error(e):
        if not is_api_request():
            return e
        if e.code == 413:
            return jsonify(error="That file is too big (max 10 MB)."), 413
        return jsonify(error=e.description), e.code

    @app.errorhandler(Exception)
    def server_error(e):
        app.logger.exception("Server error on %s %s", request.method, request.path)
        if is_api_request():
            return jsonify(error="Something went wrong on the server."), 500
        return "Something went wrong on the server.", 500

    with app.app_context():
        db.create_all()

    return app


def is_api_request():
    return request.path.startswith(("/api/", "/files/"))
