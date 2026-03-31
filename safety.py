"""
安全限流模块
防止消息回复过于频繁触发微信风控
"""

import time
from collections import defaultdict
from loguru import logger


class RateLimiter:
    """消息频率限制器"""

    def __init__(self, config: dict):
        safety = config.get("safety", {})
        self.min_interval = safety.get("min_reply_interval", 2)
        self.max_per_minute = safety.get("max_replies_per_minute", 15)
        self.whitelist = set(safety.get("whitelist_friends", []))
        self.blacklist = set(safety.get("blacklist_friends", []))
        self.reply_strangers = safety.get("reply_strangers", False)

        # 记录每个联系人的最后回复时间
        self._last_reply: dict[str, float] = {}
        # 记录每分钟回复计数（全局）
        self._minute_counter: list[float] = []

    def should_reply(self, sender_name: str, is_friend: bool) -> tuple[bool, str]:
        """
        判断是否应该回复
        返回: (是否回复, 拒绝原因)
        """
        # 黑名单检查
        if sender_name in self.blacklist:
            return False, "黑名单"

        # 白名单检查（白名单非空时，仅白名单可触发）
        if self.whitelist and sender_name not in self.whitelist:
            return False, "不在白名单"

        # 陌生人检查
        if not is_friend and not self.reply_strangers:
            return False, "陌生人"

        now = time.time()

        # 最小间隔检查
        if sender_name in self._last_reply:
            elapsed = now - self._last_reply[sender_name]
            if elapsed < self.min_interval:
                return False, f"消息间隔过短 ({elapsed:.1f}s < {self.min_interval}s)"

        # 每分钟总量检查
        self._minute_counter = [t for t in self._minute_counter if now - t < 60]
        if len(self._minute_counter) >= self.max_per_minute:
            return False, f"每分钟回复已达上限 ({self.max_per_minute})"

        return True, ""

    def record_reply(self, sender_name: str):
        """记录一次回复"""
        now = time.time()
        self._last_reply[sender_name] = now
        self._minute_counter.append(now)

    def wait_if_needed(self, sender_name: str):
        """如果距上次回复太近，等待到最小间隔"""
        if sender_name in self._last_reply:
            elapsed = time.time() - self._last_reply[sender_name]
            if elapsed < self.min_interval:
                wait_time = self.min_interval - elapsed
                logger.debug(f"限流等待 {wait_time:.1f}s")
                time.sleep(wait_time)
