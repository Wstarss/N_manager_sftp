# sftp_web/signals.py
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.utils import timezone
from django.conf import settings
from .models import SFTPAccount, DirectoryLease, SFTPLeaseSettings, SFTPAuditLog


@receiver(post_save, sender=SFTPAccount)
def create_lease_on_external_account_creation(sender, instance, created, **kwargs):
    """
    当创建新的外部SFTP账户时，自动创建租期记录
    """
    if created and instance.account_type == 'external' and instance.is_active:
        # 获取全局租期设置
        try:
            lease_settings = SFTPLeaseSettings.objects.first()
            if not lease_settings or not lease_settings.enabled:
                lease_settings = SFTPLeaseSettings(default_lease_days=180, default_notice_days=7, enabled=True)
        except SFTPLeaseSettings.DoesNotExist:
            lease_settings = SFTPLeaseSettings(default_lease_days=180, default_notice_days=7, enabled=True)

        # 计算租期结束时间
        end_date = timezone.now() + timezone.timedelta(days=lease_settings.default_lease_days)

        # 创建租期记录
        DirectoryLease.objects.create(
            username=instance.username,
            manager=instance.manager,
            end_date=end_date,
            created_by=instance.created_by
        )

        # 记录审计日志
        SFTPAuditLog.objects.create(
            username=instance.username,
            action='create',
            operator=instance.created_by,
            details=f"创建外部目录账户，初始租期{lease_settings.default_lease_days}天"
        )


@receiver(post_save, sender=DirectoryLease)
def handle_lease_update(sender, instance, **kwargs):
    """
    当租期记录更新时（如延期），记录审计日志
    """
    if instance.pk:  # 确保是更新而不是创建
        original = DirectoryLease.objects.get(pk=instance.pk)
        if original.end_date != instance.end_date and instance.original_end_date != original.end_date:
            # 记录租期延期
            days_extended = (instance.end_date - original.end_date).days
            SFTPAuditLog.objects.create(
                username=instance.username,
                action='lease_renew',
                operator=getattr(instance, 'updated_by', 'system'),
                details=f"租期延长{days_extended}天，从{original.end_date.strftime('%Y-%m-%d')}延长至{instance.end_date.strftime('%Y-%m-%d')}"
            )
            # 保存原始结束日期用于历史记录
            instance.original_end_date = original.end_date


@receiver(pre_delete, sender=SFTPAccount)
def handle_account_deletion(sender, instance, **kwargs):
    """
    当删除SFTP账户时，处理相关资源
    """
    if instance.account_type == 'external':
        # 停用租期而不是删除，保留历史记录
        try:
            lease = DirectoryLease.objects.get(username=instance.username)
            lease.is_active = False
            lease.save()

            # 记录审计日志
            SFTPAuditLog.objects.create(
                username=instance.username,
                action='lease_terminate',
                operator=getattr(instance, 'deleted_by', 'system'),
                details="账户删除，租期已停用"
            )
        except DirectoryLease.DoesNotExist:
            pass