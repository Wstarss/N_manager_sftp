import subprocess
import json
import os
from datetime import datetime, timedelta
from django.shortcuts import render
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.db import connection
import logging

# APScheduler 相关导入
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from django_apscheduler.jobstores import DjangoJobStore, register_events, register_job

# 模型导入
from .models import SFTPAccount, SFTPLeaseSettings, DirectoryLease

logger = logging.getLogger(__name__)

# 创建全局调度器实例
scheduler = BackgroundScheduler(timezone=settings.TIME_ZONE)


def start_scheduler():
    """启动APS调度器（适配新版APScheduler）"""
    try:
        # 清空已有任务（避免重复添加）
        scheduler.remove_all_jobs()

        # 直接添加任务（无需手动管理 jobstore）
        scheduler.add_job(
            check_and_process_leases,
            trigger=CronTrigger(hour=2, minute=0),
            id="daily_lease_check",
            max_instances=1,
            replace_existing=True,
            misfire_grace_time=3600  # 1小时的宽限期
        )

        # 启动调度器（仅启动一次）
        if not scheduler.running:
            scheduler.start()
            logger.info("APScheduler已启动，租期管理任务已注册")

    except Exception as e:
        logger.error(f"启动调度器失败: {str(e)}")


def check_and_process_leases():
    """检查并处理即将到期和已到期的目录租期"""
    from django.db import connection

    # 修复可能的数据库连接问题
    connection.close()

    try:
        logger.info("开始执行租期检查任务...")

        # 获取全局租期设置
        try:
            lease_settings = SFTPLeaseSettings.objects.first()
        except Exception as e:
            logger.error(f"获取租期设置失败: {str(e)}")
            lease_settings = None

        if not lease_settings or not lease_settings.enabled:
            logger.info("租期管理已禁用，跳过本次检查")
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

        logger.info(f"发现 {expiring_leases.count()} 个即将到期的目录")

        for lease in expiring_leases:
            try:
                send_lease_notice_email(lease)
                lease.notice_sent = True
                lease.save()
                logger.info(
                    f"已发送租期提醒给 {lease.manager} (目录: {lease.username}，剩余 {lease.days_remaining()} 天)")
            except Exception as e:
                logger.error(f"发送提醒邮件失败 {lease.username}: {str(e)}")

        # 2. 处理已到期的目录
        expired_leases = DirectoryLease.objects.filter(
            is_active=True,
            end_date__lte=now
        )

        logger.info(f"发现 {expired_leases.count()} 个已到期的目录")

        for lease in expired_leases:
            try:
                # 调用删除脚本
                success = delete_external_directory(lease.username)
                if success:
                    lease.is_active = False
                    lease.save()
                    logger.info(f"成功删除过期目录: {lease.username}")
                else:
                    logger.warning(f"删除目录失败: {lease.username}，将在下次重试")
            except Exception as e:
                logger.error(f"处理过期目录 {lease.username} 时出错: {str(e)}")

        logger.info("租期检查任务完成")

    except Exception as e:
        logger.exception(f"租期检查过程中出错: {str(e)}")


def send_lease_notice_email(lease):
    """发送租期即将到期的通知邮件"""
    subject = f"[SFTP系统] 外部目录租期提醒: {lease.username}"

    lease_settings = SFTPLeaseSettings.objects.first()

    context = {
        'username': lease.username,
        'manager': lease.manager,
        'days_remaining': lease.days_remaining(),
        'end_date': lease.end_date.strftime('%Y-%m-%d'),
        'notice_days': lease_settings.default_notice_days if lease_settings else 7,
    }

    message = (
        f"尊敬的管理员 {lease.manager}：\n\n"
        f"您管理的外部SFTP目录 '{lease.username}' 将在 {lease.days_remaining()} 天后到期（{lease.end_date.strftime('%Y-%m-%d')}）。\n"
        f"到期后该目录将被自动删除，请及时通知外部用户备份重要数据。\n\n"
        f"如需延长租期，请登录SFTP管理系统进行操作。\n\n"
        f"SFTP管理团队"
    )

    try:
        # 获取管理员邮箱（假设格式为 username@company.com）
        manager_email = f"{lease.manager}@company.com"  # 实际应用中应从用户模型获取

        send_mail(
            subject,
            message,
            settings.DEFAULT_FROM_EMAIL,
            [manager_email],
            fail_silently=False,
        )
        logger.info(f"成功发送租期提醒邮件到 {manager_email}")
    except Exception as e:
        logger.error(f"发送邮件失败: {str(e)}")
        raise


