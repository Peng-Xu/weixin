# 微信机器人（基于 WeChatFerry）

基于 [WeChatFerry](https://github.com/lich0821/WeChatFerry) 的个人微信机器人，支持 AI 对话、关键词自动回复、群管理、定时消息推送。

> **重要说明**：经调研，ComWeChatRobot 项目已停更 3 年（最后支持微信 3.7.x），无法用于当前微信版本。本项目采用 **WeChatFerry**——2026 年最活跃的微信 Hook 方案，支持微信 PC **3.9.12.17 / 3.9.12.51**。

## 功能清单

| 功能 | 说明 |
|---|---|
| AI 智能对话 | 接入 Claude / OpenAI，按联系人隔离上下文，支持多轮对话 |
| 关键词自动回复 | 配置关键词→回复映射，优先于 AI 匹配 |
| 群管理 | 群内 @机器人 触发 AI、关键词回复、新成员欢迎语 |
| 定时消息推送 | 基于 cron 表达式，定时向好友/群发送文本、天气等 |
| 安全限流 | 最小回复间隔、每分钟上限、黑白名单、陌生人过滤 |

## 架构

```
bot.py              主程序入口，连接微信、启动各模块
message_handler.py  核心消息处理引擎（私聊/群聊分发）
ai_chat.py          AI 对话模块（Claude/OpenAI + 上下文记忆）
scheduler.py        定时任务模块（APScheduler）
safety.py           安全限流模块
config.py           配置加载与校验
config.example.yaml 配置模板
```

## 快速开始

### 1. 环境准备

- **Windows 10/11**（WeChatFerry 仅支持 Windows）
- **Python 3.10+**
- **微信 PC 客户端 3.9.12.17 或 3.9.12.51**

> 微信版本很关键！请从 WeChatFerry 仓库的 Release 页面下载对应版本安装包，并**关闭自动更新**。

### 2. 安装依赖

```bash
cd /home/pengx/work/wx
pip install -r requirements.txt
```

### 3. 配置

```bash
# 复制配置模板
cp config.example.yaml config.yaml

# 编辑配置（至少填入 AI API Key）
```

关键配置项：
- `ai.provider`: 选择 `claude` / `openai` / `none`
- `ai.anthropic_api_key`: Claude API Key
- `auto_reply.rules`: 关键词回复规则
- `group.ai_trigger`: 群内 AI 触发方式（`at` / `prefix` / `all`）
- `scheduler.tasks`: 定时任务列表

### 4. 运行

```bash
# 1. 先打开微信 PC 并登录
# 2. 运行机器人
python bot.py

# 指定配置文件
python bot.py -c my_config.yaml
```

## 功能说明

### AI 对话

- 私聊：直接发消息即可触发 AI 回复
- 群聊：默认需要 @机器人 才触发（可在配置中修改为前缀触发或全部触发）
- 发送「重置对话」或 `/reset` 清除对话记忆
- 对话上下文按联系人/群隔离，1 小时无活动自动清除

### 关键词自动回复

优先级高于 AI。在 `config.yaml` 中配置：

```yaml
auto_reply:
  enabled: true
  rules:
    - keyword: "你好"
      reply: "你好！"
    - keyword: "天气"
      reply: "请发送「天气+城市名」查询天气"
```

### 定时任务

支持 cron 表达式，示例：

```yaml
scheduler:
  enabled: true
  tasks:
    - name: "早安推送"
      cron: "0 8 * * *"           # 每天 8:00
      target_type: "friend"
      target_name: "文件传输助手"
      message: "早上好！"
```

### 安全限流

```yaml
safety:
  min_reply_interval: 2     # 同一联系人至少间隔 2 秒
  max_replies_per_minute: 15 # 全局每分钟最多 15 条
  blacklist_friends: ["广告号"]
  reply_strangers: false
```

## 风险提示

1. **封号风险**：Hook 方式有被检测的可能。强烈建议使用**小号**运行
2. **版本锁定**：必须使用 WeChatFerry 支持的微信版本，关闭自动更新
3. **频率控制**：已内置限流，但仍建议避免群发、快速加好友等高风险操作
4. **合规使用**：仅用于个人学习和辅助，不得用于骚扰、诈骗等违法用途

## 文件说明

```
wx/
├── bot.py               # 主程序入口
├── message_handler.py   # 消息处理引擎
├── ai_chat.py           # AI 对话模块
├── scheduler.py         # 定时任务
├── safety.py            # 安全限流
├── config.py            # 配置管理
├── config.example.yaml  # 配置模板
├── requirements.txt     # Python 依赖
├── README.md            # 本文件
└── logs/                # 运行日志（自动创建）
```
# weixin
