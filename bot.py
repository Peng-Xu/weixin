"""
微信机器人主程序
基于 WeChatFerry (wcferry) 实现

使用方法:
  1. 确保已安装指定版本的微信 PC 客户端 (3.9.12.17 / 3.9.12.51 / 4.1.8)
  2. 先登录微信
  3. pip install -r requirements.txt
  4. 复制 config.example.yaml 为 config.yaml 并修改配置
  5. python bot.py
"""

import signal
import sys
import time
import threading

from loguru import logger
from wcferry import Wcf

from config import load_config
from ai_chat import AIChat
from message_handler import MessageHandler
from scheduler import TaskScheduler
from safety import RateLimiter


class WxBot:
    """微信机器人主类"""

    def __init__(self, config_path: str = "config.yaml"):
        self.config = load_config(config_path)
        self._setup_logging()
        self.wcf: Wcf | None = None
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
        logger.info("=" * 50)
        logger.info("  微信机器人启动中...")
        logger.info("  基于 WeChatFerry (wcferry)")
        logger.info("  支持微信 3.9.12.x / 4.1.8")
        logger.info("=" * 50)

        # 1. 连接微信
        wcf_config = self.config.get("wcferry", {})
        try:
            self.wcf = Wcf(
                host=wcf_config.get("host", "127.0.0.1"),
                port=wcf_config.get("port", 10086),
                debug=wcf_config.get("debug", False),
            )
            if not self.wcf.is_login():
                logger.error("微信未登录！请先在 PC 上登录微信，然后重新运行")
                sys.exit(1)
        except TypeError:
            # 兼容旧版 wcferry（不支持 host/port 参数）
            logger.warning("wcferry 版本较旧，使用默认连接方式")
            self.wcf = Wcf()
            if not self.wcf.is_login():
                logger.error("微信未登录！请先在 PC 上登录微信，然后重新运行")
                sys.exit(1)
        except Exception as e:
            logger.error(f"连接微信失败: {e}")
            logger.error("请确认:")
            logger.error("  1. 微信 PC 客户端已打开并登录")
            logger.error("     支持版本: 3.9.12.17 / 3.9.12.51 / 4.1.8")
            logger.error("  2. 已安装 wcferry: pip install wcferry")
            logger.error("  3. wcferry 版本与微信版本匹配")
            sys.exit(1)

        # 检测微信版本
        self._detect_wechat_version()

        self_info = self.wcf.get_self_wxid()
        logger.info(f"微信已连接, wxid={self_info}")

        # 2. 初始化各模块
        ai = AIChat(self.config)
        limiter = RateLimiter(self.config)

        self.handler = MessageHandler(self.config, self.wcf, ai, limiter)
        self.handler.set_self_wxid(self_info)
        self.handler.refresh_contacts()

        # 3. 启动定时任务
        if self.config["scheduler"]["enabled"]:
            self.scheduler = TaskScheduler()
            self.scheduler.set_send_func(self.handler.send_to)
            self.scheduler.load_tasks(self.config["scheduler"].get("tasks", []))
            self.scheduler.start()

        # 4. 注册消息回调
        self._running = True
        self._start_message_loop()

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

    def _start_message_loop(self):
        """启动消息接收循环"""
        def loop():
            while self._running:
                try:
                    # 尝试新版 API（WeChat 4.x）
                    # 新版 wcferry 可能使用 enable_recv_msg 带回调
                    try:
                        self.wcf.enable_receiving_msg()
                    except TypeError:
                        # 兼容某些版本使用不同的方法签名
                        self.wcf.enable_recv_msg()
                    except AttributeError:
                        self.wcf.enable_recv_msg()

                    while self._running:
                        msg = self.wcf.get_msg()
                        if msg:
                            try:
                                self.handler.handle_message(msg)
                            except Exception as e:
                                logger.error(f"消息处理异常: {e}", exc_info=True)
                except Exception as e:
                    if self._running:
                        logger.error(f"消息循环异常: {e}", exc_info=True)
                        time.sleep(3)  # 出错后等待重试

        t = threading.Thread(target=loop, daemon=True, name="msg-loop")
        t.start()

    def _detect_wechat_version(self):
        """检测微信版本并记录"""
        try:
            # 尝试获取微信版本信息（新版 wcferry 支持）
            if hasattr(self.wcf, 'get_wechat_version'):
                version = self.wcf.get_wechat_version()
                logger.info(f"检测到微信版本: {version}")
                if version.startswith("4."):
                    logger.info("微信 4.x 模式已激活")
                elif version.startswith("3.9."):
                    logger.info("微信 3.9.x 兼容模式")
                else:
                    logger.warning(f"未经测试的微信版本: {version}，可能存在兼容性问题")
            else:
                logger.info("无法检测微信版本（wcferry 版本较旧），继续运行...")
        except Exception as e:
            logger.debug(f"版本检测失败: {e}")

    def _signal_handler(self, signum, frame):
        logger.info(f"收到退出信号 ({signum})")
        self._running = False

    def stop(self):
        """停止机器人"""
        logger.info("正在停止机器人...")
        self._running = False

        if self.scheduler:
            self.scheduler.stop()

        if self.wcf:
            try:
                self.wcf.disable_recv_msg()
            except Exception:
                pass

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
