from __future__ import annotations

from datetime import date as DateType
from typing import Any, Literal

from pydantic import BaseModel, Field


class PlanRequest(BaseModel):
    date: DateType | None = None
    goal_text: str = Field(min_length=1, max_length=500)
    schedule_mode: Literal["fixed", "full_day"] = "fixed"


PlanTaskStatus = Literal["todo", "partial", "done"]
PlanItemRegenerateAction = Literal["replace", "split"]


class PlanChecklistItem(BaseModel):
    content: str = Field(min_length=1, max_length=300)
    is_done: bool = False


class PlanItem(BaseModel):
    title: str
    priority: str
    estimate_hours: float | None = None
    done_definition: str
    checklist: list[PlanChecklistItem] = Field(default_factory=list)
    progress_status: PlanTaskStatus = "todo"
    progress_percent: int = Field(default=0, ge=0, le=100)
    progress_note: str = ""


class PlanResponse(BaseModel):
    date: DateType
    goal_text: str = ""
    plan_items: list[PlanItem]
    memory_refs: list[str]
    fixed_schedule_refs: list[str] = Field(default_factory=list)
    note: str


class PlanItemProgressUpdateRequest(BaseModel):
    date: DateType
    item_index: int = Field(ge=0, le=100)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    priority: str | None = Field(default=None, pattern=r"^P[0-3]$")
    estimate_hours: float | None = Field(default=None, gt=0, le=24)
    done_definition: str | None = Field(default=None, max_length=1000)
    checklist: list[PlanChecklistItem] | None = None
    progress_status: PlanTaskStatus | None = None
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    progress_note: str | None = Field(default=None, max_length=1000)


class PlanDayCommitRequest(BaseModel):
    date: DateType
    goal_text: str | None = Field(default=None, max_length=500)
    plan_items: list[PlanItem] = Field(default_factory=list)


class PlanItemRegenerateRequest(BaseModel):
    date: DateType
    item_index: int = Field(ge=0, le=100)
    action: PlanItemRegenerateAction = "replace"
    source_item: PlanItem | None = None


class PlanItemOptimizeRequest(BaseModel):
    date: DateType | None = None
    item_index: int | None = Field(default=None, ge=0, le=200)
    goal_text: str = Field(default="", max_length=500)
    title: str = Field(min_length=1, max_length=200)
    priority: str = Field(default="P2", pattern=r"^P[0-3]$")
    estimate_hours: float | None = Field(default=None, gt=0, le=24)
    done_definition: str = Field(default="", max_length=1000)
    checklist: list[PlanChecklistItem] = Field(default_factory=list)
    progress_status: PlanTaskStatus = "todo"
    progress_percent: int = Field(default=0, ge=0, le=100)
    progress_note: str = Field(default="", max_length=1000)


class PlanItemOptimizeResponse(BaseModel):
    item: PlanItem
    item_index: int | None = Field(default=None, ge=0, le=200)
    source_model: str = "rule-based"
    fallback: bool = False
    note: str = ""


class PlanDraftGenerateRequest(BaseModel):
    goal_text: str = Field(min_length=1, max_length=500)


class PlanDraftTask(PlanItem):
    draft_id: str = Field(min_length=1, max_length=80)


class PlanDraftGenerateResponse(BaseModel):
    reference_date: DateType
    inferred_span_days: int = Field(ge=1, le=90)
    goal_text: str
    draft_tasks: list[PlanDraftTask] = Field(default_factory=list)
    source_model: str = "rule-based"
    fallback: bool = False
    note: str = ""


class PlanDraftEnrichRequest(BaseModel):
    goal_text: str = Field(default="", max_length=500)
    task: PlanDraftTask


class PlanDraftEnrichResponse(BaseModel):
    task: PlanDraftTask
    source_model: str = "rule-based"
    fallback: bool = False
    note: str = ""


class PlanDraftAssignRequest(BaseModel):
    target_date: DateType
    goal_text: str = Field(default="", max_length=500)
    tasks: list[PlanDraftTask] = Field(default_factory=list, min_length=1)


class PlanDraftAssignResponse(BaseModel):
    status: str = "ok"
    target_date: DateType
    assigned_count: int = Field(ge=0)
    plan: PlanResponse


