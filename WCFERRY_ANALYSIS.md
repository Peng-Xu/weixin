# WeChatFerry (wcferry) Integration Analysis

## Executive Summary

This is a WeChat bot based on **WeChatFerry**, a Windows-only WeChat Hook library. The bot connects to a running WeChat PC client, receives messages via polling, processes them, and sends replies. The full lifecycle involves: connection → contact caching → message loop → message dispatch → reply sending.

---

## 1. BOT.PY - Initialization & Lifecycle

### 1.1 Wcf Initialization

**File**: `bot.py` lines 70-92

```python
# Method 1: Modern WeChatFerry (with host/port support)
try:
    self.wcf = Wcf(
        host=wcf_config.get("host", "127.0.0.1"),
        port=wcf_config.get("port", 10086),
        debug=wcf_config.get("debug", False),
    )
    if not self.wcf.is_login():
        logger.error("微信未登录！请先在 PC 上登录微信，然后重新运行")
        sys.exit(1)
except TypeError:
    # Method 2: Fallback for older wcferry versions
    logger.warning("wcferry 版本较旧，使用默认连接方式")
    self.wcf = Wcf()
    if not self.wcf.is_login():
        logger.error("微信未登录！请先在 PC 上登录微信，然后重新运行")
        sys.exit(1)
```

**Key Details**:
- `Wcf` is imported from `wcferry` library
- Constructor accepts `host`, `port`, `debug` parameters (with fallback for older versions)
- Always checks `is_login()` immediately after construction
- Requires WeChat PC client to be running and logged in
- Connection defaults to `127.0.0.1:10086`

### 1.2 WxBot Methods Called on Wcf

| Method | Called In | Purpose |
|--------|-----------|---------|
| `is_login()` | `start()` (line 75, 82) | Verify WeChat login status |
| `get_self_wxid()` | `start()` (line 97) | Get self wxid for filtering |
| `get_contacts()` | `message_handler.refresh_contacts()` (line 36) | Load all contacts (dict/object) |
| `get_wechat_version()` | `_detect_wechat_version()` (line 170) | Optional version detection |
| `enable_receiving_msg()` | `_start_message_loop()` (line 143) | Modern API to enable msg receive |
| `enable_recv_msg()` | `_start_message_loop()` (lines 146, 148) | Fallback API for older versions |
| `get_msg()` | `_start_message_loop()` (line 151) | Blocking poll for next message |
| `disable_recv_msg()` | `stop()` (line 197) | Cleanup on shutdown |

### 1.3 Message Loop Structure

**File**: `bot.py` lines 135-163

```python
def _start_message_loop(self):
    """启动消息接收循环"""
    def loop():
        while self._running:
            try:
                # Enable message receiving (new or old API)
                try:
                    self.wcf.enable_receiving_msg()
                except TypeError:
                    self.wcf.enable_recv_msg()
                except AttributeError:
                    self.wcf.enable_recv_msg()

                # Message polling loop
                while self._running:
                    msg = self.wcf.get_msg()  # BLOCKING call
                    if msg:
                        try:
                            self.handler.handle_message(msg)
                        except Exception as e:
                            logger.error(f"消息处理异常: {e}", exc_info=True)
            except Exception as e:
                if self._running:
                    logger.error(f"消息循环异常: {e}", exc_info=True)
                    time.sleep(3)  # Retry after 3 seconds

    t = threading.Thread(target=loop, daemon=True, name="msg-loop")
    t.start()
```

**Key Details**:
- Runs in **separate daemon thread** (line 162)
- `enable_receiving_msg()` / `enable_recv_msg()` is called ONCE before polling loop
- `get_msg()` is a **blocking call** that waits for next message
- If `get_msg()` returns `None`, loop continues (no message yet)
- Exceptions in message polling trigger 3-second retry
- `handler.handle_message(msg)` is called immediately for each message

### 1.4 Full Bot Lifecycle

**File**: `bot.py` lines 59-134

```
1. Load configuration
2. Setup logging
3. Connect to WeChat (Wcf initialization)
4. Check login status
5. Detect WeChat version (optional)
6. Get self wxid: self.wcf.get_self_wxid()
7. Initialize modules:
   - AIChat (for AI responses)
   - RateLimiter (for safety)
   - MessageHandler (pass wcf, ai, limiter)
   - Set handler's self_wxid
   - Call handler.refresh_contacts() to cache contacts
8. Start TaskScheduler if enabled
9. Start message loop (daemon thread)
10. Register signal handlers (SIGINT, SIGTERM)
11. Keep main thread alive with while loop + sleep(1)
12. On shutdown: stop scheduler, disable_recv_msg()
```

