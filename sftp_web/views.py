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
    """启动APS调度器"""
    try:
        # 避免重复添加job store
        if not scheduler.get_jobstore('default'):
            scheduler.add_jobstore(DjangoJobStore(), "default")

        # 注册租期检查任务 - 每天凌晨2点执行
        scheduler.add_job(
            check_and_process_leases,
            trigger=CronTrigger(hour=2, minute=0),
            id="daily_lease_check",
            max_instances=1,
            replace_existing=True,
            misfire_grace_time=3600  # 1小时的宽限期
        )

        # 添加调度器事件监听
        register_events(scheduler)

        # 启动调度器
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
                elif len(password) < 8:
                    context['error'] = "密码长度至少为8位"
                else:
                    result = execute_script(['create-user', username, password])
                    if 'error' in result:
                        context['error'] = result['error']
                    else:
                        # 保存到数据库
                        SFTPAccount.objects.create(
                            username=username,
                            account_type='internal',
                            created_by=request.user.username if request.user.is_authenticated else 'system'
                        )
                        context['success'] = True
                        context['message'] = f"成功创建内部用户: {username}"

            # 2. 创建外部目录
            elif action == 'create_external':
                username = request.POST.get('username').strip()
                password = request.POST.get('password').strip()
                manager = request.POST.get('manager').strip()

                if not username or not password or not manager:
                    context['error'] = "用户名、密码和对接管理员都不能为空"
                elif len(password) < 8:
                    context['error'] = "密码长度至少为8位"
                else:
                    result = execute_script(['create-external', username, password])
                    if 'error' in result:
                        context['error'] = result['error']
                    else:
                        # 保存到数据库
                        account = SFTPAccount.objects.create(
                            username=username,
                            account_type='external',
                            manager=manager,
                            created_by=request.user.username if request.user.is_authenticated else 'system'
                        )

                        # 获取全局租期设置
                        lease_settings = SFTPLeaseSettings.objects.first()
                        lease_days = lease_settings.default_lease_days if lease_settings else 180

                        # 创建租期记录
                        end_date = timezone.now() + timedelta(days=lease_days)
                        DirectoryLease.objects.create(
                            username=username,
                            manager=manager,
                            end_date=end_date,
                            created_by=request.user.username if request.user.is_authenticated else 'system'
                        )

                        context['success'] = True
                        context['message'] = f"成功创建外部目录: {username}，租期 {lease_days} 天"

            # 3. 删除用户/目录
            elif action == 'delete_user':
                username = request.POST.get('username').strip()
                account_type = request.POST.get('account_type')

                if not username:
                    context['error'] = "用户名不能为空"
                else:
                    result = execute_script(['del-user', username])
                    if 'error' in result:
                        context['error'] = result['error']
                    else:
                        # 删除数据库记录
                        SFTPAccount.objects.filter(username=username).delete()
                        DirectoryLease.objects.filter(username=username).update(is_active=False)

                        context['success'] = True
                        context['message'] = f"成功删除 {account_type} '{username}'"

            # 4. 重置密码
            elif action == 'reset_password':
                username = request.POST.get('username').strip()
                new_password = request.POST.get('new_password').strip()

                if not username or not new_password:
                    context['error'] = "用户名和新密码不能为空"
                elif len(new_password) < 8:
                    context['error'] = "密码长度至少为8位"
                else:
                    result = execute_script(['reset-password', username, new_password])
                    if 'error' in result:
                        context['error'] = result['error']
                    else:
                        context['success'] = True
                        context['message'] = f"成功重置用户 '{username}' 的密码"

            # 5. 切换读写权限
            elif action == 'toggle_permission':
                username = request.POST.get('username').strip()
                permission_mode = request.POST.get('permission_mode')

                if not username or not permission_mode:
                    context['error'] = "参数错误"
                else:
                    # 获取当前用户信息
                    target_user = None
                    for user in context['external_dirs']:
                        if user['username'] == username:
                            target_user = user
                            break

                    if not target_user:
                        context['error'] = "未找到该外部目录"
                    else:
                        # 切换权限
                        result = execute_script([
                            'set-permission',
                            username,
                            permission_mode
                        ])

                        if 'error' in result:
                            context['error'] = result['error']
                        else:
                            context['success'] = True
                            mode_text = "读写" if permission_mode == 'readwrite' else "只读"
                            context['message'] = f"成功将 '{username}' 的权限切换为 {mode_text}"

            # 6. 更新租期设置
            elif action == 'update_lease_settings':
                default_lease_days = int(request.POST.get('default_lease_days', 180))
                default_notice_days = int(request.POST.get('default_notice_days', 7))
                enabled = 'enabled' in request.POST

                # 获取或创建设置
                settings_obj, created = SFTPLeaseSettings.objects.get_or_create(id=1)
                settings_obj.default_lease_days = default_lease_days
                settings_obj.default_notice_days = default_notice_days
                settings_obj.enabled = enabled
                settings_obj.save()

                # 更新调度任务（如果需要）
                if hasattr(scheduler, 'get_job') and scheduler.get_job("daily_lease_check"):
                    scheduler.reschedule_job(
                        "daily_lease_check",
                        trigger=CronTrigger(hour=2, minute=0)
                    )

                context['success'] = True
                context['message'] = "成功更新租期设置"

            # 7. 续期租约
            elif action == 'renew_lease':
                username = request.POST.get('username').strip()
                additional_days = int(request.POST.get('additional_days', 30))

                try:
                    lease = DirectoryLease.objects.get(username=username, is_active=True)

                    if not lease.original_end_date:
                        lease.original_end_date = lease.end_date

                    # 延长租期
                    lease.end_date = lease.end_date + timedelta(days=additional_days)
                    lease.notice_sent = False  # 重置通知状态
                    lease.save()

                    # 通知管理员
                    try:
                        # 获取管理员邮箱
                        manager_account = SFTPAccount.objects.filter(username=lease.manager).first()
                        manager_email = manager_account.email if manager_account and manager_account.email else f"{lease.manager}@company.com"

                        send_mail(
                            f"[SFTP系统] 外部目录 {username} 租期已延长",
                            f"外部目录 {username} 的租期已延长 {additional_days} 天，新的到期日期是 {lease.end_date.strftime('%Y-%m-%d')}。",
                            settings.DEFAULT_FROM_EMAIL,
                            [manager_email],
                            fail_silently=True,
                        )
                    except Exception as e:
                        logger.warning(f"发送续期通知邮件失败: {str(e)}")

                    context['success'] = True
                    context[
                        'message'] = f"成功延长 {username} 的租期 {additional_days} 天，新到期日期: {lease.end_date.strftime('%Y-%m-%d')}"
                except DirectoryLease.DoesNotExist:
                    context['error'] = "错误: 未找到该目录的租期记录"
                except Exception as e:
                    context['error'] = f"延长租期失败: {str(e)}"

            # 8. 终止租约
            elif action == 'terminate_lease':
                username = request.POST.get('username').strip()
                reason = request.POST.get('reason', '管理员提前终止')

                try:
                    lease = DirectoryLease.objects.get(username=username, is_active=True)

                    # 执行删除
                    result = execute_script(['del-user', username])
                    if 'error' in result:
                        context['error'] = result['error']
                    else:
                        # 更新租期记录
                        lease.is_active = False
                        lease.end_date = timezone.now()
                        lease.save()

                        # 通知管理员
                        try:
                            # 获取管理员邮箱
                            manager_account = SFTPAccount.objects.filter(username=lease.manager).first()
                            manager_email = manager_account.email if manager_account and manager_account.email else f"{lease.manager}@company.com"

                            send_mail(
                                f"[SFTP系统] 外部目录 {username} 已提前终止",
                                f"外部目录 {username} 已于 {timezone.now().strftime('%Y-%m-%d %H:%M')} 因以下原因被提前终止并删除:\n\n{reason}",
                                settings.DEFAULT_FROM_EMAIL,
                                [manager_email],
                                fail_silently=True,
                            )
                        except Exception as e:
                            logger.warning(f"发送终止通知邮件失败: {str(e)}")

                        context['success'] = True
                        context['message'] = f"外部目录 {username} 已提前终止并删除。"
                except DirectoryLease.DoesNotExist:
                    context['error'] = "错误: 未找到该目录的租期记录"
                except Exception as e:
                    context['error'] = f"终止租期失败: {str(e)}"

            # 9. 手动执行租期管理
            elif action == 'run_lease_management':
                try:
                    # 直接调用检查函数
                    check_and_process_leases()
                    context['success'] = True
                    context['message'] = "成功手动执行租期管理任务"
                except Exception as e:
                    context['error'] = f"执行租期管理失败: {str(e)}"

        # 获取统计信息
        context['internal_count'] = len(context['internal_users'])
        context['external_count'] = len(context['external_dirs'])

        # 获取租期信息
        active_leases = DirectoryLease.objects.filter(is_active=True)
        context['active_leases_dict'] = {lease.username: lease for lease in active_leases}

        # 获取全局租期设置
        lease_settings = SFTPLeaseSettings.objects.first()
        if not lease_settings:
            lease_settings = SFTPLeaseSettings.objects.create(
                default_lease_days=180,
                default_notice_days=7,
                enabled=True
            )
        context['lease_settings'] = lease_settings

        # 获取即将到期的目录
        notice_threshold = timezone.now() + timedelta(days=lease_settings.default_notice_days)
        context['expiring_soon'] = active_leases.filter(
            end_date__lte=notice_threshold,
            end_date__gt=timezone.now()
        ).order_by('end_date')

        # 获取已过期的目录
        context['expired_leases'] = active_leases.filter(end_date__lte=timezone.now()).order_by('end_date')

        # 获取系统信息
        try:
            disk_result = subprocess.run(
                ['df', '-h', '/home'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if disk_result.returncode == 0:
                lines = disk_result.stdout.strip().split('\n')
                if len(lines) > 1:
                    parts = lines[1].split()
                    context['disk_usage'] = {
                        'filesystem': parts[0],
                        'size': parts[1],
                        'used': parts[2],
                        'avail': parts[3],
                        'use_percent': parts[4],
                        'mount': parts[5]
                    }
        except Exception as e:
            logger.warning(f"获取磁盘信息失败: {str(e)}")
            context['disk_usage'] = None

        # 添加调度器状态
        context['scheduler_status'] = {
            'running': scheduler.running,
            'next_run_time': scheduler.get_job("daily_lease_check").next_run_time if scheduler.get_job(
                "daily_lease_check") else None
        }

    except Exception as e:
        logger.error(f"处理SFTP管理请求时发生错误: {str(e)}")
        context['error'] = f"系统错误: {str(e)}"

    return render(request, 'sftp_web/index.html', context)


@csrf_exempt
def api_create_external_dir(request):
    """API端点：创建外部目录（供其他系统调用）"""
    if request.method != 'POST':
        return JsonResponse({'error': '仅支持POST请求'}, status=405)

    try:
        data = json.loads(request.body)
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        manager = data.get('manager', '').strip()
        lease_days = data.get('lease_days', 180)
        api_key = data.get('api_key', '')

        # 验证API密钥（简单实现，实际应用中应使用更安全的认证）
        if api_key != settings.API_SECRET_KEY:
            return JsonResponse({'error': '无效的API密钥'}, status=401)

        if not username or not password or not manager:
            return JsonResponse({'error': '缺少必要参数: username, password, manager'}, status=400)

        if len(password) < 8:
            return JsonResponse({'error': '密码长度至少为8位'}, status=400)

        # 检查用户名是否已存在
        if SFTPAccount.objects.filter(username=username).exists():
            return JsonResponse({'error': f'用户名 {username} 已存在'}, status=400)

        # 创建外部目录
        result = execute_script(['create-external', username, password])
        if 'error' in result:
            return JsonResponse({'error': result['error']}, status=500)

        # 保存到数据库
        account = SFTPAccount.objects.create(
            username=username,
            account_type='external',
            manager=manager,
            created_by='api'
        )

        # 创建租期记录
        end_date = timezone.now() + timedelta(days=int(lease_days))
        DirectoryLease.objects.create(
            username=username,
            manager=manager,
            end_date=end_date,
            created_by='api'
        )

        return JsonResponse({
            'success': True,
            'username': username,
            'path': f'/{username}/',
            'lease_end_date': end_date.strftime('%Y-%m-%d'),
            'message': f'成功创建外部目录 {username}'
        })

    except json.JSONDecodeError:
        return JsonResponse({'error': '无效的JSON格式'}, status=400)
    except Exception as e:
        logger.error(f"API创建外部目录失败: {str(e)}")
        return JsonResponse({'error': f'服务器错误: {str(e)}'}, status=500)


def trigger_lease_management(request):
    """手动触发租期管理"""
    try:
        # 直接调用检查函数，而不是管理命令
        check_and_process_leases()
        return JsonResponse({'success': True, 'message': '成功执行租期管理任务'})
    except Exception as e:
        logger.error(f"手动触发租期管理失败: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)