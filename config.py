"""
配置管理模块
加载和验证 config.yaml
"""

import os
import yaml
from loguru import logger


DEFAULT_CONFIG = {
    "ai": {
        "provider": "none",
        "anthropic_api_key": "",
        "openai_api_key": "",
        "model": "claude-sonnet-4-20250514",
        "system_prompt": "你是一个友好的微信助手，请用简洁的中文回复。",
        "max_tokens": 1024,
    },
    "auto_reply": {
        "enabled": True,
        "rules": [],
    },
    "group": {
        "enabled_groups": [],
        "welcome_message": "",
        "ai_reply": True,
        "ai_trigger": "at",
        "ai_prefix": "/ai ",
    },
    "scheduler": {
        "enabled": False,
        "tasks": [],
    },
    "safety": {
        "min_reply_interval": 2,
        "max_replies_per_minute": 15,
        "whitelist_friends": [],
        "blacklist_friends": [],
        "reply_strangers": False,
    },
    "logging": {
        "level": "INFO",
        "file": "logs/wxbot.log",
        "rotation": "10 MB",
        "retention": "7 days",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并字典，override 覆盖 base"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_path: str = "config.yaml") -> dict:
    """
    加载配置文件，不存在时使用默认配置
    """
    if not os.path.exists(config_path):
        logger.warning(f"配置文件 {config_path} 不存在，使用默认配置")
        logger.warning("请复制 config.example.yaml 为 config.yaml 并修改")
        return DEFAULT_CONFIG.copy()

    with open(config_path, "r", encoding="utf-8") as f:
        user_config = yaml.safe_load(f) or {}

    config = _deep_merge(DEFAULT_CONFIG, user_config)

    # 验证关键配置
    if config["ai"]["provider"] == "claude" and not config["ai"]["anthropic_api_key"]:
        logger.error("选择了 Claude 作为 AI 提供商，但未配置 anthropic_api_key")
    if config["ai"]["provider"] == "openai" and not config["ai"]["openai_api_key"]:
        logger.error("选择了 OpenAI 作为 AI 提供商，但未配置 openai_api_key")

    logger.info(f"配置加载完成: AI={config['ai']['provider']}, "
                f"自动回复={config['auto_reply']['enabled']}, "
                f"定时任务={config['scheduler']['enabled']}")

    return config