---

## 2. MESSAGE_HANDLER.PY - Message Reception & Processing

### 2.1 WxMsg Object Structure

**Imported from**: `wcferry import WxMsg`

WxMsg is an object/dataclass with these fields accessed in the code:

| Field | Access Method | Type | Example |
|-------|---|---|---|
| sender | `getattr(msg, 'sender', None)` | str | "wxid_123456..." |
| roomid / room_id | `getattr(msg, 'roomid', None)` | str or None | "12345@chatroom" |
| content / text | `getattr(msg, 'content', None)` | str | "你好" |
| type | `getattr(msg, 'type', 0)` | int | 1 (text), others ignored |
| xml / extra | `getattr(msg, 'xml', None)` or `getattr(msg, 'extra', None)` | str (XML) | For @ info parsing |
| ats | `getattr(msg, 'ats', None)` | list/str or None | ["wxid_..."] or comma-separated |
| is_at() | `msg.is_at(wxid)` | method | Returns bool |

### 2.2 Message Receiving Data Flow

```
wcf.get_msg()  [BLOCKING CALL IN LOOP]
    ↓
Returns WxMsg object (or None if timeout)
    ↓
handler.handle_message(msg)  [CALLED FOR EACH NON-NULL MSG]
    ↓
Extracts: sender_wxid, room_id, content, type
    ↓
Filters:
  - Ignore self messages (sender == self_wxid)
  - Only process text (type == 1)
    ↓
Routes to:
  - _handle_private_message(msg, sender_name, text)
  - _handle_group_message(msg, sender_name, text)
```

### 2.3 MessageHandler Initialization

**File**: `message_handler.py` lines 19-28

```python
def __init__(self, config: dict, wcf, ai: AIChat, limiter: RateLimiter):
    self.config = config
    self.wcf = wcf                      # Wcf instance passed from bot.py
    self.ai = ai                        # AIChat instance
    self.limiter = limiter              # RateLimiter instance
    self.self_wxid = ""                 # Set later by set_self_wxid()
    self._contacts: dict[str, dict] = {}  # Cached contact info
    self._rooms: dict[str, dict] = {}     # For future group caching
```

### 2.4 Contact Resolution & Caching

**File**: `message_handler.py` lines 33-60

```python
def refresh_contacts(self):
    """刷新联系人缓存（兼容不同 wcferry 版本的返回格式）"""
    try:
        contacts = self.wcf.get_contacts()  # Returns list of Contact objects or dicts
        self._contacts = {}
        for c in contacts:
            # Format 1: dict
            if isinstance(c, dict):
                wxid = c.get("wxid", "")
                self._contacts[wxid] = c
            # Format 2: Contact object (new wcferry)
            else:
                wxid = getattr(c, "wxid", "") or getattr(c, "UserName", "")
                name = getattr(c, "name", "") or getattr(c, "NickName", "")
                remark = getattr(c, "remark", "") or getattr(c, "RemarkName", "")
                self._contacts[wxid] = {
                    "wxid": wxid,
                    "name": remark or name,  # Prefer remark
                }
```

**Data Structures**:
- `_contacts`: `dict[str, dict]` mapping `wxid → {"wxid": str, "name": str}`
- Group rooms end with `@chatroom`
- Friend wxids are regular format

**Lookup Methods**:
```python
def get_contact_name(self, wxid: str) -> str:
    """Get nickname, default to wxid if not cached"""
    if wxid in self._contacts:
        return self._contacts[wxid].get("name", wxid)
    return wxid

def _find_wxid(self, target_type: str, target_name: str) -> str:
    """Find wxid by nickname"""
    for wxid, info in self._contacts.items():
        name = info.get("name", "")
        if name == target_name:
            if target_type == "friend" and not wxid.endswith("@chatroom"):
                return wxid
            if target_type == "group" and wxid.endswith("@chatroom"):
                return wxid
    return ""
```

### 2.5 Message Field Accessors (Compatibility Layer)

**File**: `message_handler.py` lines 62-81

These methods handle wcferry API variations:

