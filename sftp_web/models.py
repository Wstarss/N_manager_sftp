from django.db import models
from django.utils import timezone
from django.conf import settings


class SFTPAccount(models.Model):
    """SFTP账户管理"""
    ACCOUNT_TYPE_CHOICES = [
        ('internal', '内部用户'),
        ('external', '外部目录'),
    ]

    username = models.CharField(max_length=100, unique=True, help_text="SFTP用户名")
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES, help_text="账户类型")
    manager = models.CharField(max_length=100, blank=True, null=True, help_text="对接管理员(仅外部目录需要)")
    email = models.EmailField(blank=True, null=True, help_text="联系邮箱")
    created_at = models.DateTimeField(auto_now_add=True, help_text="创建时间")
    created_by = models.CharField(max_length=100, help_text="创建人")
    last_accessed = models.DateTimeField(null=True, blank=True, help_text="最后访问时间")
    quota = models.BigIntegerField(null=True, blank=True, help_text="存储配额(字节)")
    is_active = models.BooleanField(default=True, help_text="账户是否激活")

    def __str__(self):
        return f"{self.username} ({self.get_account_type_display()})"

    class Meta:
        verbose_name = "SFTP账户"
        verbose_name_plural = "SFTP账户管理"


class SFTPAuditLog(models.Model):
    """SFTP操作审计日志"""
    ACTION_CHOICES = [
        ('create', '创建'),
        ('delete', '删除'),
        ('reset_password', '重置密码'),
        ('permission_change', '权限变更'),
        ('lease_renew', '租期续期'),
        ('lease_terminate', '租期终止'),
    ]

    username = models.CharField(max_length=100, help_text="操作对象用户名")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, help_text="操作类型")
    operator = models.CharField(max_length=100, help_text="操作人")
    details = models.TextField(blank=True, help_text="操作详情")
    ip_address = models.GenericIPAddressField(null=True, blank=True, help_text="操作IP地址")
    timestamp = models.DateTimeField(auto_now_add=True, help_text="操作时间")

    def __str__(self):
        return f"{self.operator} {self.get_action_display()} {self.username} at {self.timestamp}"

    class Meta:
        verbose_name = "操作审计日志"
        verbose_name_plural = "操作审计日志"
        ordering = ['-timestamp']
class SFTPLeaseSettings(models.Model):
    """全局租期设置"""
    default_lease_days = models.IntegerField(default=180, help_text="新创建外部目录的默认租期(天)")
    default_notice_days = models.IntegerField(default=7, help_text="租期到期前多少天发送提醒通知")
    enabled = models.BooleanField(default=True, help_text="是否启用自动租期管理")

    def __str__(self):
        return f"租期设置: {self.default_lease_days}天, 提前{self.default_notice_days}天通知"

    class Meta:
        verbose_name = "全局租期设置"
        verbose_name_plural = "全局租期设置"


class DirectoryLease(models.Model):
    """外部目录租期记录"""
    username = models.CharField(max_length=100, unique=True, help_text="外部目录用户名")
    manager = models.CharField(max_length=100, help_text="对接的内部管理员")
    start_date = models.DateTimeField(auto_now_add=True, help_text="租期开始时间")
    end_date = models.DateTimeField(help_text="租期结束时间")
    original_end_date = models.DateTimeField(null=True, blank=True, help_text="原始租期结束时间(用于记录延期历史)")
    notice_sent = models.BooleanField(default=False, help_text="是否已发送到期提醒")
    is_active = models.BooleanField(default=True, help_text="租期是否有效")
    created_by = models.CharField(max_length=100, null=True, blank=True, help_text="创建人")

    def days_remaining(self):
        """计算剩余天数"""
        if not self.is_active:
            return 0
        now = timezone.now()
        if self.end_date < now:
            return 0
        return (self.end_date - now).days

    def status(self):
        """返回租期状态"""
        if not self.is_active:
            return "已取消"
        remaining = self.days_remaining()
        if remaining == 0:
            return "今日到期"
        elif remaining < 0:
            return "已过期"
        elif remaining <= getattr(settings, 'LEASE_NOTICE_DAYS', 7):
            return "即将到期"
        return "正常"

    def __str__(self):
        return f"{self.username} - 到期: {self.end_date.strftime('%Y-%m-%d')}"

    class Meta:
        verbose_name = "目录租期"
        verbose_name_plural = "目录租期记录"