def delete_external_directory(username):
    """调用外部脚本删除目录"""
    try:
        result = execute_script(['del-user', username])

        if 'error' in result:
            logger.error(f"删除目录失败 {username}: {result['error']}")
            return False

        # 删除数据库记录
        SFTPAccount.objects.filter(username=username).delete()
        DirectoryLease.objects.filter(username=username).update(is_active=False)

        logger.info(f"成功删除外部目录: {username}")
        return True

    except Exception as e:
        logger.exception(f"执行删除操作时出错 {username}: {str(e)}")
        return False


# 初始化调度器 (在模块加载时)
start_scheduler()


def execute_script(command_args):
    """执行SFTP管理脚本"""
    try:
        full_command = ['sudo', 'python3', settings.SCRIPT_PATH] + command_args
        result = subprocess.run(
            full_command,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {'message': result.stdout.strip()}
        else:
            error_msg = result.stderr.strip() or result.stdout.strip()
            logger.error(f"脚本执行失败: {' '.join(full_command)} - {error_msg}")
            return {'error': error_msg}
    except Exception as e:
        logger.error(f"执行脚本时发生异常: {str(e)}")
        return {'error': str(e)}


def format_bytes(bytes_size):
    """将字节转换为人类可读格式"""
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.1f} KB"
    elif bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_size / (1024 * 1024 * 1024):.1f} GB"


def get_directory_size(path):
    """获取目录大小（安全方式）"""
    try:
        if not os.path.exists(path) or not os.path.isdir(path):
            return 0

        total_size = 0
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total_size += os.path.getsize(fp)
                except (OSError, FileNotFoundError):
                    continue
        return total_size
    except Exception as e:
        logger.error(f"获取目录大小失败: {str(e)}")
        return 0


