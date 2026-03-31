"""
微信机器人主程序

支持两种接入方式：
  • wcferry  —— 微信 PC 3.9.12.17 / 3.9.12.51（Windows 専用）
  • webhook  —— 微信 4.x，通过 HTTP API 收发消息

使用方法:
  1. 配置 config.yaml，设置 wechat.adapter = "wcferry" 或 "webhook"
  2. python bot.py
"""

import signal
import sys
import time

from loguru import logger

from config import load_config
from ai_chat import AIChat
from message_handler import MessageHandler
from scheduler import TaskScheduler
from safety import RateLimiter
from wechat_adapter import WechatAdapter


def build_adapter(config: dict) -> WechatAdapter:
    """根据配置实例化对应的微信适配器"""
    adapter_type = config.get("wechat", {}).get("adapter", "wcferry")

    if adapter_type == "wcferry":
        from wcferry_adapter import WcferryAdapter
        return WcferryAdapter()

    if adapter_type == "webhook":
        from webhook_adapter import WebhookAdapter
        return WebhookAdapter(config)

    raise ValueError(f"未知的微信适配器类型: {adapter_type}")


class WxBot:
    """微信机器人主类"""

    def __init__(self, config_path: str = "config.yaml"):
        self.config = load_config(config_path)
        self._setup_logging()
        self.adapter: WechatAdapter | None = None
        self.handler: MessageHandler | None = None
        self.scheduler: TaskScheduler | None = None
        self._running = False

    def _setup_logging(self):
        """配置日志"""
        log_config = self.config.get("logging", {})
        logger.remove()  # 移除默认 handler
        # 控制台输出
        logger.add(
            sys.stderr,
            level=log_config.get("level", "INFO"),
            format="<green>{time:HH:mm:ss}</green> | <level>{level:8s}</level> | {message}",
        )
        # 文件输出
        log_file = log_config.get("file", "logs/wxbot.log")
        logger.add(
            log_file,
            level="DEBUG",
            rotation=log_config.get("rotation", "10 MB"),
            retention=log_config.get("retention", "7 days"),
            encoding="utf-8",
        )

    def start(self):
        """启动机器人"""
        adapter_type = self.config.get("wechat", {}).get("adapter", "wcferry")
        logger.info("=" * 50)
        logger.info("  微信机器人启动中...")
        logger.info(f"  接入方式: {adapter_type}")
        logger.info("=" * 50)

        # 1. 创建并启动适配器
        try:
            self.adapter = build_adapter(self.config)
        except ValueError as e:
            logger.error(e)
            sys.exit(1)

        if not self.adapter.start():
            logger.error("适配器启动失败")
            sys.exit(1)

        if not self.adapter.is_login():
            logger.error("微信未登录！")
            if adapter_type == "wcferry":
                logger.error("请先在 PC 上登录微信，然后重新运行")
            elif adapter_type == "webhook":
                logger.error("请检查微信客户端容器是否运行且已扫码登录")
            sys.exit(1)

        self_wxid = self.adapter.get_self_wxid()
        logger.info(f"微信已连接, wxid={self_wxid or '(webhook模式无需wxid)'}")

        # 2. 初始化各模块
        ai = AIChat(self.config)
        limiter = RateLimiter(self.config)

        self.handler = MessageHandler(self.config, self.adapter, ai, limiter)
        self.handler.set_self_wxid(self_wxid)
        self.handler.refresh_contacts()

        # 3. 启动定时任务
        if self.config["scheduler"]["enabled"]:
            self.scheduler = TaskScheduler()
            self.scheduler.set_send_func(self.handler.send_to)
            self.scheduler.load_tasks(self.config["scheduler"].get("tasks", []))
            self.scheduler.start()

        # 4. 注册消息回调并启动接收
        self.adapter.set_message_callback(self.handler.handle_message)
        self._running = True
        self.adapter.enable_receiving()

        logger.info("机器人已就绪，等待消息...")
        logger.info("按 Ctrl+C 停止")

        # 5. 注册退出信号
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # 保持主线程运行
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def _signal_handler(self, signum, frame):
        logger.info(f"收到退出信号 ({signum})")
        self._running = False

    def stop(self):
        """停止机器人"""
        logger.info("正在停止机器人...")
        self._running = False

        if self.scheduler:
            self.scheduler.stop()

        if self.adapter:
            self.adapter.stop()

        logger.info("机器人已停止")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="微信机器人")
    parser.add_argument("-c", "--config", default="config.yaml", help="配置文件路径")
    args = parser.parse_args()

    bot = WxBot(config_path=args.config)
    bot.start()


if __name__ == "__main__":
    main()
