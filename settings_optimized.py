# ⚡ Configuration Django optimisée pour vitesse maximale
# Remplace le contenu actuel de octascraper/settings.py

from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-insecure-tjz4-7kpx0325vw!7#lmn5(v%247^*olqu!o16rgmni5pnuz3d'
DEBUG = True
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django.middleware.http.ConditionalGetMiddleware',  # ETags
]

ROOT_URLCONF = 'octascraper.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'octascraper.wsgi.application'

# ══════════════════════════════════════════════════════════
# DATABASE WITH CONNECTION POOLING
# ══════════════════════════════════════════════════════════
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('DB_NAME', 'octahub'),
        'USER': os.environ.get('DB_USER', 'root'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'octahub2003'),
        'HOST': os.environ.get('DB_HOST', 'db'),
        'PORT': os.environ.get('DB_PORT', '3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES', innodb_strict_mode=1",
            'isolation_level': 'read committed',
        },
        # ⚡ CONNECTION POOLING - Keep connections alive
        'CONN_MAX_AGE': 600,  # 10 minutes
        'CONN_HEALTH_CHECKS': True,
        'ATOMIC_REQUESTS': True,
    }
}

# ══════════════════════════════════════════════════════════
# REDIS CACHE CONFIGURATION
# ══════════════════════════════════════════════════════════
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://redis:6379/1',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'PARSER_CLASS': 'redis.connection.HiredisParser',
            'CONNECTION_POOL_KWARGS': {
                'max_connections': 50,
                'retry_on_timeout': True,
            },
            'SOCKET_CONNECT_TIMEOUT': 5,
            'SOCKET_TIMEOUT': 5,
            'COMPRESSOR': 'django_redis.compressors.zlib.ZlibCompressor',
            'IGNORE_EXCEPTIONS': True,
        },
        'KEY_PREFIX': 'octahub',
        'TIMEOUT': 300,
    }
}

# ⚡ Use Redis for sessions (10x faster than database)
SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_CACHE_ALIAS = 'default'
SESSION_COOKIE_AGE = 86400  # 24 hours

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ══════════════════════════════════════════════════════════
# STATIC FILES WITH COMPRESSION
# ══════════════════════════════════════════════════════════
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = []

# ⚡ Whitenoise with compression and caching
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
WHITENOISE_COMPRESS_OFFLINE = True
WHITENOISE_COMPRESS_OFFLINE_MANIFEST = 'staticfiles.json'
WHITENOISE_MAX_AGE = 31536000  # 1 year
WHITENOISE_ALLOW_ALL_ORIGINS = False

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ══════════════════════════════════════════════════════════
# SECURITY SETTINGS
# ══════════════════════════════════════════════════════════
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'index'
LOGOUT_REDIRECT_URL = 'landing'

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
# SESSION_COOKIE_SECURE = True  # Uncomment in production with HTTPS
# CSRF_COOKIE_SECURE = True     # Uncomment in production with HTTPS

# ══════════════════════════════════════════════════════════
# PERFORMANCE SETTINGS
# ══════════════════════════════════════════════════════════
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ⚡ Disable unnecessary features in production
if not DEBUG:
    LOGGING = {
        'version': 1,
        'disable_existing_loggers': False,
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
            },
        },
        'root': {
            'handlers': ['console'],
            'level': 'WARNING',
        },
    }