```python
def is_group_msg(self, msg: WxMsg) -> bool:
    """Check if message is from group"""
    roomid = getattr(msg, "roomid", None) or getattr(msg, "room_id", "")
    return bool(roomid)

def _get_roomid(self, msg: WxMsg) -> str:
    """Get group ID (tries both attribute names)"""
    return getattr(msg, "roomid", None) or getattr(msg, "room_id", "") or ""

def _get_sender(self, msg: WxMsg) -> str:
    """Get sender wxid"""
    return getattr(msg, "sender", None) or getattr(msg, "from_wxid", "") or ""

def _get_content(self, msg: WxMsg) -> str:
    """Get message text"""
    return getattr(msg, "content", None) or getattr(msg, "text", "") or ""

def _get_msg_type(self, msg: WxMsg) -> int:
    """Get message type"""
    return getattr(msg, "type", 0)
```

### 2.6 @ Detection (Multiple Methods)

**File**: `message_handler.py` lines 83-123

```python
def is_at_me(self, msg: WxMsg) -> bool:
    """Check if message @'d me (group only)"""
    
    # Method 1: Direct is_at() method (new wcferry)
    if hasattr(msg, 'is_at') and callable(getattr(msg, 'is_at', None)):
        try:
            return msg.is_at(self.self_wxid)
        except Exception:
            pass
    
    # Method 2: ats attribute (list or comma-separated string)
    ats = getattr(msg, 'ats', None)
    if ats:
        if isinstance(ats, str):
            return self.self_wxid in ats
        elif isinstance(ats, (list, tuple)):
            return self.self_wxid in ats
    
    # Method 3: XML parsing (WeChat 4.x)
    xml_content = getattr(msg, 'xml', None) or getattr(msg, 'extra', None)
    if xml_content and isinstance(xml_content, str):
        try:
            root = ET.fromstring(xml_content)
            at_node = root.find('.//atuserlist')
            if at_node is not None and at_node.text:
                at_list = at_node.text.split(',')
                if self.self_wxid in at_list:
                    return True
        except ET.ParseError:
            pass
    
    # Method 4: Text matching (WeChat 3.9.x)
    content = self._get_content(msg)
    my_name = self.get_contact_name(self.self_wxid)
    return f"@{my_name}" in content
```

### 2.7 Message Handling Entry Point

**File**: `message_handler.py` lines 136-173

```python
def handle_message(self, msg: WxMsg):
    """Main message handler"""
    sender_wxid = self._get_sender(msg)
    
    # Filter: Ignore own messages
    if sender_wxid == self.self_wxid:
        return
    
    # Filter: Only text messages (type=1)
    if self._get_msg_type(msg) != 1:
        return
    
    sender_name = self.get_contact_name(sender_wxid)
    text = self._get_content(msg).strip()
    is_group = self.is_group_msg(msg)
    
    # Log message
    if is_group:
        room_name = self.get_contact_name(self._get_roomid(msg))
        logger.info(f"[群:{room_name}] {sender_name}: {text[:50]}")
    else:
        logger.info(f"[私聊] {sender_name}: {text[:50]}")
    
    # Command: Reset conversation
    if text.lower() in ("/reset", "重置对话", "清除记忆"):
        contact_id = self._get_roomid(msg) if is_group else sender_wxid
        self.ai.reset_context(contact_id)
        self._reply(msg, "对话已重置，让我们重新开始吧！")
        return
    
    # Route: Private or group
    if is_group:
        self._handle_group_message(msg, sender_name, text)
    else:
        self._handle_private_message(msg, sender_name, text)
```

### 2.8 Private Message Handler

**File**: `message_handler.py` lines 175-200

```python
def _handle_private_message(self, msg: WxMsg, sender_name: str, text: str):
    """Handle private chat"""
    sender_wxid = self._get_sender(msg)
    
    # Safety check: Rate limiting
    can_reply, reason = self.limiter.should_reply(sender_name, is_friend=True)
    if not can_reply:
        logger.debug(f"跳过回复 {sender_name}: {reason}")
        return
    
    # 1. Keyword auto-reply (priority)
    if self.config["auto_reply"]["enabled"]:
        reply = self._match_keyword(text)
        if reply:
            self.limiter.wait_if_needed(sender_name)
            self._reply(msg, reply)
            self.limiter.record_reply(sender_name)
            return
    
    # 2. AI reply
    if self.ai.provider != "none":
        self.limiter.wait_if_needed(sender_name)
        ai_reply = self.ai.chat(sender_wxid, text)  # sender_wxid as context key
        if ai_reply:
            self._reply(msg, ai_reply)
            self.limiter.record_reply(sender_name)
```

### 2.9 Group Message Handler

**File**: `message_handler.py` lines 202-257

