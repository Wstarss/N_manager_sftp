from apscheduler.schedulers.background import BackgroundScheduler
from django_apscheduler.jobstores import DjangoJobStore, register_events, register_job
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from .models import SFTPLeaseSettings, DirectoryLease
from .utils import send_lease_notice_email, delete_external_directory, extend_directory_lease

scheduler = BackgroundScheduler(timezone=settings.TIME_ZONE)


def start():
    # 添加Django数据库job store
    scheduler.add_jobstore(DjangoJobStore(), "default")

    # 注册定时任务
    register_schedule_tasks()

    # 注册事件监听
    register_events(scheduler)

    # 启动调度器
    scheduler.start()
    print("租期管理调度器已启动...")


def register_schedule_tasks():
    """注册所有租期管理相关任务"""

    # 每天凌晨2点检查租期状态
    scheduler.add_job(
        check_and_process_leases,
        trigger="cron",
        hour=2,
        minute=0,
        id="daily_lease_check",
        max_instances=1,
        replace_existing=True,
        misfire_grace_time=3600  # 1小时的补偿执行时间
    )

    # 每隔6小时验证一次调度器状态(确保持续运行)
    scheduler.add_job(
        verify_scheduler_status,
        trigger="interval",
        hours=6,
        id="scheduler_verification",
        replace_existing=True
    )


@register_job(scheduler, "cron", hour=9, minute=0)
def check_and_process_leases():
    """检查并处理即将到期和已到期的目录租期"""
    from django.db import connection

    # 修复可能的数据库连接问题
    connection.close()

    try:
        # 获取全局租期设置
        lease_settings = SFTPLeaseSettings.objects.first()
        if not lease_settings or not lease_settings.enabled:
            print("租期管理已禁用，跳过本次检查")
            return

        now = timezone.now()
        notice_threshold = now + timedelta(days=lease_settings.default_notice_days)

        # 1. 处理即将到期的目录(发送提醒)
        expiring_leases = DirectoryLease.objects.filter(
            is_active=True,
            notice_sent=False,
            end_date__lte=notice_threshold,
            end_date__gt=now
        )

        for lease in expiring_leases:
            try:
                send_lease_notice_email(lease)
                lease.notice_sent = True
                lease.save()
                print(f"已发送租期提醒给 {lease.username}，剩余 {lease.days_remaining()} 天")
            except Exception as e:
                print(f"发送提醒邮件失败: {str(e)}")

        # 2. 处理已到期的目录
        expired_leases = DirectoryLease.objects.filter(
            is_active=True,
            end_date__lte=now
        )

        for lease in expired_leases:
            try:
                # 调用删除脚本
                success = delete_external_directory(lease.username)
                if success:
                    lease.is_active = False
                    lease.save()
                    print(f"成功删除过期目录: {lease.username}")
                else:
                    print(f"删除目录失败: {lease.username}，将在下次重试")
            except Exception as e:
                print(f"处理过期目录 {lease.username} 时出错: {str(e)}")

    except Exception as e:
        print(f"租期检查过程中出错: {str(e)}")


@register_job(scheduler, "interval", hours=6)
def verify_scheduler_status():
    """验证调度器状态并记录日志"""
    from datetime import datetime
    print(f"[{datetime.now()}] 租期管理调度器运行正常")