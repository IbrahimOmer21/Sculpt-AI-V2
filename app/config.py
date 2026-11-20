import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Config:
    # Signs the login cookie. create_app refuses to start without it.
    SECRET_KEY = os.environ.get("SECRET_KEY")
    MESHY_API_KEY = os.environ.get("MESHY_API_KEY")
    USE_MOCK_AI = os.environ.get("USE_MOCK_AI") == "1"

    # A relative sqlite path is created inside the instance/ folder.
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///sculpt.db")

    # Generated files live outside static/ so they are only reachable through the owner-checked /files route.
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(PROJECT_ROOT, "storage"))
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # Flask answers bigger uploads with 413

    # SameSite=Lax: the browser won't send our cookies on cross-site POSTs (CSRF protection).
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_DURATION = timedelta(days=30)

    SCULPTURES_PER_DAY = 3
    ITERATIONS_PER_GENERATION = 3
