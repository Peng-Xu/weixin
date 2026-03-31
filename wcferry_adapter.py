"""
WeChatFerry 适配器
封装 wcferry，支持微信 3.9.12.17 / 3.9.12.51
"""

import threading
from loguru import logger
from wcferry import Wcf, WxMsg

from wechat_adapter import WechatAdapter, Message


class WcferryAdapter(WechatAdapter):
    """基于 wcferry 的适配器，适用于微信 PC 3.9.x"""

    def __init__(self):
        self._wcf: Wcf | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        try:
            self._wcf = Wcf()
            logger.info("wcferry 适配器已启动")
            return True
        except Exception as e:
            logger.error(f"wcferry 启动失败: {e}")
            logger.error("请确认：微信 PC 3.9.12.17/3.9.12.51 已打开并登录")
            return False

    def stop(self):
        self._running = False
        if self._wcf:
            try:
                self._wcf.disable_recv_msg()
            except Exception:
                pass

    def is_login(self) -> bool:
        return self._wcf is not None and self._wcf.is_login()

    def get_self_wxid(self) -> str:
        return self._wcf.get_self_wxid() if self._wcf else ""

    def get_contacts(self) -> list[dict]:
        if not self._wcf:
            return []
        raw = self._wcf.get_contacts()
        return [{"wxid": c["wxid"], "name": c.get("name", c["wxid"])} for c in raw]

    def send_text(self, content: str, receiver: str) -> bool:
        if not self._wcf:
            return False
        try:
            self._wcf.send_text(content, receiver)
            return True
        except Exception as e:
            logger.error(f"wcferry 发送消息失败 [{receiver}]: {e}")
            return False

    def enable_receiving(self):
        """启动后台线程持续拉取消息"""
        self._running = True

        def _loop():
            self._wcf.enable_receiving_msg()
            while self._running:
                wx_msg: WxMsg = self._wcf.get_msg()
                if wx_msg and self._on_message:
                    msg = self._convert(wx_msg)
                    try:
                        self._on_message(msg)
                    except Exception as e:
                        logger.error(f"消息回调异常: {e}", exc_info=True)

        self._thread = threading.Thread(target=_loop, daemon=True, name="wcferry-recv")
        self._thread.start()
        logger.info("wcferry 消息接收已启动")

    # ── 内部工具 ──

    @staticmethod
    def _convert(wx_msg: WxMsg) -> Message:
        return Message(
            type=wx_msg.type,
            content=wx_msg.content or "",
            sender=wx_msg.sender or "",
            sender_name="",   # 由 MessageHandler 通过联系人缓存补充
            roomid=wx_msg.roomid or "",
            msg_id=str(getattr(wx_msg, "id", "")),
            is_at_me=False,   # 由 MessageHandler.is_at_me() 判断
        )
