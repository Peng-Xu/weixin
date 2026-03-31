"""
微信消息读取引擎

功能:
  1. 读取指定联系人/群聊的所有历史消息（通过滚动加载）
  2. 遍历所有会话，逐个读取消息
  3. 实时监控新消息（定时轮询对比）
  4. 数据持久化到 SQLite
"""

from __future__ import annotations

import time
import threading
from typing import Callable
from loguru import logger

from .pages.wechat_main import WeChatMainPage
from .pages.chat_page import ChatPage, ChatMessage
from .core.locator import ElementLocator
from .core.actions import ActionExecutor
from .storage import MessageStorage


class MessageReader:
    """微信消息读取引擎"""

    def __init__(
        self,
        db_path: str = "wechat_messages.db",
        min_delay: float = 0.5,
        max_delay: float = 2.0,
        screenshot_dir: str | None = None,
        strategy_cache: str | None = "logs/strategy_cache.json",
    ):
        # 核心组件
        self._locator = ElementLocator(cache_file=strategy_cache)
        self._action = ActionExecutor(
            min_delay=min_delay,
            max_delay=max_delay,
            screenshot_dir=screenshot_dir,
        )
        self._storage = MessageStorage(db_path)

        # Page Objects
        self._main_page = WeChatMainPage(self._locator, self._action)
        self._chat_page = ChatPage(self._locator, self._action)

        # 实时监控
        self._monitoring = False
        self._monitor_thread: threading.Thread | None = None
        self._on_new_message: Callable[[str, ChatMessage], None] | None = None

    def connect(self) -> bool:
        """
        连接微信窗口

        Returns:
            是否成功连接
        """
        if not self._main_page.attach():
            return False
        self._chat_page.set_window(self._main_page.window)
        logger.info("微信 UI 自动化已连接")
        return True

    # ── 读取消息 ──

    def read_chat_messages(
        self,
        contact_name: str,
        max_scrolls: int = 50,
        save_to_db: bool = True,
    ) -> list[ChatMessage]:
        """
        读取指定联系人/群聊的所有可加载消息

        Args:
            contact_name: 联系人名/群名
            max_scrolls: 最大滚动次数
            save_to_db: 是否保存到数据库

        Returns:
            消息列表
        """
        logger.info(f"开始读取聊天: {contact_name}")

        # 1. 搜索并切换到目标聊天
        if not self._main_page.search_and_select(contact_name):
            logger.error(f"无法切换到聊天: {contact_name}")
            return []

        self._action.wait_stable(1.0)

        # 2. 滚动加载并收集消息
        messages = self._chat_page.get_all_messages_by_scrolling(max_scrolls)

        # 3. 保存到数据库
        if save_to_db and messages:
            saved = self._storage.save_messages(contact_name, messages)
            logger.info(f"[{contact_name}] 保存 {saved} 条新消息到数据库")

        logger.info(f"[{contact_name}] 读取完成, 共 {len(messages)} 条消息")
        return messages

    def read_all_sessions(
        self,
        max_scrolls_per_chat: int = 30,
        save_to_db: bool = True,
        skip_sessions: list[str] | None = None,
    ) -> dict[str, list[ChatMessage]]:
        """
        遍历所有会话，读取每个会话的消息

        Args:
            max_scrolls_per_chat: 每个聊天的最大滚动次数
            save_to_db: 是否保存到数据库
            skip_sessions: 要跳过的会话名称列表

        Returns:
            {会话名: 消息列表} 字典
        """
        skip = set(skip_sessions or [])
        all_messages = {}

        # 获取会话列表
        sessions = self._main_page.get_session_list()
        logger.info(f"发现 {len(sessions)} 个会话")

        for i, session_name in enumerate(sessions):
            if session_name in skip:
                logger.debug(f"跳过会话: {session_name}")
                continue

            logger.info(f"[{i+1}/{len(sessions)}] 读取会话: {session_name}")

            try:
                messages = self.read_chat_messages(
                    session_name,
                    max_scrolls=max_scrolls_per_chat,
                    save_to_db=save_to_db,
                )
                all_messages[session_name] = messages
            except Exception as e:
                logger.error(f"读取会话 '{session_name}' 失败: {e}")
                all_messages[session_name] = []

            # 每个会话间等待，避免操作过快
            self._action.wait_stable(1.5)

        total = sum(len(msgs) for msgs in all_messages.values())
        logger.info(f"全部读取完成: {len(all_messages)} 个会话, {total} 条消息")
        return all_messages

    def read_specific_contacts(
        self,
        contact_names: list[str],
        max_scrolls: int = 50,
        save_to_db: bool = True,
    ) -> dict[str, list[ChatMessage]]:
        """
        读取指定联系人列表的消息

        Args:
            contact_names: 联系人名称列表
            max_scrolls: 每个聊天最大滚动次数
            save_to_db: 是否保存到数据库
        """
        results = {}
        for i, name in enumerate(contact_names):
            logger.info(f"[{i+1}/{len(contact_names)}] 读取: {name}")
            results[name] = self.read_chat_messages(
                name, max_scrolls=max_scrolls, save_to_db=save_to_db,
            )
            self._action.wait_stable(1.5)

        return results

    # ── 实时监控 ──

    def start_monitoring(
        self,
        contact_names: list[str] | None = None,
        interval: float = 5.0,
        on_new_message: Callable[[str, ChatMessage], None] | None = None,
    ):
        """
        启动实时消息监控

        通过定时轮询当前可见消息，与已知消息对比来检测新消息

        Args:
            contact_names: 要监控的联系人列表（None=监控当前聊天）
            interval: 轮询间隔（秒）
            on_new_message: 新消息回调函数 (contact_name, message) -> None
        """
        self._on_new_message = on_new_message
        self._monitoring = True

        def _monitor_loop():
            known_messages: dict[str, set[str]] = {}

            while self._monitoring:
                try:
                    if contact_names:
                        # 轮询多个联系人
                        for name in contact_names:
                            if not self._monitoring:
                                break
                            self._check_new_messages(name, known_messages)
                            time.sleep(1)
                    else:
                        # 仅监控当前聊天
                        title = self._chat_page.get_chat_title() or "当前聊天"
                        self._check_new_messages(title, known_messages, switch=False)

                except Exception as e:
                    logger.error(f"监控异常: {e}")

                time.sleep(interval)

        self._monitor_thread = threading.Thread(
            target=_monitor_loop, daemon=True, name="msg-monitor",
        )
        self._monitor_thread.start()
        logger.info(f"实时监控已启动 (间隔={interval}s)")

    def _check_new_messages(
        self,
        contact_name: str,
        known_messages: dict[str, set[str]],
        switch: bool = True,
    ):
        """检查某个聊天是否有新消息"""
        if switch:
            if not self._main_page.search_and_select(contact_name):
                return
            self._action.wait_stable(0.5)

        # 获取当前可见消息
        messages = self._chat_page.get_visible_messages()

        if contact_name not in known_messages:
            # 首次：记录所有已有消息
            known_messages[contact_name] = {
                f"{m.sender}:{m.content}" for m in messages
            }
            return

        # 对比找新消息
        known = known_messages[contact_name]
        for msg in messages:
            key = f"{msg.sender}:{msg.content}"
            if key not in known:
                known.add(key)
                logger.info(f"[新消息][{contact_name}] {msg}")

                # 保存到数据库
                self._storage.save_messages(contact_name, [msg])

                # 触发回调
                if self._on_new_message:
                    try:
                        self._on_new_message(contact_name, msg)
                    except Exception as e:
                        logger.error(f"消息回调异常: {e}")

    def stop_monitoring(self):
        """停止实时监控"""
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=10)
        logger.info("实时监控已停止")

    # ── 数据查询 ──

    def get_stored_messages(
        self,
        contact_name: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """查询已存储的消息"""
        return self._storage.query_messages(contact_name, limit)

    def export_messages(
        self,
        output_path: str,
        contact_name: str | None = None,
        format: str = "json",
    ) -> str:
        """导出消息"""
        return self._storage.export(output_path, contact_name, format)

    def get_stats(self) -> dict:
        """获取消息统计"""
        return self._storage.get_stats()

    def close(self):
        """关闭清理"""
        self.stop_monitoring()
        self._storage.close()
        logger.info("MessageReader 已关闭")
