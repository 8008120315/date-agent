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
        covered_dates = [row["date"] for row in sessions]

        review_context = self._build_review_context(
            req=req,
            start_date=start_date,
            end_date=end_date,
            period_label=period_label,
            sessions=sessions,
        )

        summary, actions, source_model, fallback = self._generate_summary(
            period_label=period_label,
            start_date=start_date,
            end_date=end_date,
            review_context=review_context,
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
        sessions: list[dict[str, Any]],
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

        if not sessions:
            lines.append("区间内暂无已保存任务清单记录。")
            return "\n".join(lines)

        lines.append("任务完成情况：")
        for day in sessions:
            day_date = day["date"]
            goal_text = day["goal_text"] or "（未填写目标）"
            lines.append(f"\n[{day_date}] 目标：{goal_text}")

            plan_items = day["plan_items"]
            if not plan_items:
                lines.append("- 当日暂无任务项")
            else:
                for idx, item in enumerate(plan_items, start=1):
                    status_label = self._status_label(item["status"], item["percent"])
                    lines.append(
                        f"- 任务{idx} [{item['priority']}] {item['title']} | 状态：{status_label}"
                    )
                    if item["note"]:
                        lines.append(f"  备注：{item['note']}")
                    checklist = item["checklist"]
                    if checklist:
                        checklist_line = "；".join(
                            f"{'[x]' if sub['is_done'] else '[ ]'} {sub['content']}" for sub in checklist
                        )
                        lines.append(f"  子清单：{checklist_line}")

            if day["review_text"]:
                lines.append(f"- 已有复盘原文：{day['review_text']}")
            if day["summary_text"]:
                lines.append(f"- 已有复盘摘要：{day['summary_text']}")

        return "\n".join(lines)

    def _generate_summary(
        self,
        *,
        period_label: str,
        start_date: date,
        end_date: date,
        review_context: str,
    ) -> tuple[str, list[str], str, bool]:
        if not self.llm_service.is_ready():
            summary, actions = self._fallback_summary(
                period_label=period_label,
                review_context=review_context,
            )
            return summary, actions, "rule-based", True

        system_prompt = (
            "你是复盘助手。请严格返回 JSON，不要输出其他文字。\n"
            "JSON schema:\n"
            "{\n"
            '  "summary": "...",\n'
            '  "actions": ["...", "..."],\n'
            '  "highlights": ["..."],\n'
            '  "risks": ["..."]\n'
            "}\n"
            "要求：\n"
            "1) summary 给出整体结论，150-260字；\n"
            "2) actions 输出 3-5 条可执行下一步；\n"
            "3) highlights/risk 各 2-4 条；\n"
            "4) 总结必须充分引用输入中的任务完成状态、备注、子清单信息；\n"
            "5) 使用简体中文。"
        )
        user_prompt = (
            f"复盘时间段：{period_label}\n"
            f"起止：{start_date.isoformat()} ~ {end_date.isoformat()}\n"
            "以下是复盘数据，请基于这些数据生成总结：\n"
            f"{review_context}\n"
            "请返回 JSON。"
        )

        try:
            raw_reply, model_name = self.llm_service.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                history=None,
            )
            payload = self._extract_json(raw_reply)
            summary = str(payload.get("summary", "")).strip()
            if not summary:
                raise RuntimeError("summary is empty")

            actions_raw = payload.get("actions", [])
            actions = self._normalize_actions(actions_raw)
            if not actions:
                actions = ["围绕核心未完成项安排下一个最小可执行步骤。"]

            highlights = self._normalize_actions(payload.get("highlights", []), limit=4)
            risks = self._normalize_actions(payload.get("risks", []), limit=4)

            if highlights:
                summary = f"{summary}\n\n关键亮点：" + "；".join(highlights)
            if risks:
                summary = f"{summary}\n\n主要风险：" + "；".join(risks)

            return summary, actions, model_name, False
        except Exception:
            summary, actions = self._fallback_summary(
                period_label=period_label,
                review_context=review_context,
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

    def _fallback_summary(self, *, period_label: str, review_context: str) -> tuple[str, list[str]]:
        lines = review_context.splitlines()
        task_lines = [line for line in lines if line.strip().startswith("- 任务")]
        done_count = len([line for line in task_lines if "状态：已完成" in line])
        partial_count = len([line for line in task_lines if "状态：部分完成" in line])
        todo_count = len([line for line in task_lines if "状态：未完成" in line])

        summary = (
            f"{period_label}复盘：共梳理任务 {len(task_lines)} 项，"
            f"已完成 {done_count} 项，部分完成 {partial_count} 项，未完成 {todo_count} 项。"
            "建议优先收敛未完成项，并结合备注中的阻塞信息安排下一阶段动作。"
        )
        actions = [
            "将所有未完成任务拆成 30-90 分钟可执行步骤并安排到日程。",
            "针对备注里出现的阻塞逐条给出解决动作和截止时间。",
            "把高优先级任务的完成定义写成可验收结果，再开始执行。",
        ]
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
