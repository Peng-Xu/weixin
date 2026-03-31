"""
核心消息处理引擎
接收 WeChatFerry 的消息回调，分发到各处理模块
支持微信 3.9.x 和 4.x
"""

import re
import xml.etree.ElementTree as ET
from loguru import logger
from wcferry import WxMsg

from ai_chat import AIChat
from safety import RateLimiter


class MessageHandler:
    """消息处理器"""

    def __init__(self, config: dict, wcf, ai: AIChat, limiter: RateLimiter):
        self.config = config
        self.wcf = wcf
        self.ai = ai
        self.limiter = limiter
        self.self_wxid = ""  # 自己的 wxid，启动后设置

        # 缓存联系人和群信息
        self._contacts: dict[str, dict] = {}
        self._rooms: dict[str, dict] = {}

    def set_self_wxid(self, wxid: str):
        self.self_wxid = wxid

    def refresh_contacts(self):
        """刷新联系人缓存（兼容不同 wcferry 版本的返回格式）"""
        try:
            contacts = self.wcf.get_contacts()
            self._contacts = {}
            for c in contacts:
                # 兼容新旧返回格式：dict 或 Contact 对象
                if isinstance(c, dict):
                    wxid = c.get("wxid", "")
                    self._contacts[wxid] = c
                else:
                    # 新版 wcferry 可能返回 Contact 对象
                    wxid = getattr(c, "wxid", "") or getattr(c, "UserName", "")
                    name = getattr(c, "name", "") or getattr(c, "NickName", "")
                    remark = getattr(c, "remark", "") or getattr(c, "RemarkName", "")
                    self._contacts[wxid] = {
                        "wxid": wxid,
                        "name": remark or name,  # 优先使用备注名
                    }
            logger.info(f"已缓存 {len(self._contacts)} 个联系人")
        except Exception as e:
            logger.error(f"刷新联系人失败: {e}")

    def get_contact_name(self, wxid: str) -> str:
        """获取联系人昵称"""
        if wxid in self._contacts:
            return self._contacts[wxid].get("name", wxid)
        return wxid

    def is_group_msg(self, msg: WxMsg) -> bool:
        """判断是否是群消息（兼容 3.9.x 和 4.x）"""
        roomid = getattr(msg, "roomid", None) or getattr(msg, "room_id", "")
        return bool(roomid)

    def _get_roomid(self, msg: WxMsg) -> str:
        """获取群 ID（兼容不同属性名）"""
        return getattr(msg, "roomid", None) or getattr(msg, "room_id", "") or ""

    def _get_sender(self, msg: WxMsg) -> str:
        """获取发送者 wxid（兼容不同属性名）"""
        return getattr(msg, "sender", None) or getattr(msg, "from_wxid", "") or ""

    def _get_content(self, msg: WxMsg) -> str:
        """获取消息内容（兼容不同属性名）"""
        return getattr(msg, "content", None) or getattr(msg, "text", "") or ""

    def _get_msg_type(self, msg: WxMsg) -> int:
        """获取消息类型"""
        return getattr(msg, "type", 0)

    def is_at_me(self, msg: WxMsg) -> bool:
        """
        判断群消息是否 @了我
        兼容微信 3.9.x（文本中 @昵称）和 4.x（可能有 xml 标记或 ats 属性）
        """
        if not self.self_wxid:
            return False

        # 方式 1：新版 wcferry 可能有 is_at 方法或 ats 属性
        if hasattr(msg, 'is_at') and callable(getattr(msg, 'is_at', None)):
            try:
                return msg.is_at(self.self_wxid)
            except Exception:
                pass

        # 方式 2：检查 ats 属性（新版 wcferry 可能包含被 @ 的 wxid 列表）
        ats = getattr(msg, 'ats', None)
        if ats:
            if isinstance(ats, str):
                return self.self_wxid in ats
            elif isinstance(ats, (list, tuple)):
                return self.self_wxid in ats

        # 方式 3：从 XML 额外数据中解析 @ 信息（微信 4.x）
        xml_content = getattr(msg, 'xml', None) or getattr(msg, 'extra', None)
        if xml_content and isinstance(xml_content, str):
            try:
                root = ET.fromstring(xml_content)
                # 查找 atuserlist 节点
                at_node = root.find('.//atuserlist')
                if at_node is not None and at_node.text:
                    at_list = at_node.text.split(',')
                    if self.self_wxid in at_list:
                        return True
            except ET.ParseError:
                pass

        # 方式 4：传统文本匹配（3.9.x 兼容）
        content = self._get_content(msg)
        my_name = self.get_contact_name(self.self_wxid)
        return f"@{my_name}" in content

    def extract_message_text(self, msg: WxMsg) -> str:
        """
        提取消息文本（去掉 @XXX 前缀）
        """
        text = self._get_content(msg).strip()
        # 移除群消息中的 @xxx 部分
        if self.is_group_msg(msg) and self.self_wxid:
            my_name = self.get_contact_name(self.self_wxid)
            text = re.sub(rf"@{re.escape(my_name)}\s*", "", text).strip()
        return text

    def handle_message(self, msg: WxMsg):
        """
        消息处理主入口
        """
        sender_wxid = self._get_sender(msg)

        # 忽略自己发的消息
        if sender_wxid == self.self_wxid:
            return

        # 只处理文本消息（type=1）
        if self._get_msg_type(msg) != 1:
            return

        sender_name = self.get_contact_name(sender_wxid)
        text = self._get_content(msg).strip()
        is_group = self.is_group_msg(msg)

        if is_group:
            room_name = self.get_contact_name(self._get_roomid(msg))
            logger.info(f"[群:{room_name}] {sender_name}: {text[:50]}")
        else:
            logger.info(f"[私聊] {sender_name}: {text[:50]}")

        # ── 特殊命令 ──
        if text.lower() in ("/reset", "重置对话", "清除记忆"):
            contact_id = self._get_roomid(msg) if is_group else sender_wxid
            self.ai.reset_context(contact_id)
            self._reply(msg, "对话已重置，让我们重新开始吧！")
            return

        # ── 群消息处理 ──
        if is_group:
            self._handle_group_message(msg, sender_name, text)
            return

        # ── 私聊消息处理 ──
        self._handle_private_message(msg, sender_name, text)

    def _handle_private_message(self, msg: WxMsg, sender_name: str, text: str):
        """处理私聊消息"""
        sender_wxid = self._get_sender(msg)

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

    def _handle_group_message(self, msg: WxMsg, sender_name: str, text: str):
        """处理群消息"""
        roomid = self._get_roomid(msg)
        room_name = self.get_contact_name(roomid)
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
            ai_reply = self.ai.chat(roomid, ai_text)
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

    def _reply(self, msg: WxMsg, content: str):
        """回复消息（兼容 3.9.x 和 4.x）"""
        try:
            if self.is_group_msg(msg):
                receiver = self._get_roomid(msg)
                # 新版 wcferry 的 send_text 可能支持 aters 参数
                # 用于在群中 @ 某人
                try:
                    self.wcf.send_text(content, receiver)
                except TypeError:
                    self.wcf.send_text(msg=content, receiver=receiver)
            else:
                receiver = self._get_sender(msg)
                try:
                    self.wcf.send_text(content, receiver)
                except TypeError:
                    self.wcf.send_text(msg=content, receiver=receiver)
        except Exception as e:
            logger.error(f"发送消息失败: {e}")

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

        try:
            self.wcf.send_text(message, wxid)
            logger.info(f"定时消息已发送: {target_type}:{target_name}")
            return True
        except Exception as e:
            logger.error(f"定时消息发送失败: {e}")
            return False

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
