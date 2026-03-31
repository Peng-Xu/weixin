"""
微信主窗口 Page Object

封装微信主界面的所有 UI 操作:
  - 绑定/激活微信窗口
  - 搜索联系人/群聊
  - 切换到指定聊天
  - 获取会话列表
"""

from __future__ import annotations

import time
from loguru import logger

try:
    import uiautomation as auto
except ImportError:
    auto = None

from ..core.locator import ElementLocator, ElementSpec, LocateStrategy
from ..core.actions import ActionExecutor


# ── 元素定义 ──
# 集中定义所有元素的多策略定位规格
# UI变化时只需修改这里

ELEMENTS = {
    "main_window": ElementSpec(
        name="微信主窗口",
        uia_name="微信",
        uia_control_type="WindowControl",
        ai_description="微信桌面版主窗口",
        timeout=10.0,
    ),
    "search_box": ElementSpec(
        name="搜索框",
        hotkey="{Ctrl}f",
        uia_name="搜索",
        uia_control_type="EditControl",
        ai_description="微信顶部的搜索输入框",
    ),
    "session_list": ElementSpec(
        name="会话列表",
        uia_name="会话",
        uia_control_type="ListControl",
        ai_description="微信左侧的聊天会话列表",
    ),
    "contact_list_btn": ElementSpec(
        name="通讯录按钮",
        uia_name="通讯录",
        uia_control_type="ButtonControl",
        ai_description="微信左侧导航栏的通讯录按钮",
    ),
}


class WeChatMainPage:
    """微信主窗口 Page Object"""

    # 微信窗口的已知 ClassName（不同版本可能不同）
    KNOWN_CLASS_NAMES = [
        "WeChatMainWndForPC",   # 微信 3.x
        "WeChatMainWnd",        # 微信 4.x 可能的类名
    ]

    def __init__(self, locator: ElementLocator, action: ActionExecutor):
        self._locator = locator
        self._action = action
        self._window = None

    def attach(self) -> bool:
        """
        绑定微信主窗口

        Returns:
            是否成功找到并绑定
        """
        if auto is None:
            logger.error("uiautomation 未安装")
            return False

        # 尝试多种 ClassName
        for class_name in self.KNOWN_CLASS_NAMES:
            try:
                window = auto.WindowControl(
                    ClassName=class_name,
                    searchDepth=1,
                )
                if window.Exists(3):
                    self._window = window
                    self._locator.set_window(window)
                    logger.info(f"已绑定微信窗口 (ClassName={class_name})")
                    return True
            except Exception:
                continue

        # 降级: 按窗口标题查找
        try:
            window = auto.WindowControl(Name="微信", searchDepth=1)
            if window.Exists(3):
                self._window = window
                self._locator.set_window(window)
                logger.info("已绑定微信窗口 (Name=微信)")
                return True
        except Exception:
            pass

        logger.error("未找到微信窗口, 请确认微信已打开并登录")
        return False

    def activate(self) -> bool:
        """激活(置顶)微信窗口"""
        if not self._window:
            return False
        try:
            self._window.SetActive()
            self._window.SetFocus()
            time.sleep(0.3)
            return True
        except Exception as e:
            logger.error(f"激活窗口失败: {e}")
            return False

    def search_and_select(self, keyword: str) -> bool:
        """
        搜索并选择联系人/群聊

        Args:
            keyword: 搜索关键词（联系人名/群名）

        Returns:
            是否成功找到并选中
        """
        if not self.activate():
            return False

        # 1. 打开搜索框
        search_box = self._locator.find(ELEMENTS["search_box"])
        if not search_box:
            logger.error("无法打开搜索框")
            return False

        # 2. 输入关键词
        self._action.type_text(search_box, keyword)
        self._action.wait_stable(1.0)

        # 3. 点击搜索结果中的第一个匹配项
        result_item = self._find_search_result(keyword)
        if result_item:
            self._action.click(result_item)
            self._action.wait_stable(0.5)
            # 按 Escape 关闭搜索面板（保留在选中的聊天）
            self._action.send_keys("{Escape}")
            time.sleep(0.3)
            logger.info(f"已切换到聊天: {keyword}")
            return True

        # 搜索失败，关闭搜索面板
        self._action.send_keys("{Escape}")
        logger.warning(f"搜索结果中未找到: {keyword}")
        return False

    def _find_search_result(self, keyword: str):
        """在搜索结果中查找匹配项"""
        if auto is None or not self._window:
            return None

        # 策略1: 按 Name 精确匹配
        try:
            item = self._window.ListItemControl(Name=keyword)
            if item.Exists(3):
                return item
        except Exception:
            pass

        # 策略2: 模糊匹配 - 遍历搜索结果列表
        try:
            # 搜索结果可能在一个单独的列表控件中
            search_results = self._window.ListControl(Name="搜索结果")
            if not search_results.Exists(2):
                search_results = self._window.ListControl(Name="@str:search_result")
            if not search_results.Exists(2):
                # 尝试获取所有 List 控件中的第二个（第一个是会话列表）
                search_results = self._window.ListControl(foundIndex=2)

            if search_results.Exists(2):
                children = search_results.GetChildren()
                for child in children:
                    child_name = getattr(child, 'Name', '')
                    if keyword in child_name:
                        return child
                # 没有精确匹配，取第一个结果
                if children:
                    return children[0]
        except Exception as e:
            logger.debug(f"搜索结果遍历失败: {e}")

        # 策略3: 直接取搜索后的第一个 ListItem
        try:
            first_item = self._window.ListItemControl(foundIndex=1)
            if first_item.Exists(2):
                return first_item
        except Exception:
            pass

        return None

    def get_session_list(self) -> list[str]:
        """
        获取当前可见的会话列表名称

        Returns:
            会话名称列表
        """
        if auto is None or not self._window:
            return []

        self.activate()
        sessions = []

        try:
            session_list = self._locator.find(ELEMENTS["session_list"])
            if session_list and hasattr(session_list, 'GetChildren'):
                children = session_list.GetChildren()
                for child in children:
                    name = getattr(child, 'Name', '').strip()
                    if name:
                        sessions.append(name)
        except Exception as e:
            logger.error(f"获取会话列表失败: {e}")

        return sessions

    @property
    def window(self):
        return self._window
