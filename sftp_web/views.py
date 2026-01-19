import subprocess
import re
import json
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

# 脚本绝对路径 - 请根据实际位置修改
SCRIPT_PATH = "/root/manage_sftp_users.py"  # 需替换为实际路径


def validate_username(username):
    """验证用户名格式 (Linux标准)"""
    return re.match(r'^[a-z_][a-z0-9_-]*$', username) is not None


def validate_disk_quota(value):
    """验证磁盘配额格式 (1G, 512M, 100K)"""
    return re.match(r'^\d+(\.\d+)?[GMK]$', value.upper()) is not None


def execute_script(command):
    """执行SFTP管理脚本并返回结果"""
    try:
        result = subprocess.run(
            ['sudo', 'python3', SCRIPT_PATH] + command,
            capture_output=True,
            text=True,
            timeout=30
        )

        # 处理脚本返回的错误
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "需root权限运行" in stderr:
                return {"error": "权限错误: 需要root权限执行操作"}
            elif "管理员不存在" in stderr:
                return {"error": "错误: 指定的管理员不存在"}
            elif "用户已存在" in stderr or "管理员已存在" in stderr:
                return {"error": "错误: 用户名已存在"}
            elif "不可直接删除管理员" in stderr:
                return {"error": "错误: 不能删除管理员账户"}
            return {"error": f"操作失败: {stderr[:200]}"}

        return {"success": result.stdout.strip() or "操作成功完成"}

    except subprocess.TimeoutExpired:
        return {"error": "操作超时，请重试"}
    except Exception as e:
        return {"error": f"系统错误: {str(e)}"}


@csrf_exempt
def sftp_manager(request):
    context = {
        'managers': [],
        'external_dirs': [],
        'role_filter': request.GET.get('role', 'all'),
        'message': None,
        'error': None
    }

    # 处理POST请求 (所有操作)
    if request.method == 'POST':
        action = request.POST.get('action')

        # 创建管理员
        if action == 'create_manager':
            manager_name = request.POST.get('manager_name').strip()
            password = request.POST.get('password')

            if not validate_username(manager_name):
                context['error'] = "管理员名称无效: 仅允许小写字母、数字、下划线和连字符，且必须以字母开头"
            else:
                cmd = ['add-manager', manager_name]
                if password:
                    cmd.extend(['--password', password])
                result = execute_script(cmd)
                if 'error' in result:
                    context['error'] = result['error']
                else:
                    context['message'] = f"管理员 {manager_name} 创建成功!"

        # 创建外部目录
        elif action == 'create_user':
            username = request.POST.get('username').strip()
            manager = request.POST.get('manager').strip()
            password = request.POST.get('password')
            readonly = request.POST.get('readonly') == 'on'

            if not validate_username(username):
                context['error'] = "用户名无效: 仅允许小写字母、数字、下划线和连字符，且必须以字母开头"
            else:
                cmd = ['add-user', username, manager]
                if password:
                    cmd.extend(['--password', password])
                if readonly:
                    cmd.append('--readonly')
                result = execute_script(cmd)
                if 'error' in result:
                    context['error'] = result['error']
                else:
                    context['message'] = f"外部目录 {username} 创建成功!"

        # 删除外部目录
        elif action == 'delete_user':
            username = request.POST.get('username').strip()
            result = execute_script(['del-user', username])
            if 'error' in result:
                context['error'] = result['error']
            else:
                context['message'] = f"外部目录 {username} 已删除，所有残留已清理"

        # 清理残留
        elif action == 'clean_residue':
            username = request.POST.get('username').strip()
            result = execute_script(['clean-residue', username])
            if 'error' in result:
                context['error'] = result['error']
            else:
                context['message'] = f"用户 {username} 的系统残留已清理完成"

        # 设置配额
        elif action == 'set_quota':
            username = request.POST.get('username').strip()
            disk_space = request.POST.get('disk_space').strip()
            files = request.POST.get('files')

            if not validate_disk_quota(disk_space):
                context['error'] = "磁盘配额格式无效: 请使用 1G, 512M, 100K 等格式"
            else:
                cmd = ['set-quota', username, disk_space]
                if files and files.isdigit():
                    cmd.extend(['--files', files])
                result = execute_script(cmd)
                if 'error' in result:
                    context['error'] = result['error']
                else:
                    context['message'] = f"已为 {username} 设置配额: {disk_space}"

        # 切换目录权限
        elif action == 'toggle_permission':
            username = request.POST.get('username').strip()
            permission_mode = request.POST.get('permission_mode')
            
            if not validate_username(username):
                context['error'] = "用户名无效: 仅允许小写字母、数字、下划线和连字符，且必须以字母开头"
            else:
                # 构造脚本命令
                cmd = ['toggle-permission', username]
                if permission_mode == 'readonly':
                    cmd.append('--set-readonly')
                elif permission_mode == 'readwrite':
                    cmd.append('--set-readwrite')
                
                result = execute_script(cmd)
                if 'error' in result:
                    context['error'] = result['error']
                else:
                    status = '只读' if permission_mode == 'readonly' else '读写'
                    context['message'] = f"外部目录 {username} 已成功切换为{status}模式！已连接用户需重新登录生效。"

    # 获取当前用户列表 (GET请求或操作后刷新)
    try:
        result = subprocess.run(
            ['sudo', 'python3', SCRIPT_PATH, 'list', '--role', context['role_filter']],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            # 简化解析脚本输出 (实际应用应改进脚本输出结构化数据)
            output = result.stdout
            managers = []
            external_dirs = []

            current_section = None
            for line in output.split('\n'):
                line = line.strip()
                if "===== 管理员 =====" in line:
                    current_section = 'managers'
                    continue
                elif "===== 普通用户 =====" in line:
                    current_section = 'users'
                    continue

                if current_section == 'managers' and line.startswith('管理员:'):
                    parts = line.replace('管理员:', '').split('|')
                    name = parts[0].strip()
                    users = parts[1].replace('管理用户:', '').strip().split(',') if len(parts) > 1 else []
                    managers.append({
                        'name': name,
                        'managed_users': [u.strip() for u in users if u.strip()]
                    })

                elif current_section == 'users' and line.startswith('用户:'):
                    parts = line.replace('用户:', '').split('|')
                    user_info = {}
                    for part in parts:
                        key_val = part.split(':', 1)
                        if len(key_val) == 2:
                            key = key_val[0].strip().lower()
                            val = key_val[1].strip()
                            if key == '归属':
                                user_info['manager'] = val
                            elif key == '目录':
                                user_info['directory'] = val
                            elif key == '只读':
                                user_info['readonly'] = val == '是'
                    if 'manager' in user_info and 'directory' in user_info:
                        user_info['username'] = parts[0].split('|')[0].strip()
                        external_dirs.append(user_info)

            context['managers'] = managers
            context['external_dirs'] = external_dirs
        else:
            context['error'] = f"获取用户列表失败: {result.stderr.strip()}"

    except Exception as e:
        context['error'] = f"加载数据失败: {str(e)}"

    return render(request, 'sftp_web/index.html', context)
