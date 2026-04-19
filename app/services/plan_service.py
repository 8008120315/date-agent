from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import date
from typing import Any
from uuid import uuid4

from ..config import build_local_context_block, get_settings
from ..db import get_connection, now_iso
from ..schemas import (
    PlanDraftAssignRequest,
    PlanDraftAssignResponse,
    PlanDraftEnrichRequest,
    PlanDraftEnrichResponse,
    PlanDraftGenerateRequest,
    PlanDraftGenerateResponse,
    PlanDraftTask,
    PlanChecklistItem,
    PlanItemCreateRequest,
    PlanItemDeleteRequest,
    PlanItemMoveResponse,
    PlanItemOptimizeRequest,
    PlanItemOptimizeResponse,
    PlanItemRescheduleRequest,
    PlanDayCommitRequest,
    PlanItem,
    PlanItemRegenerateRequest,
    PlanItemProgressUpdateRequest,
    PlanItemSplitRescheduleRequest,
    PlanRequest,
    PlanResponse,
)
from .llm_service import LLMService
from .memory_service import MemoryService
from .schedule_service import ScheduleService


class PlanService:
    def __init__(self) -> None:
        self.memory_service = MemoryService()
        self.llm_service = LLMService()
        self.schedule_service = ScheduleService()
        self.settings = get_settings()

    def create_plan(self, req: PlanRequest) -> PlanResponse:
        target_date = req.date or date.today()
        if target_date < date.today():
            raise RuntimeError("Cannot generate plan for a past date. Please choose today or a future date.")
        memory_refs = self._collect_memory_refs(req.goal_text)
        fixed_schedule_refs = self._collect_fixed_schedule_refs(target_date)

        plan_items, note, source_model, fallback = self._generate_plan(
            goal_text=req.goal_text,
            target_date=target_date,
            memory_refs=memory_refs,
            fixed_schedule_refs=fixed_schedule_refs,
        )
        plan_items = self._sort_plan_items(plan_items)
        plan_items_dump = [item.model_dump() for item in plan_items]

        plan_payload = {
            "goal_text": req.goal_text,
            "plan_items": plan_items_dump,
            "fixed_schedule_refs": fixed_schedule_refs,
            "note": note,
            "source_model": source_model,
            "fallback": fallback,
        }

        self.memory_service.write_memory(
            type_="plan",
            content=json.dumps(plan_payload, ensure_ascii=False),
            summary=f"[{target_date.isoformat()}] Plan generated for: {req.goal_text}",
            memory_date=target_date,
            metadata={
                "source_model": source_model,
                "fallback": fallback,
                "fixed_schedule_refs": fixed_schedule_refs,
            },
        )

        self._upsert_daily_plan(
            target_date=target_date,
            goal_text=req.goal_text,
            plan_payload=plan_payload,
        )

        return PlanResponse(
            date=target_date,
            goal_text=req.goal_text,
            plan_items=plan_items,
            memory_refs=memory_refs,
            fixed_schedule_refs=fixed_schedule_refs,
            note=note,
        )

    def get_plan(self, target_date: date) -> PlanResponse:
        payload = self._load_daily_plan_payload(target_date)
        if payload is None:
            return PlanResponse(
                date=target_date,
                goal_text="",
                plan_items=[],
                memory_refs=[],
                fixed_schedule_refs=[],
                note="",
            )

        return PlanResponse(
            date=target_date,
            goal_text=payload["goal_text"],
            plan_items=self._sort_plan_items([PlanItem(**row) for row in payload["plan_items"]]),
            memory_refs=[],
            fixed_schedule_refs=payload["fixed_schedule_refs"],
            note=payload["note"],
        )

    def update_plan_item_progress(self, req: PlanItemProgressUpdateRequest) -> PlanResponse:
        payload = self._load_daily_plan_payload(req.date)
        if payload is None:
            raise RuntimeError("No daily plan found for this date.")

        items = payload["plan_items"]
        if req.item_index >= len(items):
            raise RuntimeError("item_index is out of range.")

        row = dict(items[req.item_index])
        provided = getattr(req, "model_fields_set", set())
        if "title" in provided:
            row["title"] = str(req.title or "").strip()
        if "priority" in provided:
            row["priority"] = str(req.priority or "").strip().upper()
        if "estimate_hours" in provided:
            row["estimate_hours"] = req.estimate_hours
        if "done_definition" in provided:
            row["done_definition"] = str(req.done_definition or "").strip()
        if "checklist" in provided:
            row["checklist"] = [
                item.model_dump() if isinstance(item, PlanChecklistItem) else item
                for item in (req.checklist or [])
            ]
            if "progress_status" not in provided:
                # Child-item toggles should be able to drive overall task status.
                row["progress_status"] = "partial"
                row["progress_percent"] = None
        if "progress_status" in provided:
            row["progress_status"] = self._normalize_progress_status(req.progress_status)
        if "progress_percent" in provided:
            row["progress_percent"] = req.progress_percent
        if "progress_note" in provided:
            row["progress_note"] = str(req.progress_note or "").strip()

        items[req.item_index] = self._normalize_plan_item_dict(row, req.item_index + 1)
        items = self._sort_plan_item_dicts(items)

        updated_payload = {
            "goal_text": payload["goal_text"],
            "plan_items": items,
            "fixed_schedule_refs": payload["fixed_schedule_refs"],
            "note": payload["note"],
            "source_model": payload.get("source_model", "stored"),
            "fallback": payload.get("fallback", False),
        }
        self._upsert_daily_plan(
            target_date=req.date,
            goal_text=payload["goal_text"],
            plan_payload=updated_payload,
        )

        return self.get_plan(req.date)

    def commit_plan_items(self, req: PlanDayCommitRequest) -> PlanResponse:
        payload = self._load_daily_plan_payload(req.date)
        if payload is None:
            raise RuntimeError("No daily plan found for this date.")

        incoming = req.plan_items or []
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(incoming, start=1):
            raw = item.model_dump() if isinstance(item, PlanItem) else item
            if not isinstance(raw, dict):
                continue
            normalized.append(self._normalize_plan_item_dict(raw, index))

        updated_payload = {
            "goal_text": str(req.goal_text if req.goal_text is not None else payload["goal_text"]).strip(),
            "plan_items": self._sort_plan_item_dicts(normalized),
            "fixed_schedule_refs": payload["fixed_schedule_refs"],
            "note": payload["note"],
            "source_model": payload.get("source_model", "stored"),
            "fallback": payload.get("fallback", False),
        }
        self._upsert_daily_plan(
            target_date=req.date,
            goal_text=updated_payload["goal_text"],
            plan_payload=updated_payload,
        )
        return self.get_plan(req.date)

    def regenerate_plan_item(self, req: PlanItemRegenerateRequest) -> PlanResponse:
        payload = self._load_daily_plan_payload(req.date)
        if payload is None:
            raise RuntimeError("No daily plan found for this date.")

        items = payload["plan_items"]
        if req.item_index >= len(items):
            raise RuntimeError("item_index is out of range.")

        if req.source_item is not None:
            source_raw = req.source_item.model_dump() if isinstance(req.source_item, PlanItem) else req.source_item
            current = self._normalize_plan_item_dict(source_raw, req.item_index + 1)
        else:
            current = dict(items[req.item_index])
        goal_text = str(payload.get("goal_text", "")).strip()
        target_date = req.date
        memory_refs = self._collect_memory_refs(goal_text or current.get("title", ""))
        fixed_schedule_refs = self._collect_fixed_schedule_refs(target_date)

        if req.action == "split":
            updated = self._regenerate_item_split(
                goal_text=goal_text,
                target_date=target_date,
                current_item=current,
                memory_refs=memory_refs,
                fixed_schedule_refs=fixed_schedule_refs,
            )
        else:
            updated = self._regenerate_item_replace(
                goal_text=goal_text,
                target_date=target_date,
                current_item=current,
                memory_refs=memory_refs,
                fixed_schedule_refs=fixed_schedule_refs,
            )

        items[req.item_index] = self._normalize_plan_item_dict(updated, req.item_index + 1)
        items = self._sort_plan_item_dicts(items)

        updated_payload = {
            "goal_text": payload["goal_text"],
            "plan_items": items,
            "fixed_schedule_refs": payload["fixed_schedule_refs"],
            "note": payload["note"],
            "source_model": payload.get("source_model", "stored"),
            "fallback": payload.get("fallback", False),
        }
        self._upsert_daily_plan(
            target_date=req.date,
            goal_text=payload["goal_text"],
            plan_payload=updated_payload,
        )

        return self.get_plan(req.date)

    def optimize_plan_item(self, req: PlanItemOptimizeRequest) -> PlanItemOptimizeResponse:
        source_row = {
            "title": req.title,
            "priority": req.priority,
            "estimate_hours": req.estimate_hours,
            "done_definition": req.done_definition,
            "checklist": [item.model_dump() if isinstance(item, PlanChecklistItem) else item for item in req.checklist],
            "progress_status": req.progress_status,
            "progress_percent": req.progress_percent,
            "progress_note": req.progress_note,
        }
        current_item = self._normalize_plan_item_dict(source_row, 1)

        target_date = req.date or date.today()
        goal_text = str(req.goal_text or "").strip()
        if req.date and not goal_text:
            payload = self._load_daily_plan_payload(req.date)
            if payload is not None:
                goal_text = str(payload.get("goal_text", "")).strip()

        memory_refs = self._collect_memory_refs(goal_text or current_item.get("title", ""))
        fixed_schedule_refs = self._collect_fixed_schedule_refs(target_date)
        optimized = self._regenerate_item_replace(
            goal_text=goal_text,
            target_date=target_date,
            current_item=current_item,
            memory_refs=memory_refs,
            fixed_schedule_refs=fixed_schedule_refs,
        )
        source_model = self.settings.chat_model if self.llm_service.is_ready() else "rule-based"
        return PlanItemOptimizeResponse(
            item=PlanItem(**optimized),
            item_index=req.item_index,
            source_model=source_model,
            fallback=not self.llm_service.is_ready(),
            note="仅返回当前任务优化结果，不会修改其他任务。",
        )

    def generate_draft_pool(self, req: PlanDraftGenerateRequest) -> PlanDraftGenerateResponse:
        reference_date = date.today()
        span_days = self._infer_draft_span_days(req.goal_text)
        memory_refs = self._collect_memory_refs(req.goal_text)
        fixed_refs = self._collect_fixed_schedule_refs(reference_date)
        for cursor in range(1, min(span_days, 7)):
            date_cursor = reference_date.fromordinal(reference_date.toordinal() + cursor)
            for row in self._collect_fixed_schedule_refs(date_cursor):
                if row not in fixed_refs:
                    fixed_refs.append(row)

        tasks, note, source_model, fallback = self._generate_draft_tasks(
            goal_text=req.goal_text,
            reference_date=reference_date,
            span_days=span_days,
            memory_refs=memory_refs,
            fixed_schedule_refs=fixed_refs[:12],
        )

        draft_tasks = [
            PlanDraftTask(draft_id=f"draft-{uuid4().hex[:12]}", **task.model_dump()) for task in tasks
        ]
        return PlanDraftGenerateResponse(
            reference_date=reference_date,
            inferred_span_days=span_days,
            goal_text=req.goal_text,
            draft_tasks=draft_tasks,
            source_model=source_model,
            fallback=fallback,
            note=note,
        )

    def generate_draft_pool_stream(self, req: PlanDraftGenerateRequest) -> Iterator[tuple[str, dict[str, Any]]]:
        reference_date = date.today()
        span_days = self._infer_draft_span_days(req.goal_text)
        memory_refs = self._collect_memory_refs(req.goal_text)
        fixed_refs = self._collect_fixed_schedule_refs(reference_date)
        for cursor in range(1, min(span_days, 7)):
            date_cursor = reference_date.fromordinal(reference_date.toordinal() + cursor)
            for row in self._collect_fixed_schedule_refs(date_cursor):
                if row not in fixed_refs:
                    fixed_refs.append(row)

        target_count = max(4, min(24, span_days * 3))
        yield (
            "start",
            {
                "reference_date": reference_date.isoformat(),
                "inferred_span_days": span_days,
                "target_count": target_count,
                "goal_text": req.goal_text,
                "message": "开始生成任务草稿池",
            },
        )

        tasks, note, source_model, fallback = self._generate_draft_task_outlines(
            goal_text=req.goal_text,
            reference_date=reference_date,
            span_days=span_days,
            memory_refs=memory_refs,
            fixed_schedule_refs=fixed_refs[:12],
        )

        draft_tasks: list[PlanDraftTask] = []
        total = len(tasks)
        for index, task in enumerate(tasks, start=1):
            draft = PlanDraftTask(draft_id=f"draft-{uuid4().hex[:12]}", **task.model_dump())
            draft_tasks.append(draft)
            yield (
                "task",
                {
                    "index": index,
                    "total": total,
                    "task": draft.model_dump(),
                },
            )
            yield (
                "status",
                {
                    "index": index,
                    "total": total,
                    "message": f"已生成 {index}/{total} 条草稿任务",
                },
            )

        yield (
            "complete",
            {
                "reference_date": reference_date.isoformat(),
                "inferred_span_days": span_days,
                "goal_text": req.goal_text,
                "draft_tasks": [task.model_dump() for task in draft_tasks],
                "source_model": source_model,
                "fallback": fallback,
                "note": note,
            },
        )

    def enrich_draft_task(self, req: PlanDraftEnrichRequest) -> PlanDraftEnrichResponse:
        raw = req.task.model_dump()
        normalized = self._normalize_plan_item_dict(raw, 1)
        enriched_item, source_model, fallback, note = self._enrich_draft_task_detail(
            goal_text=req.goal_text,
            current_item=normalized,
        )
        enriched = PlanDraftTask(draft_id=req.task.draft_id, **enriched_item.model_dump())
        return PlanDraftEnrichResponse(
            task=enriched,
            source_model=source_model,
            fallback=fallback,
            note=note,
        )

    def assign_draft_tasks(self, req: PlanDraftAssignRequest) -> PlanDraftAssignResponse:
        if req.target_date < date.today():
            raise RuntimeError("target_date cannot be in the past. Please choose today or a future date.")

        payload = self._load_daily_plan_payload(req.target_date)
        if payload is None:
            payload = self._build_empty_plan_payload(
                target_date=req.target_date,
                goal_text=req.goal_text,
            )

        existing_items = list(payload["plan_items"])
        for index, task in enumerate(req.tasks, start=1):
            raw = task.model_dump()
            raw.pop("draft_id", None)
            normalized = self._normalize_plan_item_dict(raw, len(existing_items) + index)
            existing_items.append(normalized)

        updated_payload = {
            "goal_text": str(req.goal_text or payload["goal_text"]).strip(),
            "plan_items": self._sort_plan_item_dicts(existing_items),
            "fixed_schedule_refs": payload["fixed_schedule_refs"],
            "note": payload["note"],
            "source_model": payload.get("source_model", "manual"),
            "fallback": payload.get("fallback", False),
        }
        self._upsert_daily_plan(
            target_date=req.target_date,
            goal_text=updated_payload["goal_text"],
            plan_payload=updated_payload,
        )

        return PlanDraftAssignResponse(
            status="ok",
            target_date=req.target_date,
            assigned_count=len(req.tasks),
            plan=self.get_plan(req.target_date),
        )

    def create_plan_item(self, req: PlanItemCreateRequest) -> PlanResponse:
        payload = self._load_daily_plan_payload(req.date)
        if payload is None:
            payload = self._build_empty_plan_payload(target_date=req.date, goal_text="")

        items = list(payload["plan_items"])
        raw_item = {
            "title": req.title,
            "priority": req.priority,
            "estimate_hours": req.estimate_hours,
            "done_definition": req.done_definition,
            "checklist": [
                row.model_dump() if isinstance(row, PlanChecklistItem) else row
                for row in (req.checklist or [])
            ],
            "progress_status": req.progress_status,
            "progress_percent": req.progress_percent,
            "progress_note": req.progress_note,
        }
        normalized = self._normalize_plan_item_dict(raw_item, len(items) + 1)
        if req.insert_at_top:
            items.insert(0, normalized)
        else:
            items.append(normalized)

        updated_payload = {
            "goal_text": payload["goal_text"],
            "plan_items": self._sort_plan_item_dicts(items),
            "fixed_schedule_refs": payload["fixed_schedule_refs"],
            "note": payload["note"],
            "source_model": payload.get("source_model", "manual"),
            "fallback": payload.get("fallback", False),
        }
        self._upsert_daily_plan(
            target_date=req.date,
            goal_text=updated_payload["goal_text"],
            plan_payload=updated_payload,
        )
        return self.get_plan(req.date)

    def delete_plan_item(self, req: PlanItemDeleteRequest) -> PlanResponse:
        payload = self._load_daily_plan_payload(req.date)
        if payload is None:
            raise RuntimeError("No daily plan found for this date.")

        items = list(payload["plan_items"])
        if req.item_index >= len(items):
            raise RuntimeError("item_index is out of range.")
        items.pop(req.item_index)

        updated_payload = {
            "goal_text": payload["goal_text"],
            "plan_items": self._sort_plan_item_dicts(items),
            "fixed_schedule_refs": payload["fixed_schedule_refs"],
            "note": payload["note"],
            "source_model": payload.get("source_model", "stored"),
            "fallback": payload.get("fallback", False),
        }
        self._upsert_daily_plan(
            target_date=req.date,
            goal_text=updated_payload["goal_text"],
            plan_payload=updated_payload,
        )
        return self.get_plan(req.date)

    def reschedule_plan_item(self, req: PlanItemRescheduleRequest) -> PlanItemMoveResponse:
        source_payload = self._load_daily_plan_payload(req.source_date)
        if source_payload is None:
            raise RuntimeError("No daily plan found for source_date.")

        source_items = list(source_payload["plan_items"])
        if req.item_index >= len(source_items):
            raise RuntimeError("item_index is out of range.")

        moved_raw = dict(source_items.pop(req.item_index))
        moved_item = self._normalize_plan_item_dict(moved_raw, 1)

        if req.source_date == req.target_date:
            source_items.append(moved_item)
            updated_same_day_payload = {
                "goal_text": source_payload["goal_text"],
                "plan_items": self._sort_plan_item_dicts(source_items),
                "fixed_schedule_refs": source_payload["fixed_schedule_refs"],
                "note": source_payload["note"],
                "source_model": source_payload.get("source_model", "stored"),
                "fallback": source_payload.get("fallback", False),
            }
            self._upsert_daily_plan(
                target_date=req.source_date,
                goal_text=updated_same_day_payload["goal_text"],
                plan_payload=updated_same_day_payload,
            )
            same_day = self.get_plan(req.source_date)
            return PlanItemMoveResponse(
                status="ok",
                source_date=req.source_date,
                target_date=req.target_date,
                moved_count=1,
                source_plan=same_day,
                target_plan=same_day,
            )

        source_updated_payload = {
            "goal_text": source_payload["goal_text"],
            "plan_items": self._sort_plan_item_dicts(source_items),
            "fixed_schedule_refs": source_payload["fixed_schedule_refs"],
            "note": source_payload["note"],
            "source_model": source_payload.get("source_model", "stored"),
            "fallback": source_payload.get("fallback", False),
        }
        self._upsert_daily_plan(
            target_date=req.source_date,
            goal_text=source_updated_payload["goal_text"],
            plan_payload=source_updated_payload,
        )

        target_payload = self._load_daily_plan_payload(req.target_date)
        if target_payload is None:
            target_payload = self._build_empty_plan_payload(
                target_date=req.target_date,
                goal_text=source_payload.get("goal_text", ""),
            )
        target_items = list(target_payload["plan_items"])
        target_items.append(moved_item)

        target_updated_payload = {
            "goal_text": target_payload["goal_text"],
            "plan_items": self._sort_plan_item_dicts(target_items),
            "fixed_schedule_refs": target_payload["fixed_schedule_refs"],
            "note": target_payload["note"],
            "source_model": target_payload.get("source_model", "manual"),
            "fallback": target_payload.get("fallback", False),
        }
        self._upsert_daily_plan(
            target_date=req.target_date,
            goal_text=target_updated_payload["goal_text"],
            plan_payload=target_updated_payload,
        )

        return PlanItemMoveResponse(
            status="ok",
            source_date=req.source_date,
            target_date=req.target_date,
            moved_count=1,
            source_plan=self.get_plan(req.source_date),
            target_plan=self.get_plan(req.target_date),
        )

    def split_reschedule_checklist(
        self,
        req: PlanItemSplitRescheduleRequest,
    ) -> PlanItemMoveResponse:
        source_payload = self._load_daily_plan_payload(req.source_date)
        if source_payload is None:
            raise RuntimeError("No daily plan found for source_date.")

        source_items = list(source_payload["plan_items"])
        if req.item_index >= len(source_items):
            raise RuntimeError("item_index is out of range.")

        source_row = dict(source_items[req.item_index])
        source_item = self._normalize_plan_item_dict(source_row, req.item_index + 1)
        checklist = list(source_item.get("checklist", []))
        if not checklist:
            raise RuntimeError("Current task has no checklist items to split.")

        selected_indices = sorted({int(i) for i in req.checklist_indices if int(i) >= 0})
        if not selected_indices:
            raise RuntimeError("checklist_indices cannot be empty.")
        if selected_indices[-1] >= len(checklist):
            raise RuntimeError("checklist index is out of range.")

        selected_set = set(selected_indices)
        moved_checklist = [checklist[idx] for idx in selected_indices]
        remain_checklist = [row for idx, row in enumerate(checklist) if idx not in selected_set]

        # Rebuild source item from remaining checklist and recompute progress.
        updated_source_row = dict(source_item)
        updated_source_row["checklist"] = remain_checklist
        updated_source_row["progress_status"] = "partial"
        updated_source_row["progress_percent"] = None

        if remain_checklist:
            source_items[req.item_index] = self._normalize_plan_item_dict(updated_source_row, req.item_index + 1)
        else:
            source_items.pop(req.item_index)

        clone_title = str(req.new_task_title or "").strip() or f"{source_item.get('title', '任务')}-拆分"
        moved_ratio = len(moved_checklist) / max(1, len(checklist))
        base_estimate = self._safe_float(source_item.get("estimate_hours"))
        clone_estimate = None
        if base_estimate is not None:
            clone_estimate = max(0.5, round(base_estimate * moved_ratio, 1))

        clone_raw = {
            "title": clone_title,
            "priority": source_item.get("priority", "P2"),
            "estimate_hours": clone_estimate,
            "done_definition": source_item.get("done_definition", "承接拆分子任务并完成交付。"),
            "checklist": moved_checklist,
            "progress_status": "partial",
            "progress_percent": None,
            "progress_note": "",
        }
        cloned_item = self._normalize_plan_item_dict(clone_raw, 1)

        if req.source_date == req.target_date:
            source_items.append(cloned_item)
            merged_payload = {
                "goal_text": source_payload["goal_text"],
                "plan_items": self._sort_plan_item_dicts(source_items),
                "fixed_schedule_refs": source_payload["fixed_schedule_refs"],
                "note": source_payload["note"],
                "source_model": source_payload.get("source_model", "stored"),
                "fallback": source_payload.get("fallback", False),
            }
            self._upsert_daily_plan(
                target_date=req.source_date,
                goal_text=merged_payload["goal_text"],
                plan_payload=merged_payload,
            )
            same_day = self.get_plan(req.source_date)
            return PlanItemMoveResponse(
                status="ok",
                source_date=req.source_date,
                target_date=req.target_date,
                moved_count=len(moved_checklist),
                source_plan=same_day,
                target_plan=same_day,
            )

        source_updated_payload = {
            "goal_text": source_payload["goal_text"],
            "plan_items": self._sort_plan_item_dicts(source_items),
            "fixed_schedule_refs": source_payload["fixed_schedule_refs"],
            "note": source_payload["note"],
            "source_model": source_payload.get("source_model", "stored"),
            "fallback": source_payload.get("fallback", False),
        }
        self._upsert_daily_plan(
            target_date=req.source_date,
            goal_text=source_updated_payload["goal_text"],
            plan_payload=source_updated_payload,
        )

        target_payload = self._load_daily_plan_payload(req.target_date)
        if target_payload is None:
            target_payload = self._build_empty_plan_payload(
                target_date=req.target_date,
                goal_text=source_payload.get("goal_text", ""),
            )
        target_items = list(target_payload["plan_items"])
        target_items.append(cloned_item)
        target_updated_payload = {
            "goal_text": target_payload["goal_text"],
            "plan_items": self._sort_plan_item_dicts(target_items),
            "fixed_schedule_refs": target_payload["fixed_schedule_refs"],
            "note": target_payload["note"],
            "source_model": target_payload.get("source_model", "manual"),
            "fallback": target_payload.get("fallback", False),
        }
        self._upsert_daily_plan(
            target_date=req.target_date,
            goal_text=target_updated_payload["goal_text"],
            plan_payload=target_updated_payload,
        )

        return PlanItemMoveResponse(
            status="ok",
            source_date=req.source_date,
            target_date=req.target_date,
            moved_count=len(moved_checklist),
            source_plan=self.get_plan(req.source_date),
            target_plan=self.get_plan(req.target_date),
        )

    def _collect_memory_refs(self, goal_text: str) -> list[str]:
        return self.memory_service.build_memory_refs(
            query=goal_text,
            top_k=6,
            fallback_recent=3,
            types=("goal", "plan", "review", "dialogue", "profile"),
        )

    def _collect_fixed_schedule_refs(self, target_date: date) -> list[str]:
        items = self.schedule_service.list_effective_fixed_schedules_for_day(target_date)
        refs: list[str] = []
        seen: set[str] = set()
        for item in items:
            start_at = str(item.get("start_at", "")).strip()
            end_at = str(item.get("end_at", "")).strip()
            title = str(item.get("title", "")).strip()
            if not start_at or not end_at or not title:
                continue
            ref = f"{start_at}-{end_at} {title}"
            if ref in seen:
                continue
            seen.add(ref)
            refs.append(ref)
        return refs

    def _generate_plan(
        self,
        *,
        goal_text: str,
        target_date: date,
        memory_refs: list[str],
        fixed_schedule_refs: list[str],
    ) -> tuple[list[PlanItem], str, str, bool]:
        if not self.llm_service.is_ready():
            fallback_items = self._build_rule_based_plan(goal_text, fixed_schedule_refs=fixed_schedule_refs)
            return (
                fallback_items,
                "GLM_API_KEY not configured. Used rule-based plan.",
                "rule-based",
                True,
            )

        system_prompt = (
            "You are a daily task planning assistant. Output strict JSON only.\n"
            "JSON schema:\n"
            "{\n"
            '  "plan_items": [\n'
            '    {"title":"...", "priority":"P0|P1|P2|P3", "estimate_hours":1.5, "done_definition":"...", "checklist":[{"content":"...", "is_done":false}]}\n'
            "  ],\n"
            '  "note":"one short sentence"\n'
            "}\n"
            "Rules:\n"
            "1) plan_items should contain 3-8 concrete tasks for one day, sorted by priority from P0 to P3.\n"
            "2) Every task must include 2-5 actionable checklist steps.\n"
            "3) Avoid time-slot language like morning/afternoon/evening scheduling.\n"
            "4) Respect fixed schedules provided by user context.\n"
            "5) Final content must be in Simplified Chinese."
        )
        memory_block = "\n".join(f"- {item}" for item in memory_refs) or "- N/A"
        fixed_schedule_block = "\n".join(f"- {item}" for item in fixed_schedule_refs) or "- N/A"
        local_context_block = build_local_context_block(self.settings)
        user_prompt = (
            f"Date: {target_date.isoformat()}\n"
            f"Goal: {goal_text}\n"
            "Local context:\n"
            f"{local_context_block}\n"
            "Effective fixed schedules for this date:\n"
            f"{fixed_schedule_block}\n"
            "Relevant memory:\n"
            f"{memory_block}\n"
            "Please generate a practical daily task checklist prioritized by P0/P1/P2/P3.\n"
            "Return JSON only."
        )

        try:
            raw_reply, model_name = self.llm_service.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                history=None,
            )
            plan_items, note = self._parse_plan_reply(raw_reply)
            final_note = note or f"Plan generated by {model_name}."
            return plan_items, final_note, model_name, False
        except Exception as exc:  # keep MVP stable
            fallback_items = self._build_rule_based_plan(goal_text, fixed_schedule_refs=fixed_schedule_refs)
            return (
                fallback_items,
                f"LLM planning failed ({exc}). Used rule-based plan.",
                "rule-based",
                True,
            )

    def _generate_draft_tasks(
        self,
        *,
        goal_text: str,
        reference_date: date,
        span_days: int,
        memory_refs: list[str],
        fixed_schedule_refs: list[str],
    ) -> tuple[list[PlanItem], str, str, bool]:
        target_count = max(4, min(24, span_days * 3))
        if not self.llm_service.is_ready():
            fallback_items = self._build_rule_based_draft_tasks(goal_text, target_count)
            return (
                fallback_items,
                "GLM_API_KEY not configured. Used rule-based draft pool.",
                "rule-based",
                True,
            )

        system_prompt = (
            "You are a task drafting assistant for multi-day planning. Output strict JSON only.\n"
            "JSON schema:\n"
            "{\n"
            '  "tasks": [\n'
            '    {"title":"...", "priority":"P0|P1|P2|P3", "estimate_hours":1.5, "done_definition":"...", "checklist":[{"content":"...", "is_done":false}]}\n'
            "  ],\n"
            '  "note":"one short sentence"\n'
            "}\n"
            "Rules:\n"
            f"1) tasks should contain {target_count} items (allow +/-2), all in Simplified Chinese.\n"
            "2) Tasks must be independent and suitable for manual distribution across multiple dates.\n"
            "3) Every task needs 2-5 executable checklist steps.\n"
            "4) Avoid specific time-slot words like morning/afternoon/evening.\n"
            "5) Priorities should be balanced across P0/P1/P2/P3.\n"
            "6) User may provide natural-language duration like '2周/3天/一个月'; align workload roughly to it.\n"
            "7) Hidden context: today is provided by the system; use it only for pacing, do not expose it in output.\n"
        )
        memory_block = "\n".join(f"- {item}" for item in memory_refs) or "- N/A"
        fixed_schedule_block = "\n".join(f"- {item}" for item in fixed_schedule_refs) or "- N/A"
        local_context_block = build_local_context_block(self.settings)
        user_prompt = (
            f"[System hidden variable] Today is {reference_date.isoformat()}\n"
            f"Inferred workload horizon: about {span_days} days\n"
            f"Goal: {goal_text}\n"
            "Local context:\n"
            f"{local_context_block}\n"
            "Effective fixed schedules in this period:\n"
            f"{fixed_schedule_block}\n"
            "Relevant memory:\n"
            f"{memory_block}\n"
            "Please generate a multi-day task inbox (draft pool) in JSON only."
        )

        try:
            raw_reply, model_name = self.llm_service.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                history=None,
            )
            payload = self._extract_json_object(raw_reply)
            rows = payload.get("tasks", [])
            if not isinstance(rows, list):
                raise ValueError("tasks is not a list")

            parsed_items: list[PlanItem] = []
            for index, raw in enumerate(rows[:24], start=1):
                if not isinstance(raw, dict):
                    continue
                parsed_items.append(PlanItem(**self._normalize_plan_item_dict(raw, index)))

            if not parsed_items:
                raise ValueError("tasks must contain at least one valid item")

            note = str(payload.get("note", "")).strip()
            return self._sort_plan_items(parsed_items), note or f"Draft pool generated by {model_name}.", model_name, False
        except Exception as exc:
            fallback_items = self._build_rule_based_draft_tasks(goal_text, target_count)
            return (
                fallback_items,
                f"LLM draft generation failed ({exc}). Used rule-based draft pool.",
                "rule-based",
                True,
            )

    def _generate_draft_task_outlines(
        self,
        *,
        goal_text: str,
        reference_date: date,
        span_days: int,
        memory_refs: list[str],
        fixed_schedule_refs: list[str],
    ) -> tuple[list[PlanItem], str, str, bool]:
        target_count = max(4, min(24, span_days * 3))
        if not self.llm_service.is_ready():
            fallback_items = self._build_rule_based_draft_task_outlines(goal_text, target_count)
            return (
                fallback_items,
                "GLM_API_KEY not configured. Used rule-based draft outlines.",
                "rule-based",
                True,
            )

        system_prompt = (
            "You are a task drafting assistant for multi-day planning.\n"
            "Output strict JSON only.\n"
            "JSON schema:\n"
            "{\n"
            '  "tasks": [\n'
            '    {"title":"...", "priority":"P0|P1|P2|P3", "estimate_hours":1.5}\n'
            "  ],\n"
            '  "note":"one short sentence"\n'
            "}\n"
            "Rules:\n"
            f"1) tasks should contain {target_count} items (allow +/-2), all in Simplified Chinese.\n"
            "2) Keep each task concise and independently assignable across different future dates.\n"
            "3) Do NOT output checklist or long explanations.\n"
            "4) Priorities should be balanced across P0/P1/P2/P3.\n"
            "5) User may mention duration like 2周/3天/1个月; align workload roughly.\n"
        )
        memory_block = "\n".join(f"- {item}" for item in memory_refs) or "- N/A"
        fixed_schedule_block = "\n".join(f"- {item}" for item in fixed_schedule_refs) or "- N/A"
        local_context_block = build_local_context_block(self.settings)
        user_prompt = (
            f"[System hidden variable] Today is {reference_date.isoformat()}\n"
            f"Inferred workload horizon: about {span_days} days\n"
            f"Goal: {goal_text}\n"
            "Local context:\n"
            f"{local_context_block}\n"
            "Effective fixed schedules in this period:\n"
            f"{fixed_schedule_block}\n"
            "Relevant memory:\n"
            f"{memory_block}\n"
            "Please generate only concise draft task outlines in JSON."
        )

        try:
            raw_reply, model_name = self.llm_service.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                history=None,
            )
            payload = self._extract_json_object(raw_reply)
            rows = payload.get("tasks", [])
            if not isinstance(rows, list):
                raise ValueError("tasks is not a list")

            parsed_items: list[PlanItem] = []
            for index, raw in enumerate(rows[:24], start=1):
                if not isinstance(raw, dict):
                    continue
                normalized = self._normalize_plan_item_dict(
                    {
                        "title": raw.get("title"),
                        "priority": raw.get("priority"),
                        "estimate_hours": raw.get("estimate_hours"),
                        "done_definition": "先完成任务主目标，详情可按需生成。",
                        "checklist": [],
                    },
                    index,
                )
                parsed_items.append(PlanItem(**normalized))

            if not parsed_items:
                raise ValueError("tasks must contain at least one valid item")

            note = str(payload.get("note", "")).strip()
            return (
                self._sort_plan_items(parsed_items),
                note or f"Draft outlines generated by {model_name}.",
                model_name,
                False,
            )
        except Exception as exc:
            fallback_items = self._build_rule_based_draft_task_outlines(goal_text, target_count)
            return (
                fallback_items,
                f"LLM outline generation failed ({exc}). Used rule-based draft outlines.",
                "rule-based",
                True,
            )

    def _enrich_draft_task_detail(
        self,
        *,
        goal_text: str,
        current_item: dict[str, Any],
    ) -> tuple[PlanItem, str, bool, str]:
        if not self.llm_service.is_ready():
            enriched = dict(current_item)
            if not str(enriched.get("done_definition", "")).strip():
                enriched["done_definition"] = "完成该任务并形成可验证产出。"
            if not isinstance(enriched.get("checklist"), list) or not enriched.get("checklist"):
                enriched["checklist"] = self._default_checklist_items()
            return PlanItem(**self._normalize_plan_item_dict(enriched, 1)), "rule-based", True, "Used rule-based enrichment."

        system_prompt = (
            "你是任务拆解助手。请针对单个任务补全完成定义与子任务清单。\n"
            "输出严格 JSON：\n"
            "{\n"
            '  "done_definition":"...",\n'
            '  "checklist":[{"content":"...", "is_done":false}]\n'
            "}\n"
            "规则：\n"
            "1) 只处理当前这个任务，不要生成其他任务。\n"
            "2) checklist 输出 3-6 条可执行步骤。\n"
            "3) 使用简体中文。\n"
        )
        user_prompt = (
            f"总体目标：{goal_text or '未提供'}\n"
            f"任务：{json.dumps(current_item, ensure_ascii=False)}\n"
            "请返回 JSON。"
        )
        try:
            raw_reply, model_name = self.llm_service.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                history=None,
            )
            payload = self._extract_json_object(raw_reply)
            merged = dict(current_item)
            done_definition = str(payload.get("done_definition", "")).strip()
            if done_definition:
                merged["done_definition"] = done_definition
            if isinstance(payload.get("checklist"), list):
                merged["checklist"] = payload["checklist"]
            normalized = self._normalize_plan_item_dict(merged, 1)
            return PlanItem(**normalized), model_name, False, "Enriched by LLM."
        except Exception as exc:
            enriched = dict(current_item)
            if not str(enriched.get("done_definition", "")).strip():
                enriched["done_definition"] = "完成该任务并形成可验证产出。"
            if not isinstance(enriched.get("checklist"), list) or not enriched.get("checklist"):
                enriched["checklist"] = self._default_checklist_items()
            normalized = self._normalize_plan_item_dict(enriched, 1)
            return PlanItem(**normalized), "rule-based", True, f"LLM enrichment failed ({exc}). Used rule-based fallback."

    def _build_rule_based_draft_tasks(self, goal_text: str, target_count: int) -> list[PlanItem]:
        base_labels = [
            ("明确核心目标与评估标准", "P0"),
            ("梳理关键知识点与资料", "P0"),
            ("完成高优先级实操练习", "P1"),
            ("输出阶段性总结与错题复盘", "P1"),
            ("补齐薄弱项并形成笔记", "P2"),
            ("整理下一步行动与计划", "P2"),
        ]

        tasks: list[PlanItem] = []
        round_index = 1
        while len(tasks) < target_count:
            for label, priority in base_labels:
                if len(tasks) >= target_count:
                    break
                title = f"{goal_text}：{label}"
                if round_index > 1:
                    title = f"{title}（第{round_index}轮）"
                tasks.append(
                    PlanItem(
                        title=title,
                        priority=priority,
                        estimate_hours=1.5 if priority == "P0" else 1.0,
                        done_definition="完成该任务并记录可验证成果。",
                        checklist=[
                            PlanChecklistItem(content="明确输入条件与产出标准", is_done=False),
                            PlanChecklistItem(content="执行核心步骤并记录结果", is_done=False),
                            PlanChecklistItem(content="复盘问题并确定下一步", is_done=False),
                        ],
                    )
                )
            round_index += 1
        return self._sort_plan_items(tasks)

    def _build_rule_based_draft_task_outlines(self, goal_text: str, target_count: int) -> list[PlanItem]:
        base_labels = [
            ("明确核心目标与评估标准", "P0"),
            ("梳理关键知识点与资料", "P0"),
            ("完成高优先级实操练习", "P1"),
            ("输出阶段性总结与复盘", "P1"),
            ("补齐薄弱项并形成笔记", "P2"),
            ("整理下一步行动计划", "P3"),
        ]
        tasks: list[PlanItem] = []
        round_index = 1
        while len(tasks) < target_count:
            for label, priority in base_labels:
                if len(tasks) >= target_count:
                    break
                title = f"{goal_text}：{label}"
                if round_index > 1:
                    title = f"{title}（第{round_index}轮）"
                tasks.append(
                    PlanItem(
                        title=title,
                        priority=priority,
                        estimate_hours=1.5 if priority in {"P0", "P1"} else 1.0,
                        done_definition="先完成任务主目标，详情可按需生成。",
                        checklist=[],
                    )
                )
            round_index += 1
        return self._sort_plan_items(tasks)

    def _infer_draft_span_days(self, goal_text: str) -> int:
        text = str(goal_text or "").strip().lower()
        if not text:
            return 7

        arabic_patterns: list[tuple[str, int]] = [
            (r"(\d{1,2})\s*(?:周|星期|weeks?|week)", 7),
            (r"(\d{1,2})\s*(?:天|日|days?|day)", 1),
            (r"(\d{1,2})\s*(?:个?\s*月|months?|month)", 30),
        ]
        for pattern, factor in arabic_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                value = int(match.group(1))
                return max(1, min(90, value * factor))

        cn_digit = {
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
        }
        cn_match = re.search(r"(一|二|两|三|四|五|六|七|八|九|十)\s*(?:个)?\s*(周|星期|天|日|月)", text)
        if cn_match:
            count = cn_digit.get(cn_match.group(1), 1)
            unit = cn_match.group(2)
            if unit in {"周", "星期"}:
                return max(1, min(90, count * 7))
            if unit in {"月"}:
                return max(1, min(90, count * 30))
            return max(1, min(90, count))

        if "半个月" in text:
            return 15
        if "这周" in text or "本周" in text:
            return 7
        if "本月" in text or "这个月" in text:
            return 30

        return 7

    def _parse_plan_reply(self, raw_reply: str) -> tuple[list[PlanItem], str]:
        payload = self._extract_json_object(raw_reply)
        items_raw = payload.get("plan_items", [])
        if not isinstance(items_raw, list):
            raise ValueError("plan_items is not a list")

        parsed_items: list[PlanItem] = []
        for index, raw in enumerate(items_raw[:6], start=1):
            if not isinstance(raw, dict):
                continue
            normalized = self._normalize_plan_item_dict(raw, index)
            parsed_items.append(PlanItem(**normalized))

        if len(parsed_items) == 0:
            raise ValueError("plan_items must contain at least one valid item")

        note = str(payload.get("note", "")).strip()
        return self._sort_plan_items(parsed_items), note

    def _load_daily_plan_payload(self, target_date: date) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT goal_text, plan_json
                FROM daily_sessions
                WHERE date = ?
                LIMIT 1
                """,
                (target_date.isoformat(),),
            ).fetchone()

        if not row:
            return None

        goal_text = str(row["goal_text"] or "").strip()
        plan_json = row["plan_json"]
        if not plan_json:
            return {
                "goal_text": goal_text,
                "plan_items": [],
                "fixed_schedule_refs": [],
                "note": "",
            }

        try:
            parsed = json.loads(plan_json)
        except json.JSONDecodeError:
            return {
                "goal_text": goal_text,
                "plan_items": [],
                "fixed_schedule_refs": [],
                "note": "",
            }

        if isinstance(parsed, list):
            items = self._sort_plan_item_dicts(self._normalize_plan_item_dicts(parsed))
            return {
                "goal_text": goal_text,
                "plan_items": items,
                "fixed_schedule_refs": [],
                "note": "",
            }

        if isinstance(parsed, dict):
            items = self._sort_plan_item_dicts(self._normalize_plan_item_dicts(parsed.get("plan_items", [])))
            fixed_refs_raw = parsed.get("fixed_schedule_refs", [])
            fixed_refs = [str(it).strip() for it in fixed_refs_raw if str(it).strip()] if isinstance(fixed_refs_raw, list) else []
            return {
                "goal_text": str(parsed.get("goal_text") or goal_text).strip(),
                "plan_items": items,
                "fixed_schedule_refs": fixed_refs,
                "note": str(parsed.get("note", "")).strip(),
                "source_model": str(parsed.get("source_model", "stored") or "stored"),
                "fallback": bool(parsed.get("fallback", False)),
            }

        return {
            "goal_text": goal_text,
            "plan_items": [],
            "fixed_schedule_refs": [],
            "note": "",
        }

    def _build_empty_plan_payload(self, *, target_date: date, goal_text: str = "") -> dict[str, Any]:
        fixed_refs = self._collect_fixed_schedule_refs(target_date)
        return {
            "goal_text": str(goal_text or "").strip(),
            "plan_items": [],
            "fixed_schedule_refs": fixed_refs,
            "note": "",
            "source_model": "manual",
            "fallback": False,
        }

    def _normalize_plan_item_dicts(self, raw_items: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_items, list):
            return []
        normalized: list[dict[str, Any]] = []
        for index, row in enumerate(raw_items, start=1):
            if not isinstance(row, dict):
                continue
            normalized.append(self._normalize_plan_item_dict(row, index))
        return normalized

    def _normalize_plan_item_dict(self, raw: dict[str, Any], index: int) -> dict[str, Any]:
        title = str(raw.get("title", "")).strip() or f"任务 {index}"
        done_definition = str(raw.get("done_definition", "")).strip() or "有明确可验证产出并记录结果。"
        checklist_raw = raw.get("checklist")
        checklist = self._normalize_checklist_items(checklist_raw)
        if not checklist and "checklist" not in raw:
            checklist = self._default_checklist_items()
        progress_status, progress_percent = self._normalize_progress_pair(
            raw.get("progress_status"),
            raw.get("progress_percent"),
        )

        if progress_status == "done":
            checklist = self._set_all_checklist_done(checklist, is_done=True)
        elif progress_status == "todo":
            checklist = self._set_all_checklist_done(checklist, is_done=False)

        if checklist and progress_status not in {"done", "todo"}:
            # For partial/unknown state, let child-item completion drive the final status/percent.
            progress_status, progress_percent = self._derive_progress_from_checklist(checklist)

        return {
            "title": title,
            "priority": self._normalize_priority(raw.get("priority"), index),
            "estimate_hours": self._safe_float(raw.get("estimate_hours")),
            "done_definition": done_definition,
            "checklist": checklist,
            "progress_status": progress_status,
            "progress_percent": progress_percent,
            "progress_note": str(raw.get("progress_note", "")).strip(),
        }

    def _extract_json_object(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if fenced_match:
            cleaned = fenced_match.group(1).strip()
        else:
            direct_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if direct_match:
                cleaned = direct_match.group(0).strip()

        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise ValueError("root json must be object")
        return parsed

    def _normalize_priority(self, raw_priority: Any, index: int) -> str:
        value = str(raw_priority or "").upper().strip()
        if value in {"P0", "P1", "P2", "P3"}:
            return value
        if index == 1:
            return "P0"
        if index == 2:
            return "P1"
        if index == 3:
            return "P2"
        if index == 4:
            return "P3"
        return "P2"

    def _normalize_progress_status(self, raw_status: Any) -> str:
        status = self._coerce_progress_status(raw_status)
        return status or "todo"

    def _coerce_progress_status(self, raw_status: Any) -> str | None:
        status = str(raw_status or "").strip().lower()
        if status in {"todo", "partial", "done"}:
            return status
        return None

    def _normalize_progress_percent(self, raw_percent: Any) -> int | None:
        try:
            value = int(float(raw_percent))
        except (TypeError, ValueError):
            return None
        return max(0, min(100, value))

    def _normalize_progress_pair(self, raw_status: Any, raw_percent: Any) -> tuple[str, int]:
        status = self._coerce_progress_status(raw_status)
        percent = self._normalize_progress_percent(raw_percent)

        if status is None:
            if percent is None:
                return "todo", 0
            if percent >= 100:
                return "done", 100
            if percent <= 0:
                return "todo", 0
            return "partial", percent

        if status == "done":
            return "done", 100
        if status == "todo":
            return "todo", 0
        if percent is None:
            return "partial", 50
        return "partial", percent

    def _safe_float(self, value: Any) -> float | None:
        try:
            result = float(value)
            if result <= 0:
                return None
            return round(result, 1)
        except (TypeError, ValueError):
            return None

    def _build_rule_based_plan(
        self,
        goal_text: str,
        *,
        fixed_schedule_refs: list[str] | None = None,
    ) -> list[PlanItem]:
        schedule_hint = (
            f"避开固定作息时段：{'；'.join(fixed_schedule_refs[:3])}" if fixed_schedule_refs else ""
        )

        first_item_checklist = [
            "明确今日最重要结果和验收标准",
            "拆分关键步骤并估算投入时间",
            "准备执行所需资料、环境与依赖",
        ]
        if schedule_hint:
            first_item_checklist.append(schedule_hint)

        items = [
            PlanItem(
                title=f"聚焦推进：{goal_text}",
                priority="P0",
                estimate_hours=2.0,
                done_definition="形成可提交或可演示的阶段性成果。",
                checklist=[PlanChecklistItem(content=text, is_done=False) for text in first_item_checklist],
            ),
            PlanItem(
                title="完成核心交付",
                priority="P1",
                estimate_hours=2.0,
                done_definition="核心任务完成并完成一次自检。",
                checklist=[
                    PlanChecklistItem(content="连续执行一个完整任务周期", is_done=False),
                    PlanChecklistItem(content="记录关键决策与问题", is_done=False),
                    PlanChecklistItem(content="修复明显缺陷并更新结论", is_done=False),
                ],
            ),
            PlanItem(
                title="复盘与明日衔接",
                priority="P2",
                estimate_hours=1.0,
                done_definition="输出复盘结论并形成明日首要行动。",
                checklist=[
                    PlanChecklistItem(content="记录今日完成与未完成项", is_done=False),
                    PlanChecklistItem(content="识别阻塞点和风险", is_done=False),
                    PlanChecklistItem(content="写下明日第一步动作", is_done=False),
                ],
            ),
        ]
        return items

    def _regenerate_item_replace(
        self,
        *,
        goal_text: str,
        target_date: date,
        current_item: dict[str, Any],
        memory_refs: list[str],
        fixed_schedule_refs: list[str],
    ) -> dict[str, Any]:
        if not self.llm_service.is_ready():
            return self._rule_based_replace_item(current_item)

        system_prompt = (
            "你是一个任务拆解专家。现在用户提供了一个【独立任务】草稿，请只优化该单一任务的描述。\n"
            "输出必须是严格 JSON，不要输出任何额外文字。\n"
            "JSON Schema:\n"
            "{\n"
            '  "title":"...",\n'
            '  "done_definition":"...",\n'
            '  "checklist":[{"content":"...", "is_done":false}],\n'
            '  "priority":"P0|P1|P2|P3",\n'
            '  "estimate_hours":1.5\n'
            "}\n"
            "严格约束：\n"
            "1) 绝对不要引申、捏造或返回多个任务。\n"
            "2) 只返回针对该任务的优化结果。\n"
            "3) checklist 保持 3-6 条、可执行、去重复。\n"
            "4) 使用简体中文。\n"
        )
        memory_block = "\n".join(f"- {item}" for item in memory_refs) or "- N/A"
        schedule_block = "\n".join(f"- {item}" for item in fixed_schedule_refs) or "- N/A"
        user_prompt = (
            f"今天日期：{target_date.isoformat()}\n"
            f"整体目标：{goal_text or '未提供'}\n"
            f"【原任务标题】：{current_item.get('title', '')}\n"
            f"【原完成定义】：{current_item.get('done_definition', '')}\n"
            f"【原子任务列表】：{json.dumps(current_item.get('checklist', []), ensure_ascii=False)}\n"
            f"固定安排参考：\n{schedule_block}\n"
            f"相关记忆参考：\n{memory_block}\n"
            "请仅返回该单任务优化结果的 JSON。"
        )
        try:
            raw_reply, _ = self.llm_service.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                history=None,
            )
            parsed = self._extract_json_object(raw_reply)
            merged = dict(current_item)
            title = str(parsed.get("title", "")).strip()
            if title:
                merged["title"] = title
            done_definition = str(parsed.get("done_definition", "")).strip()
            if done_definition:
                merged["done_definition"] = done_definition
            priority = str(parsed.get("priority", "")).strip().upper()
            if priority in {"P0", "P1", "P2", "P3"}:
                merged["priority"] = priority
            estimate_raw = parsed.get("estimate_hours")
            if isinstance(estimate_raw, (int, float)) and 0 < float(estimate_raw) <= 24:
                merged["estimate_hours"] = float(estimate_raw)
            if isinstance(parsed.get("checklist"), list):
                merged["checklist"] = parsed["checklist"]
            return self._normalize_plan_item_dict(merged, 1)
        except Exception:
            return self._rule_based_replace_item(current_item)

    def _regenerate_item_split(
        self,
        *,
        goal_text: str,
        target_date: date,
        current_item: dict[str, Any],
        memory_refs: list[str],
        fixed_schedule_refs: list[str],
    ) -> dict[str, Any]:
        if not self.llm_service.is_ready():
            return self._rule_based_split_item(current_item)

        system_prompt = (
            "You are refining one task by splitting it into finer checklist steps. Output strict JSON only.\n"
            "Schema:\n"
            "{\n"
            '  "checklist":[{"content":"...", "is_done":false}],\n'
            '  "done_definition":"..."\n'
            "}\n"
            "Rules:\n"
            "1) Keep task title unchanged.\n"
            "2) checklist must contain 5-8 specific, actionable steps.\n"
            "3) Avoid generic or repeated items.\n"
            "4) Simplified Chinese only.\n"
        )
        memory_block = "\n".join(f"- {item}" for item in memory_refs) or "- N/A"
        schedule_block = "\n".join(f"- {item}" for item in fixed_schedule_refs) or "- N/A"
        user_prompt = (
            f"Date: {target_date.isoformat()}\n"
            f"Overall goal: {goal_text}\n"
            f"Task item: {json.dumps(current_item, ensure_ascii=False)}\n"
            f"Fixed schedules:\n{schedule_block}\n"
            f"Relevant memory:\n{memory_block}\n"
            "Please split this task into finer checklist steps. Return JSON only."
        )
        try:
            raw_reply, _ = self.llm_service.chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                history=None,
            )
            parsed = self._extract_json_object(raw_reply)
            merged = dict(current_item)
            if isinstance(parsed.get("checklist"), list):
                merged["checklist"] = parsed["checklist"]
            if str(parsed.get("done_definition", "")).strip():
                merged["done_definition"] = str(parsed["done_definition"]).strip()
            merged["progress_status"] = "todo"
            merged["progress_percent"] = 0
            return self._normalize_plan_item_dict(merged, 1)
        except Exception:
            return self._rule_based_split_item(current_item)

    def _rule_based_replace_item(self, current_item: dict[str, Any]) -> dict[str, Any]:
        base_title = str(current_item.get("title", "任务")).strip() or "任务"
        base_definition = str(current_item.get("done_definition", "")).strip()
        checklist = self._normalize_checklist_items(current_item.get("checklist"))
        if checklist:
            polished_checklist: list[dict[str, Any]] = []
            for row in checklist:
                text = str(row.get("content", "")).strip()
                if text:
                    polished_checklist.append(
                        {
                            "content": text if text.endswith("。") else f"{text}。",
                            "is_done": bool(row.get("is_done", False)),
                        }
                    )
            checklist = polished_checklist
        else:
            checklist = [
                {"content": "明确本任务的输入条件与输出物。", "is_done": False},
                {"content": "按优先级拆分执行步骤并逐条完成。", "is_done": False},
                {"content": "完成后自检并记录下一步行动。", "is_done": False},
            ]

        replaced = dict(current_item)
        replaced["title"] = base_title
        replaced["done_definition"] = base_definition or f"完成 {base_title} 的关键输出，并通过自检。"
        replaced["checklist"] = checklist
        return replaced

    def _rule_based_split_item(self, current_item: dict[str, Any]) -> dict[str, Any]:
        base_title = str(current_item.get("title", "任务")).strip() or "任务"
        checklist = [
            {"content": f"梳理 {base_title} 的输入资料与依赖", "is_done": False},
            {"content": "拆分执行步骤并标注优先顺序", "is_done": False},
            {"content": "先完成最小可交付结果", "is_done": False},
            {"content": "逐步补齐剩余细节并自检", "is_done": False},
            {"content": "记录问题与改进建议", "is_done": False},
        ]
        split_item = dict(current_item)
        split_item["checklist"] = checklist
        split_item["progress_status"] = "todo"
        split_item["progress_percent"] = 0
        return split_item

    def _normalize_checklist_items(self, value: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if isinstance(value, list):
            for row in value:
                if isinstance(row, PlanChecklistItem):
                    text = row.content.strip()
                    if text:
                        items.append({"content": text, "is_done": bool(row.is_done)})
                    continue

                if isinstance(row, dict):
                    text = str(row.get("content", "")).strip()
                    if not text:
                        continue
                    items.append({"content": text, "is_done": bool(row.get("is_done", False))})
                    continue

                text = str(row).strip()
                if text:
                    items.append({"content": text, "is_done": False})
        elif isinstance(value, str):
            parts = re.split(r"[\n;,；、]+", value)
            for part in parts:
                text = part.strip()
                if text:
                    items.append({"content": text, "is_done": False})

        if items:
            return items[:8]
        return []

    def _default_checklist_items(self) -> list[dict[str, Any]]:
        return [
            {"content": "明确本项完成标准", "is_done": False},
            {"content": "执行核心步骤", "is_done": False},
            {"content": "记录结果与阻塞", "is_done": False},
        ]

    def _set_all_checklist_done(self, checklist: list[dict[str, Any]], *, is_done: bool) -> list[dict[str, Any]]:
        return [
            {"content": str(row.get("content", "")).strip(), "is_done": is_done}
            for row in checklist
            if str(row.get("content", "")).strip()
        ]

    def _derive_progress_from_checklist(self, checklist: list[dict[str, Any]]) -> tuple[str, int]:
        if not checklist:
            return "todo", 0
        done_count = sum(1 for row in checklist if bool(row.get("is_done", False)))
        total = len(checklist)
        if done_count <= 0:
            return "todo", 0
        if done_count >= total:
            return "done", 100
        percent = max(1, min(99, int(round(done_count * 100 / total))))
        return "partial", percent

    def _priority_rank(self, value: str) -> int:
        if value == "P0":
            return 0
        if value == "P1":
            return 1
        if value == "P2":
            return 2
        if value == "P3":
            return 3
        return 9

    def _sort_plan_items(self, items: list[PlanItem]) -> list[PlanItem]:
        return sorted(
            items,
            key=lambda item: (self._priority_rank(item.priority), item.title.strip()),
        )

    def _sort_plan_item_dicts(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            items,
            key=lambda item: (
                self._priority_rank(str(item.get("priority", "P2")).upper()),
                str(item.get("title", "")).strip(),
            ),
        )

    def _upsert_daily_plan(self, target_date: date, goal_text: str, plan_payload: dict[str, Any]) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO daily_sessions (date, goal_text, plan_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    goal_text=excluded.goal_text,
                    plan_json=excluded.plan_json,
                    updated_at=excluded.updated_at
                """,
                (
                    target_date.isoformat(),
                    goal_text,
                    json.dumps(plan_payload, ensure_ascii=False),
                    now_iso(),
                ),
            )
            conn.commit()