```python
def _handle_group_message(self, msg: WxMsg, sender_name: str, text: str):
    """Handle group chat"""
    roomid = self._get_roomid(msg)
    room_name = self.get_contact_name(roomid)
    group_config = self.config["group"]
    
    # Check if group is enabled
    enabled_groups = group_config.get("enabled_groups", [])
    if enabled_groups and room_name not in enabled_groups:
        return
    
    # Safety check
    can_reply, reason = self.limiter.should_reply(sender_name, is_friend=True)
    if not can_reply:
        return
    
    # 1. Keyword auto-reply
    if self.config["auto_reply"]["enabled"]:
        reply = self._match_keyword(text)
        if reply:
            self.limiter.wait_if_needed(sender_name)
            self._reply(msg, reply)
            self.limiter.record_reply(sender_name)
            return
    
    # 2. Group AI reply
    if not group_config.get("ai_reply", False):
        return
    
    trigger = group_config.get("ai_trigger", "at")  # "at", "prefix", or "all"
    should_ai = False
    ai_text = text
    
    if trigger == "at":
        # Require @mention
        if self.is_at_me(msg):
            should_ai = True
            ai_text = self.extract_message_text(msg)  # Remove @mention
    elif trigger == "prefix":
        # Require prefix (e.g., "/ai ")
        prefix = group_config.get("ai_prefix", "/ai ")
        if text.startswith(prefix):
            should_ai = True
            ai_text = text[len(prefix):].strip()
    elif trigger == "all":
        # All messages
        should_ai = True
    
    if should_ai and ai_text and self.ai.provider != "none":
        self.limiter.wait_if_needed(sender_name)
        ai_reply = self.ai.chat(roomid, ai_text)  # roomid as context key (group context)
        if ai_reply:
            self._reply(msg, f"@{sender_name} {ai_reply}")  # @mention sender
            self.limiter.record_reply(sender_name)
```

---

## 3. MESSAGE SENDING INTERFACE

### 3.1 Internal Reply Method

**File**: `message_handler.py` lines 268-286

```python
def _reply(self, msg: WxMsg, content: str):
    """Send reply (handles private/group)"""
    try:
        if self.is_group_msg(msg):
            receiver = self._get_roomid(msg)
            # New API style
            try:
                self.wcf.send_text(content, receiver)
            except TypeError:
                # Old API style with keyword args
                self.wcf.send_text(msg=content, receiver=receiver)
        else:
            receiver = self._get_sender(msg)
            try:
                self.wcf.send_text(content, receiver)
            except TypeError:
                self.wcf.send_text(msg=content, receiver=receiver)
    except Exception as e:
        logger.error(f"发送消息失败: {e}")
```

**Method Signature**:
```
wcf.send_text(content: str, receiver: str) → None
  OR (older versions)
wcf.send_text(msg: str, receiver: str) → None

Receiver:
  - Private: sender_wxid (e.g., "wxid_123456...")
  - Group: roomid (e.g., "12345@chatroom")
```

### 3.2 External Send Interface (for Scheduler)

**File**: `message_handler.py` lines 288-305

```python
def send_to(self, target_type: str, target_name: str, message: str) -> bool:
    """Send to friend/group by name (for scheduler)"""
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
```

**Usage**:
```python
handler.send_to("friend", "文件传输助手", "早上好！")
handler.send_to("group", "技术讨论", "今日任务更新...")
```

### 3.3 Message Reply in Group Context

**File**: `message_handler.py` line 256

```python
self._reply(msg, f"@{sender_name} {ai_reply}")
```

When sending to a group, the bot prepends `@sender_name` to the message so the original sender is notified.

---

## 4. MESSAGE TEXT PROCESSING

### 4.1 Extract Message Text (Remove @)

**File**: `message_handler.py` lines 125-134

```python
def extract_message_text(self, msg: WxMsg) -> str:
    """Remove @mention from group message"""
    text = self._get_content(msg).strip()
    # Remove @xxx part in group messages
    if self.is_group_msg(msg) and self.self_wxid:
        my_name = self.get_contact_name(self.self_wxid)
        text = re.sub(rf"@{re.escape(my_name)}\s*", "", text).strip()
    return text
```

Example:
- Input: `"@机器人 今天天气怎样"`
- Output: `"今天天气怎样"`

### 4.2 Keyword Matching

**File**: `message_handler.py` lines 259-266

```python
def _match_keyword(self, text: str) -> str:
    """Match keyword rules from config"""
    rules = self.config["auto_reply"].get("rules", [])
    for rule in rules:
        keyword = rule.get("keyword", "")
        if keyword and keyword in text:
            return rule.get("reply", "")
    return ""
```