class PlanItemCreateRequest(BaseModel):
    date: DateType
    title: str = Field(min_length=1, max_length=200)
    priority: str = Field(default="P2", pattern=r"^P[0-3]$")
    estimate_hours: float | None = Field(default=None, gt=0, le=24)
    done_definition: str = Field(default="", max_length=1000)
    checklist: list[PlanChecklistItem] = Field(default_factory=list)
    progress_status: PlanTaskStatus = "todo"
    progress_percent: int = Field(default=0, ge=0, le=100)
    progress_note: str = Field(default="", max_length=1000)
    insert_at_top: bool = True


class PlanItemDeleteRequest(BaseModel):
    date: DateType
    item_index: int = Field(ge=0, le=200)


class PlanItemRescheduleRequest(BaseModel):
    source_date: DateType
    item_index: int = Field(ge=0, le=200)
    target_date: DateType


class PlanItemSplitRescheduleRequest(BaseModel):
    source_date: DateType
    item_index: int = Field(ge=0, le=200)
    target_date: DateType
    checklist_indices: list[int] = Field(default_factory=list, min_length=1)
    new_task_title: str | None = Field(default=None, max_length=200)


class PlanItemMoveResponse(BaseModel):
    status: str = "ok"
    source_date: DateType
    target_date: DateType
    moved_count: int = 1
    source_plan: PlanResponse
    target_plan: PlanResponse


ReviewRangeType = Literal["day", "last_3_days", "last_week", "custom"]


class ReviewObjectiveItem(BaseModel):
    date: str = Field(default="", max_length=20)
    title: str = Field(min_length=1, max_length=300)
    priority: str = Field(default="P2", max_length=8)
    status: str = Field(default="todo", max_length=20)
    percent: int = Field(default=0, ge=0, le=100)
    note: str = Field(default="", max_length=1000)


class ReviewRequest(BaseModel):
    range_type: ReviewRangeType = "day"
    date: DateType | None = None
    start_date: DateType | None = None
    end_date: DateType | None = None
    persist: bool = True
    focus_text: str = Field(default="", max_length=1500)
    done_text: str = Field(default="", max_length=1000)
    undone_text: str = Field(default="", max_length=1000)
    blockers_text: str = Field(default="", max_length=1000)
    frontend_completed_list: list[ReviewObjectiveItem] = Field(default_factory=list)
    frontend_incomplete_list: list[ReviewObjectiveItem] = Field(default_factory=list)
    frontend_blocked_list: list[ReviewObjectiveItem] = Field(default_factory=list)
    frontend_dates: list[str] = Field(default_factory=list, max_length=31)


class ReviewResponse(BaseModel):
    date: DateType
    range_type: ReviewRangeType = "day"
    period_label: str = ""
    start_date: DateType
    end_date: DateType
    covered_dates: list[str] = Field(default_factory=list)
    daily_summary: str
    tomorrow_actions: list[str]
    source_model: str = "rule-based"
    fallback: bool = False
    memory_id: int


class MemoryWriteRequest(BaseModel):
    date: DateType | None = None
    type: str = Field(pattern="^(goal|plan|review|profile)$")
    content: str = Field(min_length=1, max_length=3000)
    summary: str = Field(min_length=1, max_length=300)
    metadata: dict[str, Any] | None = None


class MemoryWriteResponse(BaseModel):
    memory_id: int
    status: str


class StructuredMemory(BaseModel):
    goal: list[str]
    task_progress: list[str]
    issues: list[str]
    preferences: list[str]
    conclusions: list[str]


class MemoryIngestRequest(BaseModel):
    date: DateType | None = None
    dialogue_text: str = Field(min_length=1, max_length=5000)


class MemoryIngestResponse(BaseModel):
    memory_id: int
    status: str
    structured_memory: StructuredMemory


class MemorySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    top_k: int = Field(default=5, ge=1, le=20)


class MemoryRecord(BaseModel):
    id: int
    date: str | None
    type: str
    summary: str
    created_at: str


class MemorySearchResponse(BaseModel):
    items: list[MemoryRecord]


class ReviewRecord(BaseModel):
    id: int
    date: str | None
    type: str
    summary: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ReviewRecordsResponse(BaseModel):
    items: list[ReviewRecord]


