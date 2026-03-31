"""
自愈引擎

当元素定位失败时：
1. 自动尝试降级策略
2. 记录失败信息用于后续分析
3. 缓存新发现的有效策略
4. 可选: 截图 + AI 重新分析
"""

from __future__ import annotations

import time
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class FailureRecord:
    """定位失败记录"""
    element_name: str
    strategy: str
    error: str
    timestamp: float = field(default_factory=time.time)
    screenshot_path: str | None = None


class SelfHealingEngine:
    """
    自愈引擎
    跟踪定位失败，提供自动修复建议
    """

    def __init__(self, log_dir: str = "logs/healing"):
        self._failures: list[FailureRecord] = []
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def record_failure(
        self,
        element_name: str,
        strategy: str,
        error: str,
        screenshot_path: str | None = None,
    ):
        """记录一次定位失败"""
        record = FailureRecord(
            element_name=element_name,
            strategy=strategy,
            error=error,
            screenshot_path=screenshot_path,
        )
        self._failures.append(record)
        logger.warning(
            f"[自愈] 元素 '{element_name}' 策略 '{strategy}' 失败: {error}"
        )

    def get_failure_stats(self) -> dict[str, int]:
        """获取各元素的失败次数统计"""
        stats: dict[str, int] = {}
        for f in self._failures:
            stats[f.element_name] = stats.get(f.element_name, 0) + 1
        return stats

    def get_recent_failures(self, element_name: str, limit: int = 5) -> list[FailureRecord]:
        """获取某元素最近的失败记录"""
        records = [f for f in self._failures if f.element_name == element_name]
        return records[-limit:]

    def should_skip_strategy(self, element_name: str, strategy: str) -> bool:
        """
        判断是否应该跳过某策略
        如果同一元素的同一策略连续失败3次以上，暂时跳过
        """
        recent = [
            f for f in self._failures[-20:]
            if f.element_name == element_name and f.strategy == strategy
        ]
        return len(recent) >= 3

    def save_report(self):
        """保存失败报告到文件"""
        if not self._failures:
            return
        report = {
            "total_failures": len(self._failures),
            "stats": self.get_failure_stats(),
            "recent_failures": [
                {
                    "element": f.element_name,
                    "strategy": f.strategy,
                    "error": f.error,
                    "time": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(f.timestamp)
                    ),
                }
                for f in self._failures[-50:]
            ],
        }
        report_path = self._log_dir / "healing_report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"自愈报告已保存: {report_path}")

    def clear(self):
        """清除所有失败记录"""
        self._failures.clear()