Simple substring matching, returns first match or empty string.

---

## 5. AI_CHAT.PY - Context Management

### 5.1 ChatHistory Data Structure

**File**: `ai_chat.py` lines 11-41

```python
class ChatHistory:
    """Manages conversation history per contact"""
    
    def __init__(self, max_turns: int = 20, expire_seconds: int = 3600):
        self._history: dict[str, list[dict]] = defaultdict(list)
        self._last_active: dict[str, float] = {}
        self.max_turns = max_turns  # Keep last 20 turns = 40 messages
        self.expire_seconds = expire_seconds  # 1 hour
    
    def add(self, contact_id: str, role: str, content: str):
        """Add message to history"""
        now = time.time()
        # Cleanup expired histories
        if contact_id in self._last_active:
            if now - self._last_active[contact_id] > self.expire_seconds:
                self._history[contact_id] = []
        self._last_active[contact_id] = now
        self._history[contact_id].append({"role": role, "content": content})
        # Keep only last N turns (2*N messages)
        if len(self._history[contact_id]) > self.max_turns * 2:
            self._history[contact_id] = self._history[contact_id][-self.max_turns * 2:]
    
    def get(self, contact_id: str) -> list[dict]:
        """Get history (with expiry check)"""
        now = time.time()
        if contact_id in self._last_active:
            if now - self._last_active[contact_id] > self.expire_seconds:
                self._history[contact_id] = []
        return self._history[contact_id]
    
    def clear(self, contact_id: str):
        """Clear history"""
        self._history[contact_id] = []
        self._last_active.pop(contact_id, None)
```

### 5.2 AIChat.chat() Method

**File**: `ai_chat.py` lines 99-125

```python
def chat(self, contact_id: str, message: str) -> str:
    """Send message and get AI reply"""
    if self.provider == "none" or self._client is None:
        return ""
    
    # Record user message
    self.history.add(contact_id, "user", message)
    history = self.history.get(contact_id)
    
    try:
        if self.provider == "claude":
            reply = self._chat_claude(history)
        elif self.provider in ("openai", "volcengine"):
            reply = self._chat_openai(history)
        else:
            return ""
        
        # Record assistant reply
        self.history.add(contact_id, "assistant", reply)
        return reply
    
    except Exception as e:
        logger.error(f"AI 回复失败 [{contact_id}]: {e}")
        return f"抱歉，AI 回复出错了: {type(e).__name__}"
```

**Context Key**:
- Private chat: `sender_wxid` (isolated per friend)
- Group chat: `roomid` (shared per group)

### 5.3 Claude API Call

**File**: `ai_chat.py` lines 127-134

```python
def _chat_claude(self, history: list[dict]) -> str:
    response = self._client.messages.create(
        model=self.model,
        max_tokens=self.max_tokens,
        system=self.system_prompt,
        messages=history,
    )
    return response.content[0].text
```

**Message Format**:
```python
{
    "role": "user" | "assistant",
    "content": str
}
```

---

## 6. SCHEDULER.PY - Scheduled Sending

**File**: `scheduler.py` lines 288-305

Scheduler uses the `handler.send_to()` method:

```python
def _execute_task(self, name: str, target_type: str, target_name: str,
                  message: str, message_type: str):
    """Execute scheduled task"""
    if not self._send_func:
        return
    
    if message_type == "text":
        self._send_func(target_type, target_name, message)
    elif message_type == "weather":
        city = message or "北京"
        weather_msg = self._fetch_weather(city)
        self._send_func(target_type, target_name, weather_msg)
```

---

## 7. DATA FLOW DIAGRAM

