from django.apps import AppConfig


class SftpWebConfig(AppConfig):
    name = 'sftp_web'
    verbose_name = 'SFTP管理'

    def ready(self):
        # 初始化调度器
        from .apscheduler_start import init_scheduler
        init_scheduler()

        # 导入信号处理器
        import sftp_web.signals