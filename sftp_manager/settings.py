import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'your-secret-key-here'  # 生产环境务必修改
DEBUG = True  # 生产环境设为False
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django_apscheduler',
    'sftp_web',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
]

ROOT_URLCONF = 'sftp_manager.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
            ],
        },
    },
]
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.163.com'        # 邮件服务器
EMAIL_PORT = 465                   # SSL端口
EMAIL_USE_SSL = True
EMAIL_HOST_USER = 'your_email@163.com'  # 发件人邮箱
EMAIL_HOST_PASSWORD = 'your_auth_code'  # 邮箱授权码
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER
CRONJOBS = [
    # 每天10:30执行检查任务（可前端配置时间）
    ('30 10 * * *', 'sftp_app.tasks.check_sftp_users', '>> sftp_cron.log 2>&1')
]
WSGI_APPLICATION = 'sftp_manager.wsgi.application'

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_L10N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': os.path.join(BASE_DIR, 'logs/sftp_lease.log'),
            'formatter': 'verbose',
        },
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'sftp_web': {  # 对应 views.py 中的 logger = logging.getLogger(__name__)
            'handlers': ['file', 'console'],
            'level': 'INFO',
            'propagate': True,
        },
    },
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
}