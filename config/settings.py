"""Encelis settings. All secrets come from the environment (root .env in development)."""
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
VAR_DIR = BASE_DIR / "var"
VAR_DIR.mkdir(exist_ok=True)
load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    value = os.environ.get(name)
    return value.strip() if value is not None and value.strip() != "" else default


def env_bool(name, default=False):
    value = env(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


PRODUCTION = env("DJANGO_ENV", "development") == "production"
DEBUG = env_bool("DJANGO_DEBUG", not PRODUCTION)
TESTING = "test" in sys.argv

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if PRODUCTION:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in production.")
    key_file = VAR_DIR / "dev_secret_key"
    if not key_file.exists():
        key_file.write_text(get_random_secret_key())
    SECRET_KEY = key_file.read_text().strip()

ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.core",
    "apps.accounts",
    "apps.curriculum",
    "apps.questions",
    "apps.learning",
    "apps.reviews",
    "apps.assessments",
    "apps.ai",
    "apps.analytics",
    "apps.studio",
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
    "apps.core.htmx.HtmxMiddleware",
    "apps.core.middleware.LearnerGateMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context.ui",
            ],
        },
    }
]


def _database():
    url = env("DATABASE_URL")
    if not url:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": VAR_DIR / "db.sqlite3",
            "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": 20},
        }
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured("DATABASE_URL must be a postgres:// URL.")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or ""),
        "CONN_MAX_AGE": 60,
    }


DATABASES = {"default": _database()}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.EmailOrUsernameBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "learning:dashboard"
LOGOUT_REDIRECT_URL = "core:landing"
PASSWORD_RESET_TIMEOUT = 60 * 60 * 2
EMAIL_VERIFICATION_MAX_AGE = 60 * 60 * 48

LANGUAGE_CODE = "uz"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = VAR_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG or TESTING
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# --- Cache, Celery -----------------------------------------------------------
REDIS_URL = env("REDIS_URL")
if REDIS_URL and not TESTING:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

CELERY_BROKER_URL = REDIS_URL or "memory://"
CELERY_RESULT_BACKEND = REDIS_URL or "cache+memory://"
# Without Redis (local development, simple deployments) tasks run inline.
CELERY_TASK_ALWAYS_EAGER = not REDIS_URL or TESTING
CELERY_TASK_EAGER_PROPAGATES = TESTING
CELERY_TASK_TIME_LIMIT = 300
CELERY_TIMEZONE = TIME_ZONE

# --- Email (Resend) ----------------------------------------------------------
RESEND_API_KEY = env("RESEND_API_KEY")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "encelis <onboarding@resend.dev>")
if TESTING:
    EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
elif RESEND_API_KEY:
    EMAIL_BACKEND = "apps.core.email.ResendEmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# --- AI (DeepSeek) -----------------------------------------------------------
DEEPSEEK_API_KEY = env("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
AI_MODEL_FAST = "deepseek-flash"
AI_MODEL_DEEP = "deepseek-v4-pro"
AI_ENABLED = bool(DEEPSEEK_API_KEY) and not TESTING

# --- Security ----------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
if PRODUCTION:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": VAR_DIR / "encelis.log",
            "maxBytes": 2_000_000,
            "backupCount": 3,
            "formatter": "plain",
            "encoding": "utf-8",
        },
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "encelis": {"handlers": ["console"] if TESTING else ["console", "file"], "level": "CRITICAL" if TESTING else "INFO", "propagate": False},
        "django.security": {"handlers": ["console"] if TESTING else ["console", "file"], "level": "WARNING", "propagate": False},
    },
}
