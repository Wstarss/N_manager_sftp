# sftp_web/models.py
from django.db import models
from django.utils import timezone
from datetime import timedelta

class SFTPLeaseSettings(models.Model):
    """全局租期设置"""
    enabled = models.BooleanField(default=True, verbose_name="启用租期管理")
    default_notice_days = models.IntegerField(default=7, verbose_name="默认提醒天数")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "租期全局设置"
        verbose_name_plural = "租期全局设置"

    def __str__(self):
        return f"租期设置（启用：{self.enabled}，提醒天数：{self.default_notice_days}）"

class SFTPAccount(models.Model):
    """SFTP账户信息"""
    username = models.CharField(max_length=100, unique=True, verbose_name="用户名")
    manager = models.CharField(max_length=100, verbose_name="管理员")
    email = models.EmailField(blank=True, null=True, verbose_name="邮箱")
    is_internal = models.BooleanField(default=False, verbose_name="是否内部用户")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "SFTP账户"
        verbose_name_plural = "SFTP账户"

    def __str__(self):
        return self.username

class DirectoryLease(models.Model):
    """目录租期信息"""
    username = models.CharField(max_length=100, unique=True, verbose_name="关联用户名")
    manager = models.CharField(max_length=100, verbose_name="管理员")
    start_date = models.DateTimeField(default=timezone.now, verbose_name="开始日期")
    end_date = models.DateTimeField(verbose_name="结束日期")
    is_active = models.BooleanField(default=True, verbose_name="是否有效")
    notice_sent = models.BooleanField(default=False, verbose_name="是否发送提醒")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "目录租期"
        verbose_name_plural = "目录租期"

    def __str__(self):
        return f"{self.username} - {self.end_date.strftime('%Y-%m-%d')}"

    def days_remaining(self):
        """计算剩余天数"""
        if not self.is_active or self.end_date < timezone.now():
            return 0
        delta = self.end_date - timezone.now()
        return delta.days