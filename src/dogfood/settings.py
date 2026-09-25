"""Django settings for the DOGFOOD judging portal.

Single container, SQLite, no external services. Everything the portal
needs to boot is in the built image, so `docker compose up` works with
the network off.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# A fixed dev key keeps the offline demo reproducible. Override with the
# DJANGO_SECRET_KEY env var for any real deployment.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-only-key-dogfood-2026-not-for-production",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"

# Offline single host. The portal only ever answers on localhost in the
# demo, so a permissive list is safe here and documented as dev posture.
ALLOWED_HOSTS = os.environ.get(
    "DJANGO_ALLOWED_HOSTS", "*"
).split(",")

CSRF_TRUSTED_ORIGINS = [
    o for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "dogfood.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "dogfood.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_DB_PATH", str(BASE_DIR / "db.sqlite3")),
    }
}

AUTH_USER_MODEL = "core.User"

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Ed25519 signing key for results bundles. The deployment generates it on first boot
# and keeps it in a mounted volume, never a personal or shared key. Override the path
# per deployment. Default sits under /data, the same volume as the SQLite file.
SIGNING_KEY_PATH = os.environ.get("DOGFOOD_SIGNING_KEY_PATH", "/data/signing_key.pem")

# The code commit baked into the image at build time, named in every signed bundle so
# a result points at the exact code that produced it.
GIT_COMMIT = os.environ.get("GIT_COMMIT", "unknown")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Write endpoints that a visitor can reach (voting, comments) are rate
    # limited by scope. The scopes are attached per view, not globally, so the
    # judge and organizer routes stay unthrottled. Keyed by account for a
    # logged-in caller and by client address for an anonymous one.
    "DEFAULT_THROTTLE_RATES": {
        "vote": "30/min",
        "comment": "20/min",
    },
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "DOGFOOD judging portal API",
    "DESCRIPTION": (
        "Public, backend-enforced hackathon submission and judging API. Role "
        "isolation is enforced in the backend, so a curl as the wrong role is "
        "refused with 401 or 403 before any view code runs. Results are "
        "published as an Ed25519-signed, content-addressed bundle that anyone "
        "can recompute and verify offline."
    ),
    "VERSION": "1.0.0",
    "LICENSE": {"name": "MIT", "url": "https://opensource.org/license/mit"},
    "SERVE_INCLUDE_SCHEMA": False,
    "PREPROCESSING_HOOKS": ["core.schema.dedupe_trailing_slash"],
    "TAGS": [
        {"name": "Gallery", "description": "Public project gallery and submission."},
        {"name": "Judging", "description": "Judge-only scores with per-judge isolation."},
        {"name": "Results", "description": "Signed, reproducible results bundles."},
        {"name": "Verification", "description": "The public key that signs every bundle."},
        {"name": "Export", "description": "Organizer-only weighted CSV export."},
    ],
}
