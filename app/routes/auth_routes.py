from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from app.db import db
from app.models import User

auth_bp = Blueprint("auth", __name__)


def auth_page(mode, error=None, status=200):
    return render_template("auth.html", mode=mode, error=error, email=request.form.get("email", "")), status


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("generation.index"))
    if request.method == "GET":
        return auth_page("signup")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if "@" not in email or len(email) > 255:
        return auth_page("signup", "Please enter a valid email address.", 400)
    if len(password) < 8:
        return auth_page("signup", "Your password must be at least 8 characters.", 400)
    if User.query.filter_by(email=email).first():
        return auth_page("signup", "An account with that email already exists. Try logging in.", 400)

    user = User(email=email)
    user.set_password(password)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:  # the same email signed up a moment ago (double submit)
        db.session.rollback()
        return auth_page("signup", "An account with that email already exists. Try logging in.", 400)
    login_user(user, remember=bool(request.form.get("remember")))
    return redirect(url_for("generation.index"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("generation.index"))
    if request.method == "GET":
        return auth_page("login")

    email = request.form.get("email", "").strip().lower()
    user = User.query.filter_by(email=email).first()
    # Same message for unknown email and wrong password, so nobody can probe which emails have accounts.
    if user is None or not user.check_password(request.form.get("password", "")):
        return auth_page("login", "Email or password is incorrect.", 401)

    login_user(user, remember=bool(request.form.get("remember")))
    return redirect(url_for("generation.index"))


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
