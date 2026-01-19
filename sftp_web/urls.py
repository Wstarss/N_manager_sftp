# sftp_web/urls.py 正确内容
from django.urls import path
from . import views

urlpatterns = [
    path('', views.sftp_manager, name='sftp_manager'),
    path('api/get_lease_info/', views.api_get_lease_info, name='api_get_lease_info'),
    path('api/manual_lease_check/', views.api_manual_lease_check, name='api_manual_lease_check'),
]