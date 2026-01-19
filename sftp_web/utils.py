from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string
import subprocess
import logging

logger = logging.getLogger(__name__)


def send_lease_notice_email(lease):
    """发送租期即将到期的通知邮件"""
    lease_settings = SFTPLeaseSettings.objects.first()
    subject = f"SFTP外部目录租期提醒: {lease.username}"

    context = {
        'username': lease.username,
        'manager': lease.manager,
        'days_remaining': lease.days_remaining(),
        'end_date': lease.end_date.strftime('%Y-%m-%d'),
        'notice_days': lease_settings.default_notice_days if lease_settings else 7,
        'site_url': settings.SITE_URL
    }

    message = render_to_string('emails/lease_notice.html', context)
    plain_message = render_to_string('emails/lease_notice.txt', context)

    send_mail(
        subject,
        plain_message,
        settings.DEFAULT_FROM_EMAIL,
        [lease.manager],
        html_message=message,
        fail_silently=False,
    )
    logger.info(f"已发送租期提醒邮件给 {lease.manager} (目录: {lease.username})")


def delete_external_directory(username):
    """调用外部脚本删除目录"""
    try:
        # 替换为您的实际删除脚本路径
        script_path = getattr(settings, 'DELETE_DIRECTORY_SCRIPT', '/opt/sftp/scripts/delete_external_dir.sh')

        result = subprocess.run(
            [script_path, username],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            logger.info(f"成功删除外部目录: {username}")
            return True
        else:
            logger.error(f"删除目录失败 {username}: {result.stderr}")
            return False

    except Exception as e:
        logger.exception(f"执行删除脚本时出错 {username}: {str(e)}")
        return False


def extend_directory_lease(username, additional_days=30):
    """延长目录租期"""
    from .models import DirectoryLease
    try:
        lease = DirectoryLease.objects.get(username=username, is_active=True)
        lease.original_end_date = lease.end_date  # 保存原始结束日期
        lease.end_date = lease.end_date + timedelta(days=additional_days)
        lease.notice_sent = False  # 重置通知状态
        lease.save()
        logger.info(f"已延长目录租期 {username} {additional_days}天")
        return True
    except DirectoryLease.DoesNotExist:
        logger.error(f"延长租期失败: 未找到活跃租期 {username}")
        return False