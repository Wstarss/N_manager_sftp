# sftp_web/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.sftp_manager, name='sftp_manager'),
]

# 项目根URL（sftp_manager/urls.py）
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('sftp/', include('sftp_web.urls')),  # SFTP管理页面入口
]