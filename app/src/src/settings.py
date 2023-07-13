import logging
from pathlib import Path

import environ
import sentry_sdk
from django.urls import reverse_lazy
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from functools import wraps
import inspect

root = environ.Path(__file__) - 2

env = environ.Env(DEBUG=(bool, False))

# .env file contents are not passed to docker image during build stage;
# this results in errors if you require some env var to be set, as if in "env('MYVAR')" -
# obviously it's not set during build stage, but you don't care and want to ignore that.
# To mitigate this, we set ENV_FILL_MISSING_VALUES=1 during build phase, and it activates
# monkey-patching of "environ" module, so that all unset variables are set to None and
# the library is not complaining anymore
if env.bool('ENV_FILL_MISSING_VALUES', default=False):

    def patch(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if kwargs.get('default') is env.NOTSET:
                kwargs['default'] = None
            return fn(*args, **kwargs)
        return wrapped

    for name, method in inspect.getmembers(env, predicate=inspect.ismethod):
        setattr(env, name, patch(method))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env('DEBUG')

ALLOWED_HOSTS = ['*']


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'django_extensions',

    'src.core',

]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# Content Security Policy
CSP_ENABLED = env.bool('CSP_ENABLED')
if CSP_ENABLED:
    MIDDLEWARE.append('csp.middleware.CSPMiddleware')

    CSP_REPORT_ONLY = env.bool('CSP_REPORT_ONLY', default=True)
    CSP_REPORT_URL = env('CSP_REPORT_URL', default=None) or None

    CSP_DEFAULT_SRC = env.tuple('CSP_DEFAULT_SRC')
    CSP_SCRIPT_SRC = env.tuple('CSP_SCRIPT_SRC')
    CSP_STYLE_SRC = env.tuple('CSP_STYLE_SRC')
    CSP_FONT_SRC = env.tuple('CSP_FONT_SRC')
    CSP_IMG_SRC = env.tuple('CSP_IMG_SRC')
    CSP_MEDIA_SRC = env.tuple('CSP_MEDIA_SRC')
    CSP_OBJECT_SRC = env.tuple('CSP_OBJECT_SRC')
    CSP_FRAME_SRC = env.tuple('CSP_FRAME_SRC')
    CSP_CONNECT_SRC = env.tuple('CSP_CONNECT_SRC')
    CSP_CHILD_SRC = env.tuple('CSP_CHILD_SRC')
    CSP_MANIFEST_SRC = env.tuple('CSP_MANIFEST_SRC')
    CSP_WORKER_SRC = env.tuple('CSP_WORKER_SRC')

    CSP_BLOCK_ALL_MIXED_CONTENT = env.bool('CSP_BLOCK_ALL_MIXED_CONTENT', default=False)
    CSP_EXCLUDE_URL_PREFIXES = env.tuple('CSP_EXCLUDE_URL_PREFIXES', default=tuple())


ROOT_URLCONF = 'src.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [root('src/templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

LOGIN_URL = reverse_lazy('login')
LOGIN_REDIRECT_URL = reverse_lazy('main')

WSGI_APPLICATION = 'src.wsgi.application'

# Database

DATABASES = {
    'default': env.db(),
}

# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

API_KEY = env('API_KEY', default='')


# Internationalization

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)

STATIC_URL = env('STATIC_URL', default='/static/')

STATIC_ROOT = env('STATIC_ROOT', default=root('static'))

MEDIA_URL = env('MEDIA_URL', default='/media/')

MEDIA_ROOT = env('MEDIA_ROOT', default=root('media'))

# redirect HTTP to HTTPS
if env.bool('HTTPS_REDIRECT', default=False) and not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_REDIRECT_EXEMPT = []
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
else:
    SECURE_SSL_REDIRECT = False

# trust the given (by default "X-Scheme") header that comes from our proxy (nginx),
# and any time its value is "https",
# then the request is guaranteed to be secure (i.e., it originally came in via HTTPS).
HTTPS_PROXY_HEADER = 'X_SCHEME'
if HTTPS_PROXY_HEADER and not DEBUG:
    SECURE_PROXY_SSL_HEADER = (f'HTTP_{HTTPS_PROXY_HEADER}', 'https')
else:
    SECURE_PROXY_SSL_HEADER = None

# how many failed attempts it is allowed to do before we block this user
HUBSTAFF_MAX_FAILED_BEFORE_BLOCK = 3
SWAGGER_FILE_PATH = Path(__file__).parent / 'core' / 'data' / 'hubstaff.v2.swagger.json'
HUBSTAFF_REFRESH_TOKEN = env('HUBSTAFF_REFRESH_TOKEN', default='')
SUPPORT_EMAIL = env('SUPPORT_EMAIL', default='')

JIRA_API_URL = env('JIRA_API_URL')
JIRA_API_AUTH_EMAIL = env('JIRA_API_AUTH_EMAIL')
JIRA_API_AUTH_TOKEN = env('JIRA_API_AUTH_TOKEN')
JIRA_HUBSTAFF_BOT_SUBMISSION_ISSUE_TYPE = env('JIRA_HUBSTAFF_BOT_SUBMISSION_ISSUE_TYPE')
JIRA_HUBSTAFF_BOT_SUBMISSION_CANDIDATE_EMAIL_CF = env('JIRA_HUBSTAFF_BOT_SUBMISSION_CANDIDATE_EMAIL_CF')
JIRA_PROJECT_KEY = env('JIRA_PROJECT_KEY')

DATA_UPLOAD_MAX_MEMORY_SIZE = 30 * 1024 * 1024 # 30MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 30 * 1024 * 1024 # 30MB

if env('SENTRY_DSN', default=''):
    sentry_logging = LoggingIntegration(
        level=logging.INFO,  # Capture info and above as breadcrumbs
        event_level=logging.ERROR     # Send error events from log messages
    )

    sentry_sdk.init(
        dsn=env('SENTRY_DSN', default=''),
        integrations=[DjangoIntegration(), sentry_logging]
    )

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose'
        },
    },
    'formatters': {
        'verbose': {
            'format': '\t'.join(['%(levelname).1s', '%(asctime)s', '%(process)d', '%(message)s']),
        }
    },
    'loggers': {
        '': {
            'handlers': ['console'],
            'level': 'DEBUG',
            # 'propagate': False #this will do the trick
        },
    }
}
