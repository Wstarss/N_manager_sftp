# sftp_manager/urls.py
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    # admin 路由放在项目根URL配置中
    path('admin/', admin.site.urls),
    # SFTP应用路由
    path('', include('sftp_web.urls')),
]