"""
ASGI config for sftp_manager project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/howto/deployment/asgi/
"""

# sftp_manager/wsgi.py 补充
import os
from django.core.wsgi import get_wsgi_application
from sftp_web.views import start_scheduler

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sftp_manager.settings')

application = get_wsgi_application()

# 启动调度器（仅在WSGI启动时执行一次）
start_scheduler()
