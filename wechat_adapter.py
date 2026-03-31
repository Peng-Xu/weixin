"""
微信适配器抽象层
统一 wcferry（3.9.x）和 Webhook（4.x）两种接入方式
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Message:
    """统一消息对象，替代 wcferry.WxMsg"""

    # 消息类型：1=文本, 3=图片, 34=语音, 43=视频, 49=链接/文件, ...
    type: int = 1
    # 消息正文
    content: str = ""
    # 发送方 wxid（私聊时即对方 wxid；群消息时为群成员 wxid）
    sender: str = ""
    # 发送方昵称（已解析，可能为空）
    sender_name: str = ""
    # 群 id（私聊时为空字符串）
    roomid: str = ""
    # 消息 id
    msg_id: str = ""
    # 是否 @ 了机器人（群消息有效）
    is_at_me: bool = False

    @property
    def is_group(self) -> bool:
        return bool(self.roomid)


class WechatAdapter(ABC):
    """
    微信适配器基类
    子类实现具体接入协议（wcferry / webhook）
    """

    # 消息回调：收到消息时由子类调用
    _on_message: Optional[Callable[[Message], None]] = None

    def set_message_callback(self, callback: Callable[[Message], None]):
        """注册消息处理回调"""
        self._on_message = callback

    # ── 生命周期 ──

    @abstractmethod
    def start(self) -> bool:
        """启动适配器，返回是否成功"""
        ...

    @abstractmethod
    def stop(self):
        """停止适配器"""
        ...

    @abstractmethod
    def is_login(self) -> bool:
        """微信是否已登录"""
        ...

    # ── 基础信息 ──

    @abstractmethod
    def get_self_wxid(self) -> str:
        """获取自己的 wxid"""
        ...

    @abstractmethod
    def get_contacts(self) -> list[dict]:
        """
        获取联系人列表
        每项至少包含 {"wxid": str, "name": str}
        """
        ...

    # ── 发送 ──

    @abstractmethod
    def send_text(self, content: str, receiver: str) -> bool:
        """
        发送文本消息
        receiver: wxid 或 roomid（群 id）
        """
        ...

    # ── 接收 ──

    @abstractmethod
    def enable_receiving(self):
        """开始接收消息（阻塞或异步，由子类决定）"""
        ...