def sftp_manager(request):
    context = {
        'internal_users': [],
        'external_dirs': [],
        'success': False,
        'error': None,
        'message': None,
    }

    try:
        # 获取所有SFTP账户
        result = execute_script(['list-users'])

        if 'error' in result:
            context['error'] = f"获取用户列表失败: {result['error']}"
            return render(request, 'sftp_web/index.html', context)

        all_users = result.get('users', [])
        context['internal_users'] = [u for u in all_users if u.get('type') == 'internal']
        context['external_dirs'] = [u for u in all_users if u.get('type') == 'external']

        # 获取目录大小和状态
        for user in context['external_dirs']:
            path = f"/home/{user['username']}"
            user['size'] = format_bytes(get_directory_size(path))
            user['path'] = f"/{user['username']}/"
            user['readonly'] = user.get('readonly', False)
            # 获取管理员
            try:
                account = SFTPAccount.objects.get(username=user['username'])
                user['manager'] = account.manager
            except SFTPAccount.DoesNotExist:
                user['manager'] = "未知"

        # 处理POST请求
        if request.method == 'POST':
            action = request.POST.get('action')

            # 1. 创建内部用户
            if action == 'create_internal':
                username = request.POST.get('username').strip()
                password = request.POST.get('password').strip()
                email = request.POST.get('email', '').strip()

                if not username or not password:
                    context['error'] = "用户名和密码不能为空"
                else:
                    # 执行创建内部用户脚本
                    result = execute_script(['create-internal', username, password])
                    if 'error' in result:
                        context['error'] = f"创建内部用户失败: {result['error']}"
                    else:
                        # 保存到数据库
                        SFTPAccount.objects.create(
                            username=username,
                            type='internal',
                            email=email,
                            manager=request.user.username  # 假设当前登录用户是管理员
                        )
                        context['success'] = True
                        context['message'] = f"内部用户 {username} 创建成功"

            # 2. 创建外部目录
            elif action == 'create_external':
                username = request.POST.get('username').strip()
                manager = request.POST.get('manager').strip()
                end_date_str = request.POST.get('end_date').strip()
                readonly = request.POST.get('readonly', 'false') == 'true'

                if not username or not manager or not end_date_str:
                    context['error'] = "用户名、管理员、到期时间不能为空"
                else:
                    try:
                        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                        # 执行创建外部目录脚本
                        result = execute_script(['create-external', username, str(readonly).lower()])
                        if 'error' in result:
                            context['error'] = f"创建外部目录失败: {result['error']}"
                        else:
                            # 保存SFTP账户
                            account = SFTPAccount.objects.create(
                                username=username,
                                type='external',
                                manager=manager,
                                readonly=readonly
                            )
                            # 保存租期信息
                            DirectoryLease.objects.create(
                                username=username,
                                manager=manager,
                                end_date=end_date,
                                is_active=True,
                                notice_sent=False
                            )
                            context['success'] = True
                            context['message'] = f"外部目录 {username} 创建成功，租期至 {end_date_str}"
                    except ValueError:
                        context['error'] = "到期时间格式错误，请使用YYYY-MM-DD"
                    except Exception as e:
                        context['error'] = f"创建外部目录异常: {str(e)}"

            # 3. 删除用户/目录
            elif action == 'delete':
                username = request.POST.get('username').strip()
                if not username:
                    context['error'] = "请选择要删除的用户/目录"
                else:
                    # 判断类型并执行删除
                    try:
                        account = SFTPAccount.objects.get(username=username)
                        if account.type == 'internal':
                            result = execute_script(['del-user', username])
                        else:
                            result = delete_external_directory(username)
                            result = {'success': True} if result else {'error': '删除脚本执行失败'}

                        if 'error' in result:
                            context['error'] = f"删除失败: {result['error']}"
                        else:
                            account.delete()
                            context['success'] = True
                            context['message'] = f"{username} 删除成功"
                    except SFTPAccount.DoesNotExist:
                        context['error'] = f"用户/目录 {username} 不存在"

            # 4. 延长外部目录租期
            elif action == 'extend_lease':
                username = request.POST.get('username').strip()
                end_date_str = request.POST.get('end_date').strip()

                if not username or not end_date_str:
                    context['error'] = "用户名和新到期时间不能为空"
                else:
                    try:
                        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                        lease = DirectoryLease.objects.get(username=username, is_active=True)
                        lease.end_date = end_date
                        lease.notice_sent = False  # 重置提醒状态
                        lease.save()
                        context['success'] = True
                        context['message'] = f"外部目录 {username} 租期已延长至 {end_date_str}"
                    except DirectoryLease.DoesNotExist:
                        context['error'] = f"未找到 {username} 的有效租期记录"
                    except ValueError:
                        context['error'] = "到期时间格式错误，请使用YYYY-MM-DD"

            # 刷新用户列表
            result = execute_script(['list-users'])
            if 'error' not in result:
                all_users = result.get('users', [])
                context['internal_users'] = [u for u in all_users if u.get('type') == 'internal']
                context['external_dirs'] = [u for u in all_users if u.get('type') == 'external']

    except Exception as e:
        logger.error(f"SFTP管理页面处理异常: {str(e)}")
        context['error'] = f"系统异常: {str(e)}"

    return render(request, 'sftp_web/index.html', context)


@csrf_exempt
def api_get_lease_info(request):
    """API接口：获取指定目录的租期信息"""
    if request.method == 'GET':
        username = request.GET.get('username')
        if not username:
            return JsonResponse({'error': '缺少username参数'}, status=400)

        try:
            lease = DirectoryLease.objects.get(username=username, is_active=True)
            data = {
                'username': lease.username,
                'manager': lease.manager,
                'end_date': lease.end_date.strftime('%Y-%m-%d'),
                'days_remaining': lease.days_remaining(),
                'notice_sent': lease.notice_sent
            }
            return JsonResponse(data)
        except DirectoryLease.DoesNotExist:
            return JsonResponse({'error': '租期记录不存在或已失效'}, status=404)
    return JsonResponse({'error': '仅支持GET请求'}, status=405)


@csrf_exempt
def api_manual_lease_check(request):
    """API接口：手动触发租期检查"""
    if request.method == 'POST' and request.user.is_superuser:
        try:
            check_and_process_leases()
            return JsonResponse({'success': True, 'message': '租期检查已执行'})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': '仅允许管理员POST请求'}, status=403)


# 确保程序退出时正确关闭调度器
import atexit

atexit.register(lambda: scheduler.shutdown() if scheduler.running else None)