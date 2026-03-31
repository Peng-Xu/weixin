"""
安全的 UI 操作执行器

功能:
  - 封装 click / type / scroll / hotkey 等操作
  - 自动随机延迟，模拟人类操作节奏
  - 操作前后自动截图（可选），用于调试和审计
  - 统一处理 UIA 控件对象和 (x,y) 坐标两种定位结果
"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

from loguru import logger

try:
    import uiautomation as auto
except ImportError:
    auto = None

try:
    import pyautogui
    pyautogui.FAILSAFE = True  # 鼠标移到左上角触发安全中断
    pyautogui.PAUSE = 0.1
except ImportError:
    pyautogui = None
    logger.warning("pyautogui 未安装, 坐标点击不可用: pip install pyautogui")


class ActionExecutor:
    """
    安全的 UI 操作执行器
    统一处理 UIA控件 和 (x,y)坐标 两种元素类型
    """

    def __init__(
        self,
        min_delay: float = 0.3,
        max_delay: float = 1.5,
        screenshot_dir: str | None = None,
    ):
        """
        Args:
            min_delay: 操作间最小延迟(秒)
            max_delay: 操作间最大延迟(秒)
            screenshot_dir: 截图保存目录（None则不截图）
        """
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        if self._screenshot_dir:
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    def _random_delay(self):
        """模拟人类操作的随机延迟"""
        delay = random.uniform(self.min_delay, self.max_delay)
        time.sleep(delay)

    def _is_uia_control(self, element) -> bool:
        """判断是否为UIA控件对象"""
        return hasattr(element, 'Exists') and hasattr(element, 'Click')

    def _is_coordinate(self, element) -> bool:
        """判断是否为坐标元组"""
        return isinstance(element, (tuple, list)) and len(element) == 2

    def click(self, element: Any, double: bool = False) -> bool:
        """
        点击元素

        Args:
            element: UIA控件对象 或 (x,y)坐标
            double: 是否双击
        """
        self._random_delay()
        try:
            if self._is_uia_control(element):
                if double:
                    element.DoubleClick()
                else:
                    element.Click()
                logger.debug(f"点击 UIA 控件: {getattr(element, 'Name', '?')}")
                return True

            elif self._is_coordinate(element) and pyautogui:
                x, y = element
                if double:
                    pyautogui.doubleClick(x, y)
                else:
                    pyautogui.click(x, y)
                logger.debug(f"点击坐标: ({x}, {y})")
                return True

            else:
                logger.error(f"无法点击未知类型的元素: {type(element)}")
                return False
        except Exception as e:
            logger.error(f"点击失败: {e}")
            return False

    def type_text(self, element: Any, text: str, clear_first: bool = True) -> bool:
        """
        在元素中输入文本

        Args:
            element: UIA控件 或 (x,y)坐标
            text: 要输入的文本
            clear_first: 是否先清空内容
        """
        self._random_delay()
        try:
            if self._is_uia_control(element):
                element.SetFocus()
                if clear_first:
                    # Ctrl+A 全选后删除
                    auto.SendKeys("{Ctrl}a")
                    time.sleep(0.1)
                    auto.SendKeys("{Delete}")
                    time.sleep(0.1)
                # 使用剪贴板输入中文
                self._type_via_clipboard(text)
                logger.debug(f"输入文本到 UIA 控件: {text[:20]}...")
                return True

            elif self._is_coordinate(element) and pyautogui:
                x, y = element
                pyautogui.click(x, y)
                time.sleep(0.2)
                if clear_first:
                    pyautogui.hotkey('ctrl', 'a')
                    time.sleep(0.1)
                    pyautogui.press('delete')
                    time.sleep(0.1)
                self._type_via_clipboard(text)
                logger.debug(f"输入文本到坐标 ({x},{y}): {text[:20]}...")
                return True

            else:
                logger.error(f"无法输入到未知类型的元素: {type(element)}")
                return False
        except Exception as e:
            logger.error(f"输入文本失败: {e}")
            return False

    def _type_via_clipboard(self, text: str):
        """
        通过剪贴板输入文本
        这是输入中文的最可靠方式（直接 SendKeys 不支持中文）
        """
        import ctypes
        import ctypes.wintypes

        # 将文本放入剪贴板
        CF_UNICODETEXT = 13
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        user32.OpenClipboard(0)
        user32.EmptyClipboard()

        # 分配全局内存
        hMem = kernel32.GlobalAlloc(0x0042, (len(text) + 1) * 2)
        pMem = kernel32.GlobalLock(hMem)
        ctypes.cdll.msvcrt.wcscpy(ctypes.c_wchar_p(pMem), text)
        kernel32.GlobalUnlock(hMem)

        user32.SetClipboardData(CF_UNICODETEXT, hMem)
        user32.CloseClipboard()

        # Ctrl+V 粘贴
        if auto:
            auto.SendKeys("{Ctrl}v")
        elif pyautogui:
            pyautogui.hotkey('ctrl', 'v')

        time.sleep(0.2)

    def send_keys(self, keys: str) -> bool:
        """
        发送按键序列

        Args:
            keys: 按键序列，使用 uiautomation 格式
                  如 "{Enter}", "{Ctrl}f", "{Ctrl}{Shift}v"
        """
        self._random_delay()
        try:
            if auto:
                auto.SendKeys(keys)
            elif pyautogui:
                # 简单转换 uiautomation 格式到 pyautogui
                key_map = {
                    "{Enter}": "enter", "{Tab}": "tab",
                    "{Escape}": "escape", "{Delete}": "delete",
                    "{Up}": "up", "{Down}": "down",
                    "{Left}": "left", "{Right}": "right",
                }
                if keys in key_map:
                    pyautogui.press(key_map[keys])
                else:
                    pyautogui.typewrite(keys)
            logger.debug(f"发送按键: {keys}")
            return True
        except Exception as e:
            logger.error(f"发送按键失败: {e}")
            return False

    def scroll(self, element: Any, clicks: int = -3) -> bool:
        """
        滚动操作

        Args:
            element: 滚动目标元素或坐标
            clicks: 滚动量（负数向下，正数向上）
        """
        self._random_delay()
        try:
            if self._is_uia_control(element):
                # UIA 原生滚动
                scroll_pattern = element.GetScrollPattern()
                if scroll_pattern:
                    if clicks < 0:
                        for _ in range(abs(clicks)):
                            scroll_pattern.Scroll(auto.ScrollAmount.NoAmount,
                                                  auto.ScrollAmount.SmallIncrement)
                    else:
                        for _ in range(clicks):
                            scroll_pattern.Scroll(auto.ScrollAmount.NoAmount,
                                                  auto.ScrollAmount.SmallDecrement)
                else:
                    # 回退: 鼠标滚轮
                    rect = element.BoundingRectangle
                    if rect and pyautogui:
                        cx = (rect.left + rect.right) // 2
                        cy = (rect.top + rect.bottom) // 2
                        pyautogui.scroll(clicks, cx, cy)
                return True

            elif self._is_coordinate(element) and pyautogui:
                x, y = element
                pyautogui.scroll(clicks, x, y)
                return True

        except Exception as e:
            logger.error(f"滚动失败: {e}")
        return False

    def screenshot(self, name: str = "screenshot") -> str | None:
        """
        截取当前屏幕或窗口截图

        Args:
            name: 截图文件名前缀

        Returns:
            截图文件路径 或 None
        """
        if not self._screenshot_dir:
            return None
        try:
            from PIL import ImageGrab
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filepath = self._screenshot_dir / f"{name}_{timestamp}.png"
            img = ImageGrab.grab()
            img.save(str(filepath))
            logger.debug(f"截图已保存: {filepath}")
            return str(filepath)
        except Exception as e:
            logger.error(f"截图失败: {e}")
            return None

    def wait_stable(self, seconds: float = 1.0):
        """等待UI稳定（加载完成）"""
        time.sleep(seconds)
