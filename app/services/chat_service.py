from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..db import get_connection, now_iso


Role = Literal["user", "assistant"]


@dataclass
class ChatMessage:
    id: int
    conversation_id: str
    turn_id: str
    role: Role
    content: str
    model: str | None
    fallback: bool
    created_at: str


@dataclass
class ChatConversation:
    conversation_id: str
    latest_message: str
    updated_at: str
    message_count: int


class ChatService:
    def append_message(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        role: Role,
        content: str,
        model: str | None = None,
        fallback: bool = False,
    ) -> int:
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO chat_messages
                (conversation_id, turn_id, role, content, model, fallback, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    turn_id,
                    role,
                    content,
                    model,
                    1 if fallback else 0,
                    now_iso(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def list_messages(self, *, conversation_id: str, limit: int = 200) -> list[ChatMessage]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, conversation_id, turn_id, role, content, model, fallback, created_at
                FROM chat_messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        rows = list(reversed(rows))
        return [
            ChatMessage(
                id=int(row["id"]),
                conversation_id=row["conversation_id"],
                turn_id=row["turn_id"],
                role=row["role"],
                content=row["content"],
                model=row["model"],
                fallback=bool(row["fallback"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def context_messages(
        self, *, conversation_id: str, limit: int = 20
    ) -> list[dict[str, str]]:
        messages = self.list_messages(conversation_id=conversation_id, limit=limit)
        return [{"role": m.role, "content": m.content} for m in messages]

    def list_conversations(self, *, limit: int = 100) -> list[ChatConversation]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT m.conversation_id,
                       m.content AS latest_message,
                       m.created_at AS updated_at,
                       c.message_count
                FROM chat_messages m
                JOIN (
                    SELECT conversation_id,
                           MAX(id) AS latest_id,
                           COUNT(*) AS message_count
                    FROM chat_messages
                    GROUP BY conversation_id
                ) c
                  ON c.latest_id = m.id
                ORDER BY m.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            ChatConversation(
                conversation_id=row["conversation_id"],
                latest_message=row["latest_message"],
                updated_at=row["updated_at"],
                message_count=int(row["message_count"]),
            )
            for row in rows
        ]

    def conversation_exists(self, *, conversation_id: str) -> bool:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM chat_messages
                WHERE conversation_id = ?
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        return row is not None

    def rename_conversation(self, *, conversation_id: str, new_conversation_id: str) -> int:
        with get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE chat_messages
                SET conversation_id = ?
                WHERE conversation_id = ?
                """,
                (new_conversation_id, conversation_id),
            )
            conn.commit()
            return int(cur.rowcount)

    def delete_conversation(self, *, conversation_id: str) -> int:
        with get_connection() as conn:
            cur = conn.execute(
                """
                DELETE FROM chat_messages
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            )
            conn.commit()
            return int(cur.rowcount)