class ReviewRecordResponse(BaseModel):
    item: ReviewRecord


class ReviewRecordUpdateRequest(BaseModel):
    summary: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=20000)


class ReviewRecordMutationResponse(BaseModel):
    status: str
    id: int


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str = Field(default="default", min_length=1, max_length=100)


class ChatResponse(BaseModel):
    reply: str
    model: str
    fallback: bool = False
    conversation_id: str
    turn_id: str


class ChatHistoryItem(BaseModel):
    id: int
    conversation_id: str
    turn_id: str
    role: str
    content: str
    model: str | None
    fallback: bool
    created_at: str


class ChatHistoryResponse(BaseModel):
    conversation_id: str
    items: list[ChatHistoryItem]


class ChatConversationItem(BaseModel):
    conversation_id: str
    latest_message: str
    updated_at: str
    message_count: int


class ChatConversationsResponse(BaseModel):
    items: list[ChatConversationItem]


class ChatConversationRenameRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=100)
    new_conversation_id: str = Field(min_length=1, max_length=100)


class ChatConversationDeleteRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=100)


class ChatConversationMutationResponse(BaseModel):
    status: str
    conversation_id: str
    affected_rows: int


class WebSearchItem(BaseModel):
    title: str
    url: str
    snippet: str
    published_at: str | None
    source: str


class WebSearchResponse(BaseModel):
    query: str
    provider: str
    items: list[WebSearchItem]
    notice: str | None = None


RepeatRule = Literal["daily", "workday", "weekend"]
ScheduleCategory = Literal["commute", "sleep", "meal", "custom"]
ScheduleSource = Literal["fixed", "ai", "manual"]


class FixedScheduleBase(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    category: ScheduleCategory = "custom"
    repeat_rule: RepeatRule = "daily"
    start_time: str = Field(pattern=r"^\d{2}:\d{2}(:\d{2})?$")
    end_time: str = Field(pattern=r"^\d{2}:\d{2}(:\d{2})?$")
    is_active: bool = True


class FixedScheduleCreateRequest(FixedScheduleBase):
    pass


class FixedScheduleUpdateRequest(FixedScheduleBase):
    pass


class FixedScheduleItem(FixedScheduleBase):
    id: int
    created_at: str
    updated_at: str


class FixedScheduleListResponse(BaseModel):
    items: list[FixedScheduleItem]


class FixedScheduleResponse(BaseModel):
    item: FixedScheduleItem


class FixedScheduleDeleteResponse(BaseModel):
    status: str
    id: int


class ScheduleEventChecklistItem(BaseModel):
    id: int
    content: str
    is_done: bool
    sort_order: int


class ScheduleEventItem(BaseModel):
    id: str
    date: str
    start_at: str
    end_at: str
    title: str
    source: ScheduleSource
    editable: bool
    plan_id: int | None = None
    checklist: list[ScheduleEventChecklistItem] = Field(default_factory=list)


class DayScheduleResponse(BaseModel):
    date: str
    items: list[ScheduleEventItem]


class ScheduleEventCreateRequest(BaseModel):
    date: DateType
    start_at: str = Field(pattern=r"^\d{2}:\d{2}(:\d{2})?$")
    end_at: str = Field(pattern=r"^\d{2}:\d{2}(:\d{2})?$")
    title: str = Field(min_length=1, max_length=200)
    source: ScheduleSource = "manual"
    editable: bool = True
    plan_id: int | None = None
    checklist: list[str] = Field(default_factory=list)


class ScheduleEventUpdateRequest(BaseModel):
    start_at: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}(:\d{2})?$")
    end_at: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}(:\d{2})?$")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    editable: bool | None = None
    checklist: list[str] | None = None


class ScheduleEventMoveRequest(BaseModel):
    start_at: str = Field(pattern=r"^\d{2}:\d{2}(:\d{2})?$")
    end_at: str = Field(pattern=r"^\d{2}:\d{2}(:\d{2})?$")


class ScheduleEventResizeRequest(BaseModel):
    end_at: str = Field(pattern=r"^\d{2}:\d{2}(:\d{2})?$")


class ScheduleEventResponse(BaseModel):
    item: ScheduleEventItem


class ScheduleEventDeleteResponse(BaseModel):
    status: str
    id: int
