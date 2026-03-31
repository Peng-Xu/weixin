"""
核心消息处理引擎
接收消息回调，分发到各处理模块
适配器无关：同时支持 wcferry（3.9.x）和 Webhook（4.x）
"""

import re
from loguru import logger

from ai_chat import AIChat
from safety import RateLimiter
from wechat_adapter import Message, WechatAdapter


class MessageHandler:
    """消息处理器（适配器无关）"""

    def __init__(self, config: dict, adapter: WechatAdapter, ai: AIChat, limiter: RateLimiter):
        self.config = config
        self.adapter = adapter
        self.ai = ai
        self.limiter = limiter
        self.self_wxid = ""  # 自己的 wxid，启动后设置

        # 缓存联系人和群信息
        self._contacts: dict[str, dict] = {}
        self._rooms: dict[str, dict] = {}

    def set_self_wxid(self, wxid: str):
        self.self_wxid = wxid

    def refresh_contacts(self):
        """刷新联系人缓存"""
        try:
            contacts = self.adapter.get_contacts()
            self._contacts = {c["wxid"]: c for c in contacts}
            logger.info(f"已缓存 {len(self._contacts)} 个联系人")
        except Exception as e:
            logger.error(f"刷新联系人失败: {e}")

    def get_contact_name(self, wxid: str) -> str:
        """获取联系人昵称"""
        if wxid in self._contacts:
            return self._contacts[wxid].get("name", wxid)
        return wxid

    def is_group_msg(self, msg: Message) -> bool:
        """判断是否是群消息"""
        return msg.is_group

    def is_at_me(self, msg: Message) -> bool:
        """判断群消息是否 @了我"""
        # webhook 模式下 is_at_me 由后端直接设置
        if msg.is_at_me:
            return True
        # wcferry 兜底：检查消息内容是否含 @自己昵称
        if not self.self_wxid:
            return False
        my_name = self.get_contact_name(self.self_wxid)
        return f"@{my_name}" in msg.content

    def extract_message_text(self, msg: Message) -> str:
        """
        提取消息文本（去掉 @XXX 前缀）
        """
        text = msg.content.strip()
        if self.is_group_msg(msg) and self.self_wxid:
            my_name = self.get_contact_name(self.self_wxid)
            text = re.sub(rf"@{re.escape(my_name)}\s*", "", text).strip()
        return text

    def handle_message(self, msg: Message):
        """
        消息处理主入口
        """
        # 忽略自己发的消息
        if msg.sender == self.self_wxid:
            return

        # 只处理文本消息（type=1）
        if msg.type != 1:
            return

        sender_wxid = msg.sender
        sender_name = self.get_contact_name(sender_wxid)
        text = msg.content.strip()
        is_group = self.is_group_msg(msg)

        if is_group:
            room_name = self.get_contact_name(msg.roomid)
            logger.info(f"[群:{room_name}] {sender_name}: {text[:50]}")
        else:
            logger.info(f"[私聊] {sender_name}: {text[:50]}")

        # ── 特殊命令 ──
        if text.lower() in ("/reset", "重置对话", "清除记忆"):
            contact_id = msg.roomid if is_group else sender_wxid
            self.ai.reset_context(contact_id)
            self._reply(msg, "对话已重置，让我们重新开始吧！")
            return

        # ── 群消息处理 ──
        if is_group:
            self._handle_group_message(msg, sender_name, text)
            return

        # ── 私聊消息处理 ──
        self._handle_private_message(msg, sender_name, text)

    def _handle_private_message(self, msg: Message, sender_name: str, text: str):
        """处理私聊消息"""
        sender_wxid = msg.sender

        # 安全检查
        can_reply, reason = self.limiter.should_reply(sender_name, is_friend=True)
        if not can_reply:
            logger.debug(f"跳过回复 {sender_name}: {reason}")
            return

        # 1. 关键词自动回复
        if self.config["auto_reply"]["enabled"]:
            reply = self._match_keyword(text)
            if reply:
                self.limiter.wait_if_needed(sender_name)
                self._reply(msg, reply)
                self.limiter.record_reply(sender_name)
                return

        # 2. AI 回复
        if self.ai.provider != "none":
            self.limiter.wait_if_needed(sender_name)
            ai_reply = self.ai.chat(sender_wxid, text)
            if ai_reply:
                self._reply(msg, ai_reply)
                self.limiter.record_reply(sender_name)

    def _handle_group_message(self, msg: Message, sender_name: str, text: str):
        """处理群消息"""
        room_name = self.get_contact_name(msg.roomid)
        group_config = self.config["group"]

        # 检查是否在启用群列表中
        enabled_groups = group_config.get("enabled_groups", [])
        if enabled_groups and room_name not in enabled_groups:
            return

        # 安全检查
        can_reply, reason = self.limiter.should_reply(sender_name, is_friend=True)
        if not can_reply:
            logger.debug(f"跳过群回复 {sender_name}: {reason}")
            return

        # 1. 关键词自动回复（群里也生效）
        if self.config["auto_reply"]["enabled"]:
            reply = self._match_keyword(text)
            if reply:
                self.limiter.wait_if_needed(sender_name)
                self._reply(msg, reply)
                self.limiter.record_reply(sender_name)
                return

        # 2. 群 AI 回复
        if not group_config.get("ai_reply", False):
            return

        trigger = group_config.get("ai_trigger", "at")
        should_ai = False
        ai_text = text

        if trigger == "at":
            # 需要 @机器人
            if self.is_at_me(msg):
                should_ai = True
                ai_text = self.extract_message_text(msg)
        elif trigger == "prefix":
            # 需要特定前缀
            prefix = group_config.get("ai_prefix", "/ai ")
            if text.startswith(prefix):
                should_ai = True
                ai_text = text[len(prefix):].strip()
        elif trigger == "all":
            should_ai = True

        if should_ai and ai_text and self.ai.provider != "none":
            self.limiter.wait_if_needed(sender_name)
            # 群消息用 roomid 作为上下文 key（共享群上下文）
            ai_reply = self.ai.chat(msg.roomid, ai_text)
            if ai_reply:
                # 群里回复时 @发送者
                self._reply(msg, f"@{sender_name} {ai_reply}")
                self.limiter.record_reply(sender_name)

    def _match_keyword(self, text: str) -> str:
        """匹配关键词规则，返回回复内容"""
        rules = self.config["auto_reply"].get("rules", [])
        for rule in rules:
            keyword = rule.get("keyword", "")
            if keyword and keyword in text:
                return rule.get("reply", "")
        return ""

    def _reply(self, msg: Message, content: str):
        """回复消息"""
        receiver = msg.roomid if self.is_group_msg(msg) else msg.sender
        self.adapter.send_text(content, receiver)

    def send_to(self, target_type: str, target_name: str, message: str) -> bool:
        """
        向指定目标发送消息（供定时任务使用）
        target_type: "friend" 或 "group"
        target_name: 好友昵称或群名
        """
        wxid = self._find_wxid(target_type, target_name)
        if not wxid:
            logger.error(f"找不到目标: {target_type}:{target_name}")
            return False

        ok = self.adapter.send_text(message, wxid)
        if ok:
            logger.info(f"定时消息已发送: {target_type}:{target_name}")
        else:
            logger.error(f"定时消息发送失败: {target_type}:{target_name}")
        return ok

    def _find_wxid(self, target_type: str, target_name: str) -> str:
        """根据昵称查找 wxid"""
        for wxid, info in self._contacts.items():
            name = info.get("name", "")
            if name == target_name:
                if target_type == "friend" and not wxid.endswith("@chatroom"):
                    return wxid
                if target_type == "group" and wxid.endswith("@chatroom"):
                    return wxid
        return ""
