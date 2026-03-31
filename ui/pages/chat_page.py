"""
聊天页面 Page Object

封装单个聊天窗口内的所有 UI 操作:
  - 读取消息列表
  - 滚动加载历史消息
  - 解析消息内容（发送者、时间、文本）
  - 输入并发送消息
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from loguru import logger

try:
    import uiautomation as auto
except ImportError:
    auto = None

from ..core.locator import ElementLocator, ElementSpec
from ..core.actions import ActionExecutor


# ── 元素定义 ──

ELEMENTS = {
    "chat_title": ElementSpec(
        name="聊天标题",
        uia_control_type="TextControl",
        uia_found_index=1,
        uia_search_depth=5,
        ai_description="当前聊天窗口顶部的联系人/群名称",
    ),
    "message_list": ElementSpec(
        name="消息列表",
        uia_name="消息",
        uia_control_type="ListControl",
        ai_description="聊天窗口中的消息列表区域",
    ),
    "input_box": ElementSpec(
        name="输入框",
        uia_name="输入",
        uia_control_type="EditControl",
        ai_description="聊天窗口底部的消息输入框",
    ),
    "send_button": ElementSpec(
        name="发送按钮",
        uia_name="发送(S)",
        uia_control_type="ButtonControl",
        ai_description="聊天窗口中的发送按钮",
    ),
}


@dataclass
class ChatMessage:
    """聊天消息数据结构"""
    sender: str       # 发送者名称
    content: str      # 消息文本内容
    time: str = ""    # 时间戳（如果能获取到）
    msg_type: str = "text"  # 消息类型: text, image, file, system, etc.
    raw_name: str = ""  # 原始控件名称（调试用）

    def __str__(self):
        time_str = f"[{self.time}] " if self.time else ""
        return f"{time_str}{self.sender}: {self.content}"

    def to_dict(self) -> dict:
        return {
            "sender": self.sender,
            "content": self.content,
            "time": self.time,
            "msg_type": self.msg_type,
        }


class ChatPage:
    """聊天页面 Page Object"""

    def __init__(self, locator: ElementLocator, action: ActionExecutor, window=None):
        self._locator = locator
        self._action = action
        self._window = window

    def set_window(self, window):
        self._window = window

    def get_chat_title(self) -> str:
        """获取当前聊天窗口的标题（对方名称/群名）"""
        if auto is None or not self._window:
            return ""
        try:
            # 标题通常在聊天区域顶部
            title = self._locator.find(ELEMENTS["chat_title"])
            if title and hasattr(title, 'Name'):
                return title.Name.strip()
        except Exception as e:
            logger.debug(f"获取聊天标题失败: {e}")
        return ""

    def get_visible_messages(self) -> list[ChatMessage]:
        """
        获取当前可见的所有消息

        Returns:
            ChatMessage 列表
        """
        if auto is None or not self._window:
            return []

        messages = []
        msg_list = self._locator.find(ELEMENTS["message_list"])
        if not msg_list or not hasattr(msg_list, 'GetChildren'):
            logger.warning("未找到消息列表控件")
            return []

        children = msg_list.GetChildren()
        for child in children:
            msg = self._parse_message_item(child)
            if msg:
                messages.append(msg)

        return messages

    def _parse_message_item(self, item) -> ChatMessage | None:
        """
        解析单个消息控件

        微信消息控件的结构（参考，不同版本可能不同）:
          ListItem
            ├─ Button (头像, Name=发送者名称)
            ├─ Text (消息内容)
            └─ ...

        策略: 多种解析方式降级
        """
        try:
            item_name = getattr(item, 'Name', '').strip()

            # 跳过空项
            if not item_name:
                return None

            # ── 策略 1: 从 Name 属性直接解析 ──
            # 微信某些版本的 ListItem.Name 格式为 "发送者名\n消息内容"
            if '\n' in item_name:
                parts = item_name.split('\n', 1)
                return ChatMessage(
                    sender=parts[0].strip(),
                    content=parts[1].strip(),
                    raw_name=item_name,
                )

            # ── 策略 2: 遍历子控件提取信息 ──
            sender = ""
            content = ""
            msg_type = "text"

            sub_controls = item.GetChildren()
            for ctrl in sub_controls:
                ctrl_type = getattr(ctrl, 'ControlTypeName', '')
                ctrl_name = getattr(ctrl, 'Name', '').strip()

                if ctrl_type == 'ButtonControl' and ctrl_name:
                    # 头像按钮通常包含发送者名称
                    sender = ctrl_name
                elif ctrl_type == 'TextControl' and ctrl_name:
                    content = ctrl_name
                elif ctrl_type == 'EditControl' and ctrl_name:
                    content = ctrl_name
                elif ctrl_type == 'PaneControl':
                    # 可能是图片/文件等复杂消息
                    inner_text = self._extract_pane_text(ctrl)
                    if inner_text:
                        content = inner_text
                        msg_type = "rich"

            if content:
                if not sender:
                    # 某些情况下 item_name 就是发送者
                    sender = item_name if item_name != content else "未知"
                return ChatMessage(
                    sender=sender,
                    content=content,
                    msg_type=msg_type,
                    raw_name=item_name,
                )

            # ── 策略 3: 整个 Name 作为系统消息 ──
            # 时间戳、撤回提示等系统消息
            if self._is_system_message(item_name):
                return ChatMessage(
                    sender="[系统]",
                    content=item_name,
                    msg_type="system",
                    raw_name=item_name,
                )

            # 兜底: 无法解析的当作文本
            return ChatMessage(
                sender="未知",
                content=item_name,
                raw_name=item_name,
            )

        except Exception as e:
            logger.debug(f"解析消息失败: {e}")
            return None

    def _extract_pane_text(self, pane_ctrl) -> str:
        """递归提取 Pane 控件内的文本"""
        texts = []
        try:
            for child in pane_ctrl.GetChildren():
                name = getattr(child, 'Name', '').strip()
                if name:
                    texts.append(name)
                # 递归一层
                for sub in child.GetChildren():
                    sub_name = getattr(sub, 'Name', '').strip()
                    if sub_name:
                        texts.append(sub_name)
        except Exception:
            pass
        return ' '.join(texts)

    def _is_system_message(self, text: str) -> bool:
        """判断是否为系统消息"""
        system_patterns = [
            r'^\d{1,2}:\d{2}$',           # 时间 "14:30"
            r'^\d{4}年\d{1,2}月\d{1,2}日',  # 日期
            r'撤回了一条消息',
            r'加入了群聊',
            r'修改了群名',
            r'以上是打招呼的内容',
            r'你已添加',
            r'拍了拍',
        ]
        return any(re.search(p, text) for p in system_patterns)

    def scroll_up(self, times: int = 3) -> bool:
        """
        向上滚动加载更多历史消息

        Args:
            times: 滚动次数

        Returns:
            是否成功滚动
        """
        msg_list = self._locator.find(ELEMENTS["message_list"])
        if not msg_list:
            return False

        for i in range(times):
            self._action.scroll(msg_list, clicks=5)  # 正数=向上
            self._action.wait_stable(0.8)

        return True

    def scroll_to_top(self, max_scrolls: int = 50) -> int:
        """
        持续向上滚动直到无法加载更多消息

        Args:
            max_scrolls: 最大滚动次数

        Returns:
            实际滚动次数
        """
        msg_list = self._locator.find(ELEMENTS["message_list"])
        if not msg_list:
            return 0

        prev_count = 0
        no_change_count = 0
        scroll_count = 0

        for i in range(max_scrolls):
            # 获取当前消息数
            try:
                current_count = len(msg_list.GetChildren())
            except Exception:
                current_count = 0

            if current_count == prev_count:
                no_change_count += 1
                if no_change_count >= 3:
                    # 连续3次滚动后消息数不变，说明已到顶
                    logger.info(f"已滚动到顶部 (共 {current_count} 条消息)")
                    break
            else:
                no_change_count = 0

            prev_count = current_count
            self._action.scroll(msg_list, clicks=5)
            self._action.wait_stable(1.0)
            scroll_count += 1

        return scroll_count

    def send_message(self, text: str) -> bool:
        """
        发送文本消息

        Args:
            text: 要发送的文本

        Returns:
            是否发送成功
        """
        # 定位输入框
        input_box = self._locator.find(ELEMENTS["input_box"])
        if not input_box:
            logger.error("未找到消息输入框")
            return False

        # 输入文本
        self._action.type_text(input_box, text)
        self._action.wait_stable(0.3)

        # 按 Enter 发送
        self._action.send_keys("{Enter}")
        logger.info(f"消息已发送: {text[:30]}...")
        return True

    def get_all_messages_by_scrolling(self, max_scrolls: int = 50) -> list[ChatMessage]:
        """
        通过滚动加载获取所有历史消息

        先滚动到顶部，再逐步向下收集所有消息，自动去重

        Args:
            max_scrolls: 最大向上滚动次数

        Returns:
            去重后的所有消息
        """
        logger.info("开始滚动加载历史消息...")

        # 1. 先收集当前可见消息
        all_seen: dict[str, ChatMessage] = {}

        def _collect():
            for msg in self.get_visible_messages():
                key = f"{msg.sender}:{msg.content}"
                if key not in all_seen:
                    all_seen[key] = msg

        _collect()

        # 2. 滚动到顶部，边滚边收集
        msg_list = self._locator.find(ELEMENTS["message_list"])
        if not msg_list:
            return list(all_seen.values())

        prev_count = len(all_seen)
        no_change_count = 0

        for i in range(max_scrolls):
            self._action.scroll(msg_list, clicks=5)  # 向上
            self._action.wait_stable(1.0)
            _collect()

            current_count = len(all_seen)
            if current_count == prev_count:
                no_change_count += 1
                if no_change_count >= 3:
                    break
            else:
                no_change_count = 0
            prev_count = current_count
            logger.debug(f"滚动 #{i+1}, 已收集 {current_count} 条消息")

        # 3. 再滚回底部收集剩余
        for _ in range(max_scrolls):
            self._action.scroll(msg_list, clicks=-5)  # 向下
            self._action.wait_stable(0.5)
            old_count = len(all_seen)
            _collect()
            if len(all_seen) == old_count:
                no_change_count += 1
                if no_change_count >= 3:
                    break
            else:
                no_change_count = 0

        logger.info(f"历史消息加载完成, 共 {len(all_seen)} 条")
        return list(all_seen.values())
