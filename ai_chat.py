"""
AI 对话模块
支持 Claude / OpenAI，带上下文记忆（按联系人隔离）
"""

import time
from collections import defaultdict
from loguru import logger


class ChatHistory:
    """管理每个联系人的对话历史"""

    def __init__(self, max_turns: int = 20, expire_seconds: int = 3600):
        self._history: dict[str, list[dict]] = defaultdict(list)
        self._last_active: dict[str, float] = {}
        self.max_turns = max_turns
        self.expire_seconds = expire_seconds

    def add(self, contact_id: str, role: str, content: str):
        now = time.time()
        # 过期清理
        if contact_id in self._last_active:
            if now - self._last_active[contact_id] > self.expire_seconds:
                self._history[contact_id] = []
        self._last_active[contact_id] = now
        self._history[contact_id].append({"role": role, "content": content})
        # 保留最近 N 轮
        if len(self._history[contact_id]) > self.max_turns * 2:
            self._history[contact_id] = self._history[contact_id][-self.max_turns * 2:]

    def get(self, contact_id: str) -> list[dict]:
        now = time.time()
        if contact_id in self._last_active:
            if now - self._last_active[contact_id] > self.expire_seconds:
                self._history[contact_id] = []
        return self._history[contact_id]

    def clear(self, contact_id: str):
        self._history[contact_id] = []
        self._last_active.pop(contact_id, None)


class AIChat:
    """AI 对话统一接口"""

    def __init__(self, config: dict):
        self.provider = config["ai"]["provider"]
        self.model = config["ai"]["model"]
        self.system_prompt = config["ai"]["system_prompt"]
        self.max_tokens = config["ai"]["max_tokens"]
        self.history = ChatHistory()
        self._client = None

        if self.provider == "claude":
            self._init_claude(config["ai"]["anthropic_api_key"])
        elif self.provider == "openai":
            self._init_openai(config["ai"]["openai_api_key"])
        elif self.provider == "volcengine":
            self._init_volcengine(config["ai"]["volcengine_api_key"])
        elif self.provider == "none":
            logger.info("AI 对话已禁用")
        else:
            logger.error(f"未知的 AI 提供商: {self.provider}")

    def _init_claude(self, api_key: str):
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
            logger.info(f"Claude 初始化成功, model={self.model}")
        except ImportError:
            logger.error("请安装 anthropic: pip install anthropic")
        except Exception as e:
            logger.error(f"Claude 初始化失败: {e}")

    def _init_openai(self, api_key: str):
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key)
            logger.info(f"OpenAI 初始化成功, model={self.model}")
        except ImportError:
            logger.error("请安装 openai: pip install openai")
        except Exception as e:
            logger.error(f"OpenAI 初始化失败: {e}")

    def _init_volcengine(self, api_key: str):
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=api_key,
                base_url="https://ark.cn-beijing.volces.com/api/v3",
            )
            logger.info(f"火山引擎 ARK 初始化成功, model={self.model}")
        except ImportError:
            logger.error("请安装 openai: pip install openai")
        except Exception as e:
            logger.error(f"火山引擎 ARK 初始化失败: {e}")

    def chat(self, contact_id: str, message: str) -> str:
        """
        发送消息并获取 AI 回复
        contact_id: 用于隔离不同联系人的对话上下文
        """
        if self.provider == "none" or self._client is None:
            return ""

        # 记录用户消息
        self.history.add(contact_id, "user", message)
        history = self.history.get(contact_id)

        try:
            if self.provider == "claude":
                reply = self._chat_claude(history)
            elif self.provider in ("openai", "volcengine"):
                reply = self._chat_openai(history)
            else:
                return ""

            # 记录助手回复
            self.history.add(contact_id, "assistant", reply)
            return reply

        except Exception as e:
            logger.error(f"AI 回复失败 [{contact_id}]: {e}")
            return f"抱歉，AI 回复出错了: {type(e).__name__}"

    def _chat_claude(self, history: list[dict]) -> str:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system_prompt,
            messages=history,
        )
        return response.content[0].text

    def _chat_openai(self, history: list[dict]) -> str:
        messages = [{"role": "system", "content": self.system_prompt}] + history
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
        )
        return response.choices[0].message.content

    def reset_context(self, contact_id: str):
        """清除某个联系人的对话历史"""
        self.history.clear(contact_id)
        logger.info(f"已清除 {contact_id} 的对话历史")
