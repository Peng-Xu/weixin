"""
定时任务模块
基于 APScheduler 实现定时消息推送
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger


class TaskScheduler:
    """定时任务管理器"""

    def __init__(self):
        self._scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self._send_func = None  # 发送消息的回调函数
        self._tasks: list[dict] = []

    def set_send_func(self, func):
        """
        设置发送消息的回调函数
        func(target_type, target_name, message) -> bool
        """
        self._send_func = func

    def load_tasks(self, tasks: list[dict]):
        """从配置加载定时任务"""
        for task in tasks:
            try:
                self._add_task(task)
            except Exception as e:
                logger.error(f"加载定时任务失败 [{task.get('name', '?')}]: {e}")

    def _add_task(self, task: dict):
        name = task["name"]
        cron_expr = task["cron"]
        target_type = task["target_type"]
        target_name = task["target_name"]
        message = task.get("message", "")
        message_type = task.get("message_type", "text")

        # 解析 cron 表达式（支持 5 段和 6 段格式）
        parts = cron_expr.strip().split()
        if len(parts) == 5:
            trigger = CronTrigger(
                minute=parts[0], hour=parts[1],
                day=parts[2], month=parts[3], day_of_week=parts[4],
                timezone="Asia/Shanghai",
            )
        elif len(parts) == 6:
            trigger = CronTrigger(
                second=parts[0], minute=parts[1], hour=parts[2],
                day=parts[3], month=parts[4], day_of_week=parts[5],
                timezone="Asia/Shanghai",
            )
        else:
            raise ValueError(f"无效的 cron 表达式: {cron_expr}")

        def job_func(tt=target_type, tn=target_name, msg=message, mt=message_type, n=name):
            self._execute_task(n, tt, tn, msg, mt)

        self._scheduler.add_job(
            job_func,
            trigger=trigger,
            id=f"task_{name}",
            name=name,
            replace_existing=True,
        )
        self._tasks.append(task)
        logger.info(f"定时任务已注册: {name} ({cron_expr}) -> {target_type}:{target_name}")

    def _execute_task(self, name: str, target_type: str, target_name: str,
                      message: str, message_type: str):
        """执行定时任务"""
        if not self._send_func:
            logger.error(f"定时任务 [{name}] 无发送函数，跳过")
            return

        logger.info(f"执行定时任务: {name} -> {target_type}:{target_name}")

        try:
            if message_type == "text":
                self._send_func(target_type, target_name, message)
            elif message_type == "weather":
                city = message or "北京"
                weather_msg = self._fetch_weather(city)
                self._send_func(target_type, target_name, weather_msg)
            else:
                self._send_func(target_type, target_name, message)
        except Exception as e:
            logger.error(f"定时任务执行失败 [{name}]: {e}")

    def _fetch_weather(self, city: str) -> str:
        """获取天气信息（使用免费 API）"""
        try:
            import requests
            # 使用 wttr.in 免费天气 API
            resp = requests.get(
                f"https://wttr.in/{city}?format=3&lang=zh",
                timeout=10,
            )
            if resp.status_code == 200:
                return f"今日天气\n{resp.text.strip()}"
            else:
                return f"天气查询失败（{resp.status_code}）"
        except Exception as e:
            logger.error(f"天气查询异常: {e}")
            return "天气查询服务暂时不可用"

    def start(self):
        """启动调度器"""
        if self._tasks:
            self._scheduler.start()
            logger.info(f"定时任务调度器已启动，共 {len(self._tasks)} 个任务")
        else:
            logger.info("没有定时任务，调度器未启动")

    def stop(self):
        """停止调度器"""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("定时任务调度器已停止")
