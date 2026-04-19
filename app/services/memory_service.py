import json
import re
from datetime import date, datetime
from typing import Any

from ..db import get_connection, now_iso


class MemoryService:
    _NO_MEMORY_FALLBACK = "No related memory found. Planned from current goal only."
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

    def search(
        self,
        query: str,
        top_k: int = 5,
        *,
        types: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return self.recent(limit=top_k, types=types)

        query_terms = self._extract_query_terms(normalized_query)
        rows = self._fetch_search_candidates(
            query=normalized_query,
            query_terms=query_terms,
            types=types,
            limit=max(top_k * 5, 24),
        )
        if not rows:
            return []

        scored = []
        for row in rows:
            score = self._score_row(row=row, query=normalized_query, query_terms=query_terms)
            if score <= 0:
                continue
            scored.append((score, row))

        if not scored:
            return []

        scored.sort(
            key=lambda item: (
                item[0],
                str(item[1].get("date") or ""),
                str(item[1].get("created_at") or ""),
                int(item[1].get("id") or 0),
            ),
            reverse=True,
        )
        return [self._strip_content(dict(row)) for _, row in scored[:top_k]]

    def recent(
        self,
        limit: int = 5,
        *,
        types: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        base_sql = """
            SELECT id, date, type, summary, content, created_at
            FROM memories
        """
        params: list[Any] = []
        if types:
            placeholders = ", ".join("?" for _ in types)
            base_sql += f" WHERE type IN ({placeholders})"
            params.extend(types)
        base_sql += " ORDER BY date DESC, created_at DESC, id DESC LIMIT ?"
        params.append(limit)

        with get_connection() as conn:
            rows = conn.execute(base_sql, tuple(params)).fetchall()
        return [self._strip_content(dict(r)) for r in rows]

    def build_memory_refs(
        self,
        *,
        query: str,
        top_k: int = 5,
        fallback_recent: int = 3,
        types: tuple[str, ...] | None = None,
    ) -> list[str]:
        direct_hits = self.search(query=query, top_k=top_k, types=types)
        refs = [self._format_memory_ref(row) for row in direct_hits if row.get("summary")]
        if refs:
            return refs

        if fallback_recent > 0:
            recent_rows = self.recent(limit=fallback_recent, types=types)
            refs = [self._format_memory_ref(row) for row in recent_rows if row.get("summary")]
            if refs:
                return refs

        return [self._NO_MEMORY_FALLBACK]

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

    def _fetch_search_candidates(
        self,
        *,
        query: str,
        query_terms: list[str],
        types: tuple[str, ...] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        where_clauses: list[str] = []
        params: list[Any] = []

        if types:
            placeholders = ", ".join("?" for _ in types)
            where_clauses.append(f"type IN ({placeholders})")
            params.extend(types)

        search_terms = [query] + query_terms
        if search_terms:
            like_clauses: list[str] = []
            for token in search_terms[:24]:
                like_clauses.append("summary LIKE ?")
                like_clauses.append("content LIKE ?")
                like = f"%{token}%"
                params.extend([like, like])
            where_clauses.append(f"({' OR '.join(like_clauses)})")

        sql = """
            SELECT id, date, type, summary, content, created_at
            FROM memories
        """
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        sql += " ORDER BY date DESC, created_at DESC, id DESC LIMIT ?"
        params.append(max(1, min(200, limit)))

        with get_connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def _extract_query_terms(self, query: str) -> list[str]:
        tokens: list[str] = []
        seen: set[str] = set()

        def add_token(raw: str) -> None:
            token = str(raw or "").strip()
            if len(token) < 2:
                return
            key = token.lower()
            if key in seen:
                return
            seen.add(key)
            tokens.append(token)

        for part in self._split_keywords(query):
            add_token(part)
            if re.search(r"[\u4e00-\u9fff]", part):
                for han_token in self._expand_han_terms(part):
                    add_token(han_token)

        return tokens[:24]

    def _split_keywords(self, text: str) -> list[str]:
        normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", str(text or "").strip())
        return [part for part in normalized.split() if part]

    def _expand_han_terms(self, text: str) -> list[str]:
        chunks = re.findall(r"[\u4e00-\u9fff]{2,}", text)
        expanded: list[str] = []
        for chunk in chunks:
            if len(chunk) <= 4:
                expanded.append(chunk)
                continue
            # Build short n-grams to improve fuzzy retrieval for long Chinese phrases.
            for size in (2, 3, 4):
                for idx in range(0, len(chunk) - size + 1):
                    expanded.append(chunk[idx : idx + size])
                    if len(expanded) >= 18:
                        return expanded
        return expanded

    def _score_row(
        self,
        *,
        row: dict[str, Any],
        query: str,
        query_terms: list[str],
    ) -> float:
        summary = str(row.get("summary") or "").lower()
        content = str(row.get("content") or "").lower()
        normalized_query = query.lower()
        score = 0.0

        if normalized_query and normalized_query in summary:
            score += 6.0
        if normalized_query and normalized_query in content:
            score += 4.0

        for term in query_terms:
            lower_term = term.lower()
            if lower_term in summary:
                score += 2.0
            if lower_term in content:
                score += 1.0

        memory_type = str(row.get("type") or "").lower()
        if memory_type == "goal":
            score += 0.6
        elif memory_type == "plan":
            score += 0.5
        elif memory_type == "review":
            score += 0.4
        elif memory_type == "dialogue":
            score += 0.2

        score += self._recency_bonus(row)
        return score

    def _recency_bonus(self, row: dict[str, Any]) -> float:
        dt = self._parse_row_timestamp(row)
        if dt is None:
            return 0.0
        days_ago = max(0.0, (datetime.now() - dt).total_seconds() / 86400)
        if days_ago <= 1:
            return 2.0
        if days_ago <= 3:
            return 1.6
        if days_ago <= 7:
            return 1.1
        if days_ago <= 30:
            return 0.6
        return 0.2

    def _parse_row_timestamp(self, row: dict[str, Any]) -> datetime | None:
        date_text = str(row.get("date") or "").strip()
        if date_text:
            try:
                return datetime.strptime(date_text, "%Y-%m-%d")
            except ValueError:
                pass

        created_at = str(row.get("created_at") or "").strip()
        if not created_at:
            return None
        try:
            # Support "YYYY-MM-DDTHH:MM:SS" and with timezone suffix.
            cleaned = created_at.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(cleaned)
            if parsed.tzinfo is not None:
                return parsed.astimezone().replace(tzinfo=None)
            return parsed
        except ValueError:
            return None

    def _strip_content(self, row: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        normalized.pop("content", None)
        return normalized

    def _format_memory_ref(self, row: dict[str, Any]) -> str:
        summary = str(row.get("summary") or "").strip()
        if not summary:
            return ""
        memory_type = str(row.get("type") or "").strip() or "memory"
        date_text = str(row.get("date") or "").strip() or str(row.get("created_at") or "").strip()[:10] or "N/A"
        return f"[{memory_type}|{date_text}] {summary}"