```
┌──────────────────────────────────────────────────────────────┐
│ RECEIVING FLOW                                               │
└──────────────────────────────────────────────────────────────┘

WeChat PC Client
    ↓
wcf.get_msg() [BLOCKING in message loop thread]
    ↓ (returns WxMsg when message arrives)
handler.handle_message(msg: WxMsg)
    ├─ Extract: sender_wxid, roomid, content, type
    ├─ Filter: Is self? → skip
    ├─ Filter: Is text (type=1)? → skip if not
    ├─ Get contact names from cache
    ├─ Check special commands (/reset)
    ├─ Route: is_group_msg()?
    │   ├─ YES → _handle_group_message()
    │   │   ├─ Check enabled_groups
    │   │   ├─ Check rate limits
    │   │   ├─ Try keyword match
    │   │   ├─ Check trigger: at / prefix / all
    │   │   │   ├─ "at": Check is_at_me() + extract_message_text()
    │   │   │   ├─ "prefix": Check startswith() + remove prefix
    │   │   │   └─ "all": Use message as-is
    │   │   └─ Call ai.chat(roomid, text) [shared group context]
    │   └─ NO → _handle_private_message()
    │       ├─ Check rate limits
    │       ├─ Try keyword match
    │       └─ Call ai.chat(sender_wxid, text) [isolated context]
    └─ _reply(msg, response_text)
         ├─ Get receiver: roomid (group) or sender_wxid (private)
         └─ wcf.send_text(response_text, receiver)


┌──────────────────────────────────────────────────────────────┐
│ INITIALIZATION FLOW                                          │
└──────────────────────────────────────────────────────────────┘

bot.py: WxBot.start()
    ├─ Wcf(host, port, debug)
    ├─ wcf.is_login() → verify logged in
    ├─ wcf.get_self_wxid() → get own wxid
    ├─ AIChat(config) → init AI provider
    ├─ RateLimiter(config)
    ├─ MessageHandler(config, wcf, ai, limiter)
    ├─ handler.set_self_wxid(wxid)
    ├─ handler.refresh_contacts()
    │   └─ wcf.get_contacts() → parse and cache
    ├─ TaskScheduler if enabled
    │   └─ scheduler.set_send_func(handler.send_to)
    └─ _start_message_loop() [daemon thread]
         ├─ wcf.enable_receiving_msg() or wcf.enable_recv_msg()
         └─ Loop: while _running:
             └─ msg = wcf.get_msg() [blocking]
```

---

## 8. KEY WCFERRY API SUMMARY

### Connection & Status
- `Wcf(host="127.0.0.1", port=10086, debug=False)` → Connection
- `wcf.is_login()` → bool
- `wcf.get_self_wxid()` → str (e.g., "wxid_...")
- `wcf.get_wechat_version()` → str (optional)

### Contacts
- `wcf.get_contacts()` → list[Contact|dict]
  - Each item: `{"wxid": str, "name": str, "remark": str, ...}` or Contact object

### Message Receiving
- `wcf.enable_receiving_msg()` / `wcf.enable_recv_msg()` → None
- `wcf.get_msg()` → WxMsg | None (blocking)
  - Fields: `sender`, `roomid`/`room_id`, `content`/`text`, `type`, `xml`/`extra`, `ats`, `is_at(wxid)`
  - Type: 1=text, others=ignored

### Message Sending
- `wcf.send_text(msg_or_content: str, receiver: str)` → None
  - Receiver: wxid (private) or roomid (group, ends with "@chatroom")

### Cleanup
- `wcf.disable_recv_msg()` → None

---

## 9. COMPATIBILITY NOTES

The code handles multiple wcferry versions:

| Feature | Old API | New API | Fallback |
|---------|---------|---------|----------|
| Init params | `Wcf()` | `Wcf(host, port, debug)` | Try new, catch TypeError |
| Enable msg | `enable_recv_msg()` | `enable_receiving_msg()` | Try new, catch TypeError/AttributeError |
| Message fields | `msg.sender`, `msg.content`, `msg.roomid` | `msg.from_wxid`, `msg.text`, `msg.room_id` | Try attr1, fallback attr2 |
| @ detection | Text matching | `msg.ats`, `msg.is_at()`, XML parse | All four methods tried |
| send_text() | `send_text(msg, receiver)` | `send_text(content, receiver)` | Try positional, catch TypeError |

---

## 10. MESSAGE TYPE CONSTANTS

Only message type **1** (text) is processed. Other types are ignored:

```python
TYPE_TEXT = 1  # Only this is handled
# Other types: images, files, voice, video, etc. are filtered out
```

---

## SUMMARY

**Wcf Initialization**: 
- Created once at startup with host/port config
- Verified with `is_login()`
- Stays alive for entire bot lifetime

**Message Loop**:
- Separate daemon thread
- Calls `get_msg()` in blocking loop
- Each message processed by `handle_message(msg)`

**Message Reception**:
- WxMsg has sender, roomid, content, type, ats fields
- Compatibility layer handles old/new API variations

**Message Sending**:
- `wcf.send_text(content, receiver)` for direct replies
- `handler.send_to(type, name, message)` for scheduled sends
- Group messages get `@sender_name` prefix

**Context**:
- Private chats: isolated per friend (by wxid)
- Group chats: shared per room (by roomid)
- Auto-expires after 1 hour of inactivity
