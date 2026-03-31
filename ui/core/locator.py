"""
多策略元素定位引擎

定位策略优先级:
  1. 快捷键 (最稳定，完全不依赖UI布局)
  2. UIA Name 属性 (按语义名称查找)
  3. UIA ControlType + 相对位置 (按控件类型 + 上下文)
  4. AI 视觉识别 (兜底，截图让AI定位)

每种策略失败后自动降级到下一种，成功的策略会被缓存以加速后续查找。
"""

from __future__ import annotations

import json
import time
import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Any

from loguru import logger

try:
    import uiautomation as auto
except ImportError:
    auto = None
    logger.warning("uiautomation 未安装, UIA 定位策略不可用: pip install uiautomation")


class LocateStrategy(enum.Enum):
    """定位策略类型"""
    HOTKEY = "hotkey"           # 快捷键
    UIA_NAME = "uia_name"      # UIA Name 属性
    UIA_AUTO_ID = "uia_auto_id"  # UIA AutomationId
    UIA_TYPE = "uia_type"      # UIA ControlType + 索引
    AI_VISION = "ai_vision"    # AI 视觉识别


@dataclass
class ElementSpec:
    """
    元素定位规格
    描述如何通过多种策略找到一个UI元素
    """
    name: str                   # 语义名称，如 "搜索框", "发送按钮"
    hotkey: str | None = None   # 快捷键，如 "{Ctrl}f"
    uia_name: str | None = None  # UIA Name 属性
    uia_auto_id: str | None = None  # UIA AutomationId
    uia_control_type: str | None = None  # 如 "EditControl", "ButtonControl"
    uia_found_index: int = 1    # 同类型控件的第几个 (1-based)
    uia_search_depth: int = 0   # 搜索深度 (0=不限)
    ai_description: str | None = None  # 给AI的描述，如 "微信搜索输入框"
    parent_spec: ElementSpec | None = None  # 父元素规格，用于缩小搜索范围
    timeout: float = 5.0        # 查找超时(秒)

    # 内部缓存字段
    _cached_strategy: LocateStrategy | None = field(default=None, repr=False)

    def __hash__(self):
        return hash(self.name)


