import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'your-secret-key-here'  # 生产环境务必修改
DEBUG = True  # 生产环境设为False
ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'sftp_web',
    'django_apscheduler',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',  # Admin必需
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',  # Admin必需
    'django.contrib.messages.middleware.MessageMiddleware',  # Admin必需
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'sftp_manager.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],  # 确保模板目录配置正确
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',  # Admin必需
                'django.contrib.messages.context_processors.messages',  # Admin必需
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
# 自动创建日志目录（核心修复）
LOG_DIR = os.path.join(BASE_DIR, 'logs')
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR, exist_ok=True)

# 日志配置（替换原有 LOGGING 配置）
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose'
        },
        # 修复后的文件日志处理器
        'file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',  # 改用轮转日志处理器
            'filename': os.path.join(LOG_DIR, 'sftp_lease.log'),
            'maxBytes': 10485760,  # 10MB
            'backupCount': 5,  # 保留5个备份
            'formatter': 'verbose',
            'encoding': 'utf-8',  # 指定编码，避免中文乱码
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': True,
        },
        'sftp_web': {  # 你的 SFTP 应用日志
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,  # 不向上传递，避免重复日志
        },
    },
}