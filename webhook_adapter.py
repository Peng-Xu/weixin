"""
Webhook 适配器
适用于微信 4.x，通过 HTTP API 收发消息
架构：
  接收 —— 本地启动 FastAPI 服务，微信客户端后端将消息 POST 到此处
  发送 —— 调用微信客户端后端的 HTTP 发送接口

兼容大多数提供 HTTP API 的微信 4.x 客户端容器，如 wechatpad、gewechat 等。

配置示例（config.yaml）：
  wechat:
    adapter: "webhook"
    webhook:
      listen_host: "0.0.0.0"
      listen_port: 8080
      send_url: "http://127.0.0.1:9000/send/text"
      contacts_url: "http://127.0.0.1:9000/contacts"   # 可选
      health_url: "http://127.0.0.1:9000/health"        # 可选
      token: ""                                         # API 鉴权 token（可选）

收消息的 Payload（客户端后端 POST 过来）：
  {
    "type": 1,             // 消息类型，1=文本
    "content": "Hello",
    "sender": "wxid_xxx",
    "sender_name": "昵称",
    "roomid": "",          // 群消息时为群 id，私聊时为空
    "is_at_me": false,
    "msg_id": "12345"
  }

发消息的 Payload（POST 到 send_url）：
  {
    "to": "wxid_xxx 或 roomid",
    "content": "Hello",
    "token": "..."         // 若配置了 token
  }
"""

import threading
import time
import uvicorn
import requests
from fastapi import FastAPI, Request, Response
from loguru import logger

from wechat_adapter import WechatAdapter, Message


class WebhookAdapter(WechatAdapter):
    """基于 HTTP Webhook 的适配器，适用于微信 4.x"""

    def __init__(self, config: dict):
        wh = config.get("wechat", {}).get("webhook", {})
        self._host = wh.get("listen_host", "0.0.0.0")
        self._port = int(wh.get("listen_port", 8080))
        self._send_url = wh.get("send_url", "http://127.0.0.1:9000/send/text")
        self._contacts_url = wh.get("contacts_url", "")
        self._health_url = wh.get("health_url", "")
        self._token = wh.get("token", "")

        self._self_wxid: str = wh.get("self_wxid", "")  # 可在配置中写死，也可通过 API 查询
        self._contacts_cache: list[dict] = []
        self._app = self._build_app()
        self._server_thread: threading.Thread | None = None
        self._server = None

    # ── 生命周期 ──

    def start(self) -> bool:
        try:
            cfg = uvicorn.Config(
                self._app,
                host=self._host,
                port=self._port,
                log_level="warning",  # 减少 uvicorn 自身日志噪音
            )
            self._server = uvicorn.Server(cfg)
            self._server_thread = threading.Thread(
                target=self._server.run,
                daemon=True,
                name="webhook-server",
            )
            self._server_thread.start()
            # 等待服务就绪
            time.sleep(0.5)
            logger.info(f"Webhook 服务已启动，监听 {self._host}:{self._port}")
            return True
        except Exception as e:
            logger.error(f"Webhook 服务启动失败: {e}")
            return False

    def stop(self):
        if self._server:
            self._server.should_exit = True

    def is_login(self) -> bool:
        if not self._health_url:
            # 未配置 health_url，乐观认为已登录
            return True
        try:
            resp = requests.get(self._health_url, timeout=3)
            return resp.status_code == 200 and "healthy" in resp.text.lower()
        except Exception:
            return False

    # ── 基础信息 ──

    def get_self_wxid(self) -> str:
        return self._self_wxid

    def get_contacts(self) -> list[dict]:
        if self._contacts_cache:
            return self._contacts_cache
        if not self._contacts_url:
            return []
        try:
            resp = requests.get(
                self._contacts_url,
                headers=self._auth_headers(),
                timeout=5,
            )
            data = resp.json()
            # 兼容 {"data": [...]} 或直接返回列表
            contacts = data.get("data", data) if isinstance(data, dict) else data
            self._contacts_cache = [
                {"wxid": c.get("wxid", c.get("id", "")), "name": c.get("name", c.get("nickname", ""))}
                for c in contacts
            ]
            logger.info(f"已获取 {len(self._contacts_cache)} 个联系人")
            return self._contacts_cache
        except Exception as e:
            logger.error(f"获取联系人失败: {e}")
            return []

    # ── 发送 ──

    def send_text(self, content: str, receiver: str) -> bool:
        payload = {"to": receiver, "content": content}
        if self._token:
            payload["token"] = self._token
        try:
            resp = requests.post(
                self._send_url,
                json=payload,
                headers=self._auth_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return True
            logger.error(f"发送消息失败 HTTP {resp.status_code}: {resp.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"发送消息异常 [{receiver}]: {e}")
            return False

    # ── 接收（FastAPI 服务器处理入站消息）──

    def enable_receiving(self):
        """Webhook 模式下消息由 HTTP 服务器推送，此方法为空操作"""
        logger.info("Webhook 模式：等待微信客户端后端推送消息...")

    # ── 内部工具 ──

    def _auth_headers(self) -> dict:
        if self._token:
            return {"X-Token": self._token, "Authorization": f"Bearer {self._token}"}
        return {}

    def _build_app(self) -> FastAPI:
        """构建 FastAPI 应用，注册收消息路由"""
        app = FastAPI(title="WxBot Webhook Receiver", docs_url=None, redoc_url=None)

        @app.post("/webhook")
        async def receive_message(request: Request):
            try:
                data = await request.json()
            except Exception:
                return Response(content="bad request", status_code=400)

            msg = Message(
                type=int(data.get("type", 1)),
                content=str(data.get("content", "")),
                sender=str(data.get("sender", "")),
                sender_name=str(data.get("sender_name", "")),
                roomid=str(data.get("roomid", "")),
                msg_id=str(data.get("msg_id", "")),
                is_at_me=bool(data.get("is_at_me", False)),
            )

            if self._on_message:
                try:
                    self._on_message(msg)
                except Exception as e:
                    logger.error(f"消息回调异常: {e}", exc_info=True)

            return {"success": True}

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        return app
