import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env():
    """Крошечный загрузчик .env (без внешних пакетов)."""
    env = BASE_DIR / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-on-server")
DEBUG = os.environ.get("DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = ["agent"]
MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": False,
    "OPTIONS": {"context_processors": []},
}]

# Django-базы не нужны: данные лежат в отдельном SQLite-файле, который открывается только на чтение.
DATABASES = {}

LANGUAGE_CODE = "ru"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = False
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"agent.service": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}
