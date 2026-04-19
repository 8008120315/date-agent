import json
import re
from datetime import date
from typing import Any

from ..db import get_connection, now_iso


class MemoryService:
    _CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
        "goal": ("目标", "计划", "打算", "想要", "goal", "plan"),
        "task_progress": ("进展", "完成", "已做", "正在", "推进", "progress", "done"),
        "issues": ("问题", "阻碍", "困难", "卡住", "失败", "bug", "issue"),
        "preferences": ("偏好", "喜欢", "不喜欢", "希望", "习惯", "prefer"),
        "conclusions": ("结论", "决定", "总结", "下一步", "复盘", "therefore", "next"),
    }

    def write_memory(
        self,
        type_: str,
        content: str,
        summary: str,
        memory_date: date | None = None,
        metadata: dict | None = None,
    ) -> int:
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO memories (date, type, content, summary, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_date.isoformat() if memory_date else None,
                    type_,
                    content,
                    summary,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now_iso(),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        like = f"%{query.strip()}%"
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, date, type, summary, created_at
                FROM memories
                WHERE summary LIKE ? OR content LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (like, like, top_k),
            ).fetchall()
        return [dict(r) for r in rows]

    def recent(self, limit: int = 5) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, date, type, summary, created_at
                FROM memories
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_memories(
        self,
        *,
        type_: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT id, date, type, summary, content, metadata, created_at
            FROM memories
        """
        params: list[Any] = []
        if type_:
            query += " WHERE type = ?"
            params.append(type_)
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with get_connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            record["metadata"] = self._loads_metadata(record.get("metadata"))
            result.append(record)
        return result

    def list_reviews(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        return self.list_memories(type_="review", limit=limit, offset=offset)

    def query_reviews(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        query: str = "",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        where_clauses = ["type = 'review'"]
        params: list[Any] = []

        q = query.strip()
        if q:
            where_clauses.append("(summary LIKE ? OR content LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])

        if start_date:
            where_clauses.append("date >= ?")
            params.append(start_date)
        if end_date:
            where_clauses.append("date <= ?")
            params.append(end_date)

        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT id, date, type, summary, content, metadata, created_at
            FROM memories
            WHERE {where_sql}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        with get_connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._normalize_record(dict(row)) for row in rows]

    def get_review(self, record_id: int) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, date, type, summary, content, metadata, created_at
                FROM memories
                WHERE id = ? AND type = 'review'
                """,
                (record_id,),
            ).fetchone()
        if row is None:
            return None
        return self._normalize_record(dict(row))

    def update_review(self, *, record_id: int, summary: str, content: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE memories
                SET summary = ?, content = ?
                WHERE id = ? AND type = 'review'
                """,
                (summary, content, record_id),
            )
            conn.commit()
            if cursor.rowcount <= 0:
                return None
        return self.get_review(record_id)

    def delete_review(self, *, record_id: int) -> bool:
        with get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM memories WHERE id = ? AND type = 'review'",
                (record_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def _loads_metadata(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if raw is None:
            return {}
        try:
            parsed = json.loads(str(raw))
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _normalize_record(self, record: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(record)
        normalized["metadata"] = self._loads_metadata(normalized.get("metadata"))
        return normalized

    def ingest_dialogue_memory(
        self, dialogue_text: str, memory_date: date | None = None
    ) -> tuple[int, dict[str, list[str]]]:
        structured_memory = self._extract_structured_memory(dialogue_text)
        summary = self._build_structured_summary(structured_memory)
        memory_id = self.write_memory(
            type_="dialogue",
            content=dialogue_text,
            summary=summary,
            memory_date=memory_date,
            metadata={
                "structured_memory": structured_memory,
                "source": "dialogue_ingest_v1",
            },
        )
        return memory_id, structured_memory

    def _extract_structured_memory(self, dialogue_text: str) -> dict[str, list[str]]:
        structured: dict[str, list[str]] = {
            "goal": [],
            "task_progress": [],
            "issues": [],
            "preferences": [],
            "conclusions": [],
        }
        sentences = self._split_sentences(dialogue_text)
        for sentence in sentences:
            lower_sentence = sentence.lower()
            matched = False
            for category, keywords in self._CATEGORY_KEYWORDS.items():
                if any(keyword.lower() in lower_sentence for keyword in keywords):
                    structured[category].append(sentence)
                    matched = True
            if not matched and ("所以" in sentence or "因此" in sentence):
                structured["conclusions"].append(sentence)

        if not any(structured.values()) and sentences:
            structured["conclusions"].append(sentences[0])
        return structured

    def _split_sentences(self, text: str) -> list[str]:
        normalized = text.replace("\r\n", "\n")
        chunks = re.split(r"[\n\u3002\uff01\uff1f!?；;]+", normalized)
        return [c.strip() for c in chunks if c.strip()]

    def _build_structured_summary(self, structured_memory: dict[str, list[str]]) -> str:
        goal = structured_memory["goal"][0] if structured_memory["goal"] else "未明确目标"
        progress = (
            structured_memory["task_progress"][0]
            if structured_memory["task_progress"]
            else "未提及进展"
        )
        issue = structured_memory["issues"][0] if structured_memory["issues"] else "未提及问题"
        return f"目标: {goal} | 进展: {progress} | 问题: {issue}"
