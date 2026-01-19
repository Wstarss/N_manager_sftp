"""
Django应用启动时初始化APScheduler
"""
import os
from django.conf import settings
from django.apps import apps

def init_scheduler():
    """初始化并启动APScheduler"""
    if os.environ.get('RUN_MAIN', None) != 'true':  # 避免在Django重载时重复启动
        from .scheduler import start
        start()