import json
import logging
import os
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent
DEV_SECRET_KEY = "dev-insecure-change-me"


def env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default):
    value = os.getenv(name)
    if value is None:
        value = default
    return [item.strip() for item in value.split(",") if item.strip()]


def default_cookie_samesite(debug):
    return "Lax" if debug else "None"


def require_production_secret(name, value, dev_default):
    if DEBUG:
        return
    if not value or value == dev_default:
        raise ImproperlyConfigured(
            f"Set {name} to a unique secret before running production."
        )


def validate_production_password_hash(value):
    """Catch copied/truncated researcher password hashes during startup."""
    if DEBUG:
        return
    parts = (value or "").split("$")
    if (
        len(parts) != 4
        or parts[0] != "pbkdf2_sha256"
        or not parts[1].isdigit()
        or not parts[2]
        or not parts[3]
    ):
        raise ImproperlyConfigured(
            "RESEARCH_ADMIN_PASSWORD_HASH must be a complete pbkdf2_sha256 hash "
            "from manage.py hash_admin_password."
        )


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", DEV_SECRET_KEY)
DEBUG = env_bool("DJANGO_DEBUG", True)
require_production_secret("DJANGO_SECRET_KEY", SECRET_KEY, DEV_SECRET_KEY)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "course_iframe",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


def database_config():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }

    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL must use postgres:// or postgresql://")

    config = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": parsed.port or "",
    }
    options = {
        key: value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    }
    if env_bool("DJANGO_DB_POOL", not env_bool("DJANGO_DEBUG", True)):
        options["pool"] = {
            "min_size": int(os.getenv("DJANGO_DB_POOL_MIN", "2")),
            "max_size": int(os.getenv("DJANGO_DB_POOL_MAX", "8")),
            "timeout": float(os.getenv("DJANGO_DB_POOL_TIMEOUT", "10")),
        }
    if options:
        config["OPTIONS"] = options
    return config


DATABASES = {"default": database_config()}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/London"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

COOKIE_SAMESITE = os.getenv("DJANGO_COOKIE_SAMESITE", default_cookie_samesite(DEBUG))
COOKIE_SECURE = env_bool("DJANGO_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SAMESITE = COOKIE_SAMESITE
CSRF_COOKIE_SECURE = COOKIE_SECURE
SESSION_COOKIE_SAMESITE = COOKIE_SAMESITE
SESSION_COOKIE_SECURE = COOKIE_SECURE
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Security headers — only enforced when not in DEBUG so local HTTP keeps working.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
if not DEBUG:
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", True)
    SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", False)  # Caddy already redirects

# Course activities may be embedded by any LMS domain, including after migrations.
# Deliberately omit both frame-ancestors and X-Frame-Options restrictions.
SILENCED_SYSTEM_CHECKS = ["security.W002"]

# Either a plaintext password (dev convenience) or a hashed string produced by
# `manage.py hash_admin_password` (recommended for production). The login view
# detects which form is in use.
RESEARCH_ADMIN_PASSWORD = os.getenv("RESEARCH_ADMIN_PASSWORD", "dev-admin-password")
RESEARCH_ADMIN_PASSWORD_HASH = os.getenv("RESEARCH_ADMIN_PASSWORD_HASH", "")
if not DEBUG and not RESEARCH_ADMIN_PASSWORD_HASH:
    raise ImproperlyConfigured(
        "Set RESEARCH_ADMIN_PASSWORD_HASH in production. Generate it with manage.py hash_admin_password."
    )
validate_production_password_hash(RESEARCH_ADMIN_PASSWORD_HASH)

NOMINATIM_BASE_URL = os.getenv("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org")
NOMINATIM_USER_AGENT = os.getenv(
    "NOMINATIM_USER_AGENT",
    "elearning-iframe/1.0 (housing data entry)",
)
NOMINATIM_VERIFY = env_bool("NOMINATIM_VERIFY", True)
NOMINATIM_CACHE_SECONDS = int(os.getenv("NOMINATIM_CACHE_SECONDS", "604800"))  # 7 days


# ─── Logging ──────────────────────────────────────────────────────────────────
class JsonLogFormatter(logging.Formatter):
    """Compact JSON log lines: stdout-friendly, parseable by any log collector."""

    def format(self, record):
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key in (
            "event",
            "participant_id",
            "activity",
            "actor",
            "row_count",
            "from_version",
            "to_version",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, default=str)


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "config.settings.JsonLogFormatter"},
        "plain": {"format": "%(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "plain" if DEBUG else "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.getenv("DJANGO_LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django.server": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "course_iframe": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}


# ─── Sentry (optional, opt-in via env) ───────────────────────────────────────
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[DjangoIntegration()],
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
            send_default_pii=False,
            environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        )
    except ImportError:
        # sentry-sdk not installed; keep running without it.
        pass

# Repeatable sections submit many typed fields in one activity response.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 50000
