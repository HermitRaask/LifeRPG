"""Single-user local web application settings."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("LIFERPG_WEB_DATA", BASE_DIR / "data" / "web"))
SECRET_KEY = os.environ.get(
    "LIFERPG_SECRET_KEY", "local-only-liferpg-do-not-expose-to-network"
)
DEBUG = os.environ.get("LIFERPG_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]
ROOT_URLCONF = "webconfig.urls"
WSGI_APPLICATION = "webconfig.wsgi.application"
INSTALLED_APPS = [
    "django.contrib.contenttypes", "django.contrib.sessions",
    "django.contrib.messages", "django.contrib.staticfiles", "webgame",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": os.environ.get("LIFERPG_WEB_DB", BASE_DIR / "db.sqlite3"),
    "OPTIONS": {"timeout": 20},
}}
LANGUAGE_CODE = "ru"
TIME_ZONE = "Europe/Moscow"
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
