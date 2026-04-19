from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from ..db import get_connection, now_iso
from ..schemas import ReviewRequest, ReviewResponse
from .llm_service import LLMService
from .memory_service import MemoryService


class ReviewService:
    def __init__(self) -> None:
        self.memory_service = MemoryService()
        self.llm_service = LLMService()

    def create_review(self, req: ReviewRequest) -> ReviewResponse:
        start_date, end_date, period_label = self._resolve_period(req)
        sessions = self._list_period_sessions(start_date=start_date, end_date=end_date)
        objective_data = self._resolve_objective_data(
            req=req,
            sessions=sessions,
            start_date=start_date,
            end_date=end_date,
        )
        covered_dates = objective_data["dates"]

        review_context = self._build_review_context(
            req=req,
            start_date=start_date,
            end_date=end_date,
            period_label=period_label,
            objective_data=objective_data,
        )

        summary, actions, source_model, fallback = self._generate_summary(
            period_label=period_label,
            start_date=start_date,
            end_date=end_date,
            review_context=review_context,
            objective_data=objective_data,
            subjective_input=self._build_subjective_input(req),
        )

        memory_id = 0
        if req.persist:
            memory_summary = f"[{start_date.isoformat()}~{end_date.isoformat()}] 复盘：{summary[:80]}"
            memory_id = self.memory_service.write_memory(
                type_="review",
                content=review_context,
                summary=memory_summary,
                memory_date=end_date,
                metadata={
                    "range_type": req.range_type,
                    "period_label": period_label,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "covered_dates": covered_dates,
                    "source_model": source_model,
                    "fallback": fallback,
                    "actions": actions,
                },
            )

        if req.persist and req.range_type == "day":
            self._upsert_daily_review(
                target_date=end_date,
                req=req,
                summary=summary,
                period_label=period_label,
            )

        return ReviewResponse(
            date=end_date,
            range_type=req.range_type,
            period_label=period_label,
            start_date=start_date,
            end_date=end_date,
            covered_dates=covered_dates,
            daily_summary=summary,
            tomorrow_actions=actions,
            source_model=source_model,
            fallback=fallback,
            memory_id=memory_id,
        )

    def _resolve_period(self, req: ReviewRequest) -> tuple[date, date, str]:
        anchor = req.date or date.today()

        if req.range_type == "day":
            return anchor, anchor, f"{anchor.isoformat()} 当日复盘"

        if req.range_type == "last_3_days":
            start = anchor - timedelta(days=2)
            return start, anchor, f"{start.isoformat()} 至 {anchor.isoformat()}（最近三天）"

        if req.range_type == "last_week":
            this_week_monday = anchor - timedelta(days=anchor.weekday())
            start = this_week_monday - timedelta(days=7)
            end = this_week_monday - timedelta(days=1)
            return start, end, f"{start.isoformat()} 至 {end.isoformat()}（上周）"

        if req.range_type == "custom":
            if not req.start_date or not req.end_date:
                raise RuntimeError("custom range requires start_date and end_date.")
            if req.end_date < req.start_date:
                raise RuntimeError("end_date must be greater than or equal to start_date.")
            span_days = (req.end_date - req.start_date).days + 1
            if span_days > 31:
                raise RuntimeError("custom range cannot exceed 31 days.")
            return (
                req.start_date,
                req.end_date,
                f"{req.start_date.isoformat()} 至 {req.end_date.isoformat()}（自定义）",
            )

        raise RuntimeError("Unsupported range_type.")

    def _list_period_sessions(self, *, start_date: date, end_date: date) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT date, goal_text, plan_json, review_text, summary_text
                FROM daily_sessions
                WHERE date >= ? AND date <= ?
                ORDER BY date ASC
                """,
                (start_date.isoformat(), end_date.isoformat()),
            ).fetchall()

        sessions: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            sessions.append(
                {
                    "date": str(record.get("date") or "").strip(),
                    "goal_text": str(record.get("goal_text") or "").strip(),
                    "plan_items": self._extract_plan_items(record.get("plan_json")),
                    "review_text": str(record.get("review_text") or "").strip(),
                    "summary_text": str(record.get("summary_text") or "").strip(),
                }
            )
        return sessions

    def _extract_plan_items(self, plan_json: Any) -> list[dict[str, Any]]:
        if not plan_json:
            return []

        try:
            parsed = json.loads(str(plan_json))
        except json.JSONDecodeError:
            return []

        if isinstance(parsed, list):
            raw_items = parsed
        elif isinstance(parsed, dict):
            raw_items = parsed.get("plan_items", [])
        else:
            raw_items = []

        if not isinstance(raw_items, list):
            return []

        items: list[dict[str, Any]] = []
        for index, row in enumerate(raw_items, start=1):
            if not isinstance(row, dict):
                continue
            title = str(row.get("title", "")).strip() or f"任务 {index}"
            priority = str(row.get("priority", "P2")).strip().upper() or "P2"
            status = str(row.get("progress_status", "todo")).strip().lower()
            if status not in {"todo", "partial", "done"}:
                status = "todo"
            percent = self._normalize_percent(row.get("progress_percent"), default=0)
            note = str(row.get("progress_note", "")).strip()
            checklist = self._extract_checklist(row.get("checklist"))

            # Derive progress from checklist when checklist is available.
            if checklist:
                done_count = sum(1 for item in checklist if item["is_done"])
                total_count = len(checklist)
                if done_count == 0:
                    status = "todo"
                    percent = 0
                elif done_count == total_count:
                    status = "done"
                    percent = 100
                else:
                    status = "partial"
                    percent = max(1, min(99, int(round(done_count * 100 / total_count))))
            else:
                if status == "done":
                    percent = 100
                elif status == "todo":
                    percent = 0
                elif percent <= 0 or percent >= 100:
                    percent = 50

            items.append(
                {
                    "title": title,
                    "priority": priority,
                    "status": status,
                    "percent": percent,
                    "note": note,
                    "checklist": checklist,
                }
            )

        items.sort(key=lambda item: (self._priority_rank(item["priority"]), item["title"]))
        return items

    def _extract_checklist(self, raw: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []

        if isinstance(raw, list):
            for row in raw:
                if isinstance(row, dict):
                    content = str(row.get("content", "")).strip()
                    if not content:
                        continue
                    items.append({"content": content, "is_done": bool(row.get("is_done", False))})
                    continue
                content = str(row).strip()
                if content:
                    items.append({"content": content, "is_done": False})
            return items[:10]

        if isinstance(raw, str):
            parts = re.split(r"[\n;,；、]+", raw)
            for part in parts:
                content = part.strip()
                if content:
                    items.append({"content": content, "is_done": False})
            return items[:10]

        return []

    def _build_review_context(
        self,
        *,
        req: ReviewRequest,
        start_date: date,
        end_date: date,
        period_label: str,
        objective_data: dict[str, Any],
    ) -> str:
        lines: list[str] = []
        lines.append(f"复盘范围：{period_label}")
        lines.append(f"开始日期：{start_date.isoformat()}")
        lines.append(f"结束日期：{end_date.isoformat()}")

        if req.focus_text.strip():
            lines.append(f"复盘关注点：{req.focus_text.strip()}")

        related_memory_refs = self._collect_related_memory_refs(
            focus_text=req.focus_text.strip(),
            period_label=period_label,
        )
        if related_memory_refs:
            lines.append("相关长期记忆：")
            for ref in related_memory_refs:
                lines.append(f"- {ref}")

        if req.done_text.strip() or req.undone_text.strip() or req.blockers_text.strip():
            lines.append("用户补充输入：")
            if req.done_text.strip():
                lines.append(f"- 已完成：{req.done_text.strip()}")
            if req.undone_text.strip():
                lines.append(f"- 未完成：{req.undone_text.strip()}")
            if req.blockers_text.strip():
                lines.append(f"- 阻塞：{req.blockers_text.strip()}")

        lines.append("客观任务数据（用于生成复盘）：")
        lines.append(self._format_objective_section("已完成任务", objective_data["done"]))
        lines.append(self._format_objective_section("未完成任务", objective_data["incomplete"]))
        lines.append(self._format_objective_section("阻塞任务", objective_data["blocked"]))
        return "\n".join(lines)

    def _generate_summary(
        self,
        *,
        period_label: str,
        start_date: date,
        end_date: date,
        review_context: str,
        objective_data: dict[str, Any],
        subjective_input: str,
    ) -> tuple[str, list[str], str, bool]:
        if not self.llm_service.is_ready():
            summary, actions = self._fallback_summary(
                period_label=period_label,
                objective_data=objective_data,
            )
            return summary, actions, "rule-based", True

        completed_json = json.dumps(objective_data["done"], ensure_ascii=False)
        incomplete_json = json.dumps(objective_data["incomplete"], ensure_ascii=False)
        blocked_json = json.dumps(objective_data["blocked"], ensure_ascii=False)
        subjective_text = subjective_input or "无"
        system_prompt = (
            "# Role\n"
            "你是一个客观、专业的个人效率教练。你需要根据用户提供的今日任务执行客观数据，以及用户的主观补充，生成一份结构化的复盘报告。\n\n"
            "# Constraints (严格遵守)\n"
            "1. 绝不捏造事实：如果“未完成”列表中为空，绝对不能在总结中说“有任务未完成”或编造不存在的任务。\n"
            "2. 数据一致性：你的总结必须 100% 贴合用户提供的客观 JSON 数据。\n"
            "3. 如果所有任务都已完成，请多给予鼓励，并分析高效率的原因；如果有未完成/阻塞，请客观分析风险并给出下一步行动建议。\n"
            "4. 禁止引用任何未出现在 Input Data 的任务名称、状态、数量或结论。\n\n"
            "# Output Format\n"
            "请严格按照以下 Markdown 格式输出：\n"
            "### 总体总结\n"
            "...\n"
            "### 关键亮点\n"
            "...\n"
            "### 主要风险\n"
            "...\n"
            "### 下一步行动\n"
            "..."
        )
        user_prompt = (
            f"复盘时间段：{period_label}（{start_date.isoformat()} ~ {end_date.isoformat()}）\n\n"
            "# Input Data\n"
            f"- 已完成任务：{completed_json}\n"
            f"- 未完成任务：{incomplete_json}\n"
            f"- 阻塞任务：{blocked_json}\n"
            f"- 本次重点关注与主观补充：{subjective_text}\n\n"
            "注意：若“主要风险”为空，请写“当前进度良好，无明显风险”。\n"
            "请只输出 Markdown 正文，不要额外解释。"
        )

        try:
            raw_reply, model_name = self.llm_service.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                history=None,
            )
            sections = self._extract_markdown_sections(raw_reply)
            summary = self._compose_summary_from_sections(sections)
            actions = self._parse_markdown_list_sections(sections.get("下一步行动", ""), limit=6)
            if not actions:
                actions = self._default_actions_from_objective(objective_data)
            return summary, actions, model_name, False
        except Exception:
            summary, actions = self._fallback_summary(
                period_label=period_label,
                objective_data=objective_data,
            )
            return summary, actions, "rule-based", True

    def _extract_json(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if fenced:
            cleaned = fenced.group(1).strip()
        else:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                cleaned = match.group(0).strip()

        payload = json.loads(cleaned)
        if not isinstance(payload, dict):
            raise RuntimeError("invalid json payload")
        return payload

    def _normalize_actions(self, raw: Any, *, limit: int = 5) -> list[str]:
        if isinstance(raw, list):
            rows = [str(item).strip() for item in raw if str(item).strip()]
        elif isinstance(raw, str):
            rows = [part.strip() for part in re.split(r"[\n;,；、]+", raw) if part.strip()]
        else:
            rows = []
        return rows[:limit]

    def _fallback_summary(self, *, period_label: str, objective_data: dict[str, Any]) -> tuple[str, list[str]]:
        done = objective_data["done"]
        incomplete = objective_data["incomplete"]
        blocked = objective_data["blocked"]
        total = len(done) + len(incomplete)
        summary = (
            f"{period_label}复盘：共梳理任务 {total} 项，"
            f"已完成 {len(done)} 项，未完成/部分完成 {len(incomplete)} 项，阻塞 {len(blocked)} 项。"
        )
        if not incomplete and not blocked:
            summary += "整体推进稳定，执行效率较好。"
        else:
            summary += "建议优先解决阻塞并收敛未完成项。"
        actions = self._default_actions_from_objective(objective_data)
        return summary, actions

    def _status_label(self, status: str, percent: int) -> str:
        if status == "done":
            return "已完成"
        if status == "partial":
            return f"部分完成({percent}%)"
        return "未完成"

    def _collect_related_memory_refs(self, *, focus_text: str, period_label: str) -> list[str]:
        query_text = focus_text or period_label
        refs = self.memory_service.build_memory_refs(
            query=query_text,
            top_k=4,
            fallback_recent=0,
            types=("review", "plan", "goal", "dialogue"),
        )
        if not refs:
            return []
        if len(refs) == 1 and refs[0].startswith("No related memory found"):
            return []
        return refs

    def _build_subjective_input(self, req: ReviewRequest) -> str:
        rows = []
        if req.focus_text.strip():
            rows.append(f"关注点：{req.focus_text.strip()}")
        if req.done_text.strip():
            rows.append(f"已完成补充：{req.done_text.strip()}")
        if req.undone_text.strip():
            rows.append(f"未完成补充：{req.undone_text.strip()}")
        if req.blockers_text.strip():
            rows.append(f"阻塞补充：{req.blockers_text.strip()}")
        return "；".join(rows) if rows else "无"

    def _resolve_objective_data(
        self,
        *,
        req: ReviewRequest,
        sessions: list[dict[str, Any]],
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        use_frontend = bool(
            req.frontend_completed_list
            or req.frontend_incomplete_list
            or req.frontend_blocked_list
            or req.frontend_dates
        )
        if use_frontend:
            done = [self._normalize_objective_item(item.model_dump(), default_status="done") for item in req.frontend_completed_list]
            incomplete = [
                self._normalize_objective_item(item.model_dump(), default_status="todo")
                for item in req.frontend_incomplete_list
            ]
            blocked = [self._normalize_objective_item(item.model_dump(), default_status="todo") for item in req.frontend_blocked_list]
            dates = sorted({str(item).strip() for item in req.frontend_dates if str(item).strip()})
            if not dates:
                date_set = {row["date"] for row in [*done, *incomplete, *blocked] if row.get("date")}
                dates = sorted(date_set) if date_set else self._enumerate_dates(start_date, end_date)
            return {"done": done, "incomplete": incomplete, "blocked": blocked, "dates": dates}

        done, incomplete, blocked = self._build_objective_from_sessions(sessions)
        dates = [row["date"] for row in sessions if row.get("date")]
        if not dates:
            dates = self._enumerate_dates(start_date, end_date)
        return {"done": done, "incomplete": incomplete, "blocked": blocked, "dates": dates}

    def _build_objective_from_sessions(self, sessions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        done: list[dict[str, Any]] = []
        incomplete: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        for day in sessions:
            day_date = str(day.get("date") or "").strip()
            for item in day.get("plan_items", []):
                raw = {
                    "date": day_date,
                    "title": item.get("title", ""),
                    "priority": item.get("priority", "P2"),
                    "status": item.get("status", "todo"),
                    "percent": item.get("percent", 0),
                    "note": item.get("note", ""),
                }
                normalized = self._normalize_objective_item(raw, default_status="todo")
                if normalized["status"] == "done":
                    done.append(normalized)
                else:
                    incomplete.append(normalized)
                if normalized["note"] and re.search(r"(阻塞|卡住|等待|依赖|延期|风险|blocked|blocker)", normalized["note"], re.IGNORECASE):
                    blocked.append(normalized)
        return done, incomplete, blocked

    def _normalize_objective_item(self, raw: dict[str, Any], *, default_status: str) -> dict[str, Any]:
        title = str(raw.get("title") or "").strip() or "未命名任务"
        priority = str(raw.get("priority") or "P2").strip().upper() or "P2"
        status = str(raw.get("status") or default_status).strip().lower()
        if status not in {"done", "partial", "todo"}:
            status = default_status if default_status in {"done", "partial", "todo"} else "todo"
        percent = self._normalize_percent(raw.get("percent"), default=100 if status == "done" else 0)
        if status == "done":
            percent = 100
        elif status == "todo":
            percent = 0
        elif percent <= 0:
            percent = 50
        return {
            "date": str(raw.get("date") or "").strip(),
            "title": title,
            "priority": priority,
            "status": status,
            "percent": percent,
            "note": str(raw.get("note") or "").strip(),
        }

    def _enumerate_dates(self, start_date: date, end_date: date) -> list[str]:
        days = []
        cursor = start_date
        while cursor <= end_date and len(days) < 31:
            days.append(cursor.isoformat())
            cursor = cursor + timedelta(days=1)
        return days

    def _format_objective_section(self, title: str, rows: list[dict[str, Any]]) -> str:
        lines = [f"{title}："]
        if not rows:
            lines.append("- 无")
            return "\n".join(lines)
        for idx, row in enumerate(rows, start=1):
            status_label = self._status_label(row["status"], row["percent"])
            prefix = f"[{row['date']}] " if row.get("date") else ""
            lines.append(f"- {idx}. {prefix}[{row['priority']}] {row['title']} | 状态：{status_label}")
            if row.get("note"):
                lines.append(f"  备注：{row['note']}")
        return "\n".join(lines)

    def _extract_markdown_sections(self, text: str) -> dict[str, str]:
        cleaned = str(text or "").strip()
        sections: dict[str, str] = {}
        pattern = re.compile(
            r"###\s*(总体总结|关键亮点|主要风险|下一步行动)\s*\n([\s\S]*?)(?=\n###\s*(?:总体总结|关键亮点|主要风险|下一步行动)\s*\n|$)"
        )
        for match in pattern.finditer(cleaned):
            key = match.group(1).strip()
            value = match.group(2).strip()
            sections[key] = value
        return sections

    def _parse_markdown_list_sections(self, text: str, *, limit: int = 6) -> list[str]:
        rows: list[str] = []
        for raw in str(text or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            bullet = re.match(r"^[-*]\s+(.+)$", line)
            ordered = re.match(r"^\d+\.\s+(.+)$", line)
            if bullet:
                rows.append(bullet.group(1).strip())
            elif ordered:
                rows.append(ordered.group(1).strip())
            else:
                rows.append(line)
        normalized = [row for row in rows if row][:limit]
        return normalized

    def _compose_summary_from_sections(self, sections: dict[str, str]) -> str:
        overall = str(sections.get("总体总结", "")).strip()
        highlights = self._parse_markdown_list_sections(sections.get("关键亮点", ""), limit=6)
        risks = self._parse_markdown_list_sections(sections.get("主要风险", ""), limit=6)

        chunks = [overall] if overall else []
        if highlights:
            chunks.append("关键亮点：" + "；".join(highlights))
        if risks:
            chunks.append("主要风险：" + "；".join(risks))
        if not chunks:
            raise RuntimeError("empty markdown sections")
        return "\n\n".join(chunks)

    def _default_actions_from_objective(self, objective_data: dict[str, Any]) -> list[str]:
        incomplete = objective_data.get("incomplete", [])
        blocked = objective_data.get("blocked", [])
        if not incomplete and not blocked:
            return [
                "保持当前节奏，继续按优先级推进下一阶段任务。",
                "复盘高效率做法并固化为下周执行模板。",
                "预留 30 分钟做风险预警和计划缓冲。",
            ]
        return [
            "把未完成事项拆成最小可执行步骤，并明确完成截止时间。",
            "针对阻塞项逐条指定负责人/依赖项和解除时间点。",
            "优先处理高优先级任务，降低次要任务占用时间。",
        ]

    def _normalize_percent(self, value: Any, *, default: int = 0) -> int:
        try:
            number = int(float(value))
            return max(0, min(100, number))
        except (TypeError, ValueError):
            return default

    def _priority_rank(self, value: str) -> int:
        if value == "P0":
            return 0
        if value == "P1":
            return 1
        return 2

    def _upsert_daily_review(
        self,
        *,
        target_date: date,
        req: ReviewRequest,
        summary: str,
        period_label: str,
    ) -> None:
        review_text = (
            f"range: {period_label}\n"
            f"focus: {req.focus_text}\n"
            f"done: {req.done_text}\n"
            f"undone: {req.undone_text}\n"
            f"blockers: {req.blockers_text}"
        )
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO daily_sessions (date, review_text, summary_text, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    review_text=excluded.review_text,
                    summary_text=excluded.summary_text,
                    updated_at=excluded.updated_at
                """,
                (target_date.isoformat(), review_text, summary, now_iso()),
            )
            conn.commit()
