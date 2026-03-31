"""
消息持久化存储

使用 SQLite 存储读取到的消息，支持:
  - 自动去重（相同联系人+内容+时间不重复插入）
  - 按联系人/时间范围查询
  - 导出为 JSON / CSV
  - 统计信息
"""

from __future__ import annotations

import json
import csv
import sqlite3
import time
from pathlib import Path
from loguru import logger

from .pages.chat_page import ChatMessage


class MessageStorage:
    """SQLite 消息存储"""

    def __init__(self, db_path: str = "wechat_messages.db"):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()
        logger.info(f"消息数据库已打开: {db_path}")

    def _init_db(self):
        """初始化数据库表"""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact TEXT NOT NULL,
                sender TEXT NOT NULL,
                content TEXT NOT NULL,
                msg_type TEXT DEFAULT 'text',
                msg_time TEXT DEFAULT '',
                created_at REAL NOT NULL,
                UNIQUE(contact, sender, content, msg_time)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_contact
                ON messages(contact);

            CREATE INDEX IF NOT EXISTS idx_messages_created
                ON messages(created_at);

            CREATE TABLE IF NOT EXISTS contacts (
                name TEXT PRIMARY KEY,
                last_read_at REAL,
                message_count INTEGER DEFAULT 0
            );
        """)
        self._conn.commit()

    def save_messages(
        self, contact_name: str, messages: list[ChatMessage]
    ) -> int:
        """
        保存消息到数据库（自动去重）

        Args:
            contact_name: 联系人/群名
            messages: 消息列表

        Returns:
            新插入的消息数量
        """
        now = time.time()
        inserted = 0

        for msg in messages:
            try:
                self._conn.execute(
                    """INSERT OR IGNORE INTO messages
                       (contact, sender, content, msg_type, msg_time, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        contact_name,
                        msg.sender,
                        msg.content,
                        msg.msg_type,
                        msg.time,
                        now,
                    ),
                )
                if self._conn.total_changes:
                    inserted += 1
            except sqlite3.IntegrityError:
                pass  # 重复消息，跳过
            except Exception as e:
                logger.debug(f"保存消息失败: {e}")

        # 更新联系人记录
        self._conn.execute(
            """INSERT INTO contacts (name, last_read_at, message_count)
               VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                   last_read_at = excluded.last_read_at,
                   message_count = message_count + excluded.message_count""",
            (contact_name, now, inserted),
        )

        self._conn.commit()
        return inserted

    def query_messages(
        self,
        contact_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sender: str | None = None,
        keyword: str | None = None,
    ) -> list[dict]:
        """
        查询消息

        Args:
            contact_name: 按联系人过滤（None=所有）
            limit: 最大返回数
            offset: 偏移量
            sender: 按发送者过滤
            keyword: 按内容关键词搜索
        """
        conditions = []
        params = []

        if contact_name:
            conditions.append("contact = ?")
            params.append(contact_name)
        if sender:
            conditions.append("sender = ?")
            params.append(sender)
        if keyword:
            conditions.append("content LIKE ?")
            params.append(f"%{keyword}%")

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
            SELECT contact, sender, content, msg_type, msg_time, created_at
            FROM messages {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        cursor = self._conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_contacts(self) -> list[dict]:
        """获取所有已读取的联系人及其消息统计"""
        cursor = self._conn.execute(
            """SELECT name, last_read_at, message_count
               FROM contacts ORDER BY last_read_at DESC"""
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_stats(self) -> dict:
        """获取整体统计信息"""
        total_msgs = self._conn.execute(
            "SELECT COUNT(*) FROM messages"
        ).fetchone()[0]

        total_contacts = self._conn.execute(
            "SELECT COUNT(DISTINCT contact) FROM messages"
        ).fetchone()[0]

        top_contacts = self._conn.execute(
            """SELECT contact, COUNT(*) as cnt FROM messages
               GROUP BY contact ORDER BY cnt DESC LIMIT 10"""
        ).fetchall()

        return {
            "total_messages": total_msgs,
            "total_contacts": total_contacts,
            "top_contacts": [
                {"name": row[0], "count": row[1]} for row in top_contacts
            ],
            "db_path": self._db_path,
        }

    def export(
        self,
        output_path: str,
        contact_name: str | None = None,
        format: str = "json",
    ) -> str:
        """
        导出消息

        Args:
            output_path: 输出文件路径
            contact_name: 按联系人过滤
            format: "json" 或 "csv"

        Returns:
            实际输出文件路径
        """
        messages = self.query_messages(contact_name, limit=999999)

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        if format == "json":
            if not output.suffix:
                output = output.with_suffix(".json")
            output.write_text(
                json.dumps(messages, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        elif format == "csv":
            if not output.suffix:
                output = output.with_suffix(".csv")
            with open(output, "w", newline="", encoding="utf-8-sig") as f:
                if messages:
                    writer = csv.DictWriter(f, fieldnames=messages[0].keys())
                    writer.writeheader()
                    writer.writerows(messages)
        else:
            raise ValueError(f"不支持的格式: {format}")

        logger.info(f"已导出 {len(messages)} 条消息到: {output}")
        return str(output)

    def close(self):
        """关闭数据库连接"""
        self._conn.close()