class ElementLocator:
    """
    多策略元素定位器
    核心设计: 尝试多种策略定位元素，成功后缓存最佳策略
    """

    # 策略执行顺序
    STRATEGY_ORDER = [
        LocateStrategy.HOTKEY,
        LocateStrategy.UIA_AUTO_ID,
        LocateStrategy.UIA_NAME,
        LocateStrategy.UIA_TYPE,
        LocateStrategy.AI_VISION,
    ]

    def __init__(self, window_control=None, cache_file: str | None = None):
        """
        Args:
            window_control: 微信主窗口的 UIA 控件对象
            cache_file: 策略缓存文件路径（持久化成功的定位策略）
        """
        self._window = window_control
        self._strategy_cache: dict[str, LocateStrategy] = {}
        self._cache_file = Path(cache_file) if cache_file else None
        self._ai_locator: Callable | None = None  # AI视觉定位回调

        # 加载持久化缓存
        if self._cache_file and self._cache_file.exists():
            self._load_cache()

    def set_window(self, window_control):
        """设置/更新微信窗口控件"""
        self._window = window_control

    def set_ai_locator(self, func: Callable):
        """
        注册AI视觉定位函数
        func 签名: (description: str) -> tuple[int, int] | None
        返回屏幕坐标 (x, y) 或 None
        """
        self._ai_locator = func

    def find(self, spec: ElementSpec) -> Any | None:
        """
        查找元素，自动使用最佳策略

        Args:
            spec: 元素定位规格

        Returns:
            UIA控件对象 或 (x,y)坐标元组(AI视觉) 或 None
        """
        # 优先使用缓存的策略
        cached = self._strategy_cache.get(spec.name) or spec._cached_strategy
        if cached:
            result = self._try_strategy(cached, spec)
            if result is not None:
                return result
            # 缓存失效，清除
            logger.warning(f"[{spec.name}] 缓存策略 {cached.value} 失效, 尝试其他策略")
            self._strategy_cache.pop(spec.name, None)
            spec._cached_strategy = None

        # 按优先级尝试所有策略
        for strategy in self.STRATEGY_ORDER:
            result = self._try_strategy(strategy, spec)
            if result is not None:
                # 缓存成功策略
                self._strategy_cache[spec.name] = strategy
                spec._cached_strategy = strategy
                self._save_cache()
                logger.info(f"[{spec.name}] 使用策略 {strategy.value} 定位成功")
                return result

        logger.error(f"[{spec.name}] 所有定位策略均失败")
        return None

    def _try_strategy(self, strategy: LocateStrategy, spec: ElementSpec) -> Any | None:
        """尝试单个策略"""
        try:
            if strategy == LocateStrategy.HOTKEY:
                return self._locate_by_hotkey(spec)
            elif strategy == LocateStrategy.UIA_AUTO_ID:
                return self._locate_by_auto_id(spec)
            elif strategy == LocateStrategy.UIA_NAME:
                return self._locate_by_uia_name(spec)
            elif strategy == LocateStrategy.UIA_TYPE:
                return self._locate_by_uia_type(spec)
            elif strategy == LocateStrategy.AI_VISION:
                return self._locate_by_ai_vision(spec)
        except Exception as e:
            logger.debug(f"[{spec.name}] 策略 {strategy.value} 异常: {e}")
        return None

    def _locate_by_hotkey(self, spec: ElementSpec):
        """通过快捷键激活元素"""
        if not spec.hotkey or auto is None:
            return None
        if self._window:
            self._window.SetFocus()
        auto.SendKeys(spec.hotkey)
        time.sleep(0.3)
        # 快捷键后，获取当前焦点控件
        focused = auto.GetFocusedControl()
        if focused and focused.Exists(0.5):
            return focused
        return None

    def _locate_by_auto_id(self, spec: ElementSpec):
        """通过 AutomationId 定位"""
        if not spec.uia_auto_id or auto is None or not self._window:
            return None
        control = self._window.Control(
            AutomationId=spec.uia_auto_id,
            searchDepth=spec.uia_search_depth or 10,
        )
        if control.Exists(spec.timeout):
            return control
        return None

    def _locate_by_uia_name(self, spec: ElementSpec):
        """通过 UIA Name 属性定位"""
        if not spec.uia_name or auto is None or not self._window:
            return None

        search_root = self._window
        if spec.parent_spec:
            parent = self.find(spec.parent_spec)
            if parent and hasattr(parent, 'Exists'):
                search_root = parent

        # 根据 ControlType 选择对应的查找方法
        control_type = spec.uia_control_type or "Control"
        find_method = getattr(search_root, control_type, None)
        if find_method is None:
            find_method = search_root.Control

        kwargs = {"Name": spec.uia_name}
        if spec.uia_search_depth:
            kwargs["searchDepth"] = spec.uia_search_depth
        if spec.uia_found_index > 1:
            kwargs["foundIndex"] = spec.uia_found_index

        control = find_method(**kwargs)
        if control.Exists(spec.timeout):
            return control
        return None

    def _locate_by_uia_type(self, spec: ElementSpec):
        """通过 ControlType + 索引定位"""
        if not spec.uia_control_type or auto is None or not self._window:
            return None

        search_root = self._window
        find_method = getattr(search_root, spec.uia_control_type, None)
        if find_method is None:
            return None

        kwargs = {"foundIndex": spec.uia_found_index}
        if spec.uia_search_depth:
            kwargs["searchDepth"] = spec.uia_search_depth

        control = find_method(**kwargs)
        if control.Exists(spec.timeout):
            return control
        return None

    def _locate_by_ai_vision(self, spec: ElementSpec):
        """通过AI视觉识别定位"""
        if not self._ai_locator or not spec.ai_description:
            return None
        result = self._ai_locator(spec.ai_description)
        if result and isinstance(result, (tuple, list)) and len(result) == 2:
            return tuple(result)  # 返回 (x, y) 坐标
        return None

    def _load_cache(self):
        """从文件加载策略缓存"""
        try:
            data = json.loads(self._cache_file.read_text(encoding="utf-8"))
            for name, strategy_val in data.items():
                try:
                    self._strategy_cache[name] = LocateStrategy(strategy_val)
                except ValueError:
                    pass
            logger.info(f"已加载 {len(self._strategy_cache)} 条策略缓存")
        except Exception as e:
            logger.debug(f"加载策略缓存失败: {e}")

    def _save_cache(self):
        """持久化策略缓存"""
        if not self._cache_file:
            return
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            data = {name: s.value for name, s in self._strategy_cache.items()}
            self._cache_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"保存策略缓存失败: {e}")

    def clear_cache(self, element_name: str | None = None):
        """清除策略缓存"""
        if element_name:
            self._strategy_cache.pop(element_name, None)
        else:
            self._strategy_cache.clear()
        self._save_cache()
