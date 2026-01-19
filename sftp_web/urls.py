from django.urls import path
from . import views

urlpatterns = [
    path('', views.sftp_manager, name='sftp_manager'),
]