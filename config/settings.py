import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent


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


def env_frame_ancestors(name, default):
    value = os.getenv(name)
    if value is None:
        value = default
    return [
        item.strip()
        for chunk in value.split(",")
        for item in chunk.split()
        if item.strip()
    ]


def default_cookie_samesite(debug):
    return "Lax" if debug else "None"


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
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
    "course_iframe.middleware.ContentSecurityPolicyMiddleware",
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
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username or "",
        "PASSWORD": parsed.password or "",
        "HOST": parsed.hostname or "",
        "PORT": parsed.port or "",
    }
    if env_bool("DJANGO_DB_POOL", not env_bool("DJANGO_DEBUG", True)):
        config["OPTIONS"] = {
            "pool": {
                "min_size": int(os.getenv("DJANGO_DB_POOL_MIN", "2")),
                "max_size": int(os.getenv("DJANGO_DB_POOL_MAX", "8")),
                "timeout": float(os.getenv("DJANGO_DB_POOL_TIMEOUT", "10")),
            }
        }
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

# Allow embedding inside Moodle via CSP frame-ancestors. Modern browsers prefer
# this CSP directive over X-Frame-Options, so configure the Moodle/LMS parent
# origins with DJANGO_CSP_FRAME_ANCESTORS.
REQUIRE_CSP_FRAME_ANCESTORS = env_bool("DJANGO_REQUIRE_CSP_FRAME_ANCESTORS", not DEBUG)
if REQUIRE_CSP_FRAME_ANCESTORS and "DJANGO_CSP_FRAME_ANCESTORS" not in os.environ:
    raise ImproperlyConfigured(
        "Set DJANGO_CSP_FRAME_ANCESTORS to the Moodle/LMS origin before running production."
    )
CSP_FRAME_ANCESTORS = env_frame_ancestors(
    "DJANGO_CSP_FRAME_ANCESTORS",
    "'self'",
)
X_FRAME_OPTIONS = "SAMEORIGIN"  # Django default; CSP frame-ancestors overrides in modern browsers

IFRAME_SIGNING_SECRET = os.getenv("IFRAME_SIGNING_SECRET", "dev-iframe-secret")
# Either a plaintext password (dev convenience) or a hashed string produced by
# `manage.py hash_admin_password` (recommended for production). The login view
# detects which form is in use.
RESEARCH_ADMIN_PASSWORD = os.getenv("RESEARCH_ADMIN_PASSWORD", "dev-admin-password")
RESEARCH_ADMIN_PASSWORD_HASH = os.getenv("RESEARCH_ADMIN_PASSWORD_HASH", "")
if not DEBUG and not RESEARCH_ADMIN_PASSWORD_HASH:
    raise ImproperlyConfigured(
        "Set RESEARCH_ADMIN_PASSWORD_HASH in production. Generate it with manage.py hash_admin_password."
    )

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
