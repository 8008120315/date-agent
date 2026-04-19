from pathlib import Path
from datetime import datetime
from uuid import uuid4
import re

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import build_local_context_block, get_settings
from .db import init_db
from .schemas import (
    ChatConversationDeleteRequest,
    ChatConversationMutationResponse,
    ChatConversationRenameRequest,
    ChatConversationsResponse,
    ChatHistoryResponse,
    ChatRequest,
    ChatResponse,
    DayScheduleResponse,
    FixedScheduleCreateRequest,
    FixedScheduleDeleteResponse,
    FixedScheduleListResponse,
    FixedScheduleResponse,
    FixedScheduleUpdateRequest,
    MemoryIngestRequest,
    MemoryIngestResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    MemoryWriteRequest,
    MemoryWriteResponse,
    PlanDraftAssignRequest,
    PlanDraftAssignResponse,
    PlanDraftGenerateRequest,
    PlanDraftGenerateResponse,
    PlanDayCommitRequest,
    PlanItemCreateRequest,
    PlanItemDeleteRequest,
    PlanItemMoveResponse,
    PlanItemRegenerateRequest,
    PlanItemProgressUpdateRequest,
    PlanItemRescheduleRequest,
    PlanItemSplitRescheduleRequest,
    PlanRequest,
    PlanResponse,
    ReviewRequest,
    ReviewRecordMutationResponse,
    ReviewRecordResponse,
    ReviewRecordUpdateRequest,
    ReviewRecordsResponse,
    ReviewResponse,
    ScheduleEventCreateRequest,
    ScheduleEventDeleteResponse,
    ScheduleEventResponse,
    ScheduleEventUpdateRequest,
    WebSearchResponse,
)
from .services.chat_service import ChatService
from .services.llm_service import LLMService
from .services.memory_service import MemoryService
from .services.plan_service import PlanService
from .services.review_service import ReviewService
from .services.schedule_service import ScheduleService
from .services.search_service import SearchService

app = FastAPI(title="Date Agent MVP", version="0.1.0")

base_dir = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(base_dir / "templates"))
app.mount("/static", StaticFiles(directory=str(base_dir / "static")), name="static")

memory_service = MemoryService()
plan_service = PlanService()
review_service = ReviewService()
search_service = SearchService()
chat_service = ChatService()
llm_service = LLMService()
schedule_service = ScheduleService()
settings = get_settings()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse)
def home() -> RedirectResponse:
    return RedirectResponse(url="/plan/generate", status_code=307)


@app.get("/plan/generate", response_class=HTMLResponse)
def generate_plan_page(request: Request):
    return templates.TemplateResponse(
        "generate.html",
        {
            "request": request,
            "active_page": "generate",
        },
    )


@app.get("/plan/daily", response_class=HTMLResponse)
def daily_plan_page(request: Request):
    return templates.TemplateResponse(
        "daily.html",
        {
            "request": request,
            "active_page": "daily",
        },
    )


@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request):
    return templates.TemplateResponse(
        "review.html",
        {
            "request": request,
            "active_page": "review",
        },
    )


@app.get("/review/records", response_class=HTMLResponse)
def review_records_page(request: Request):
    return templates.TemplateResponse(
        "review_records.html",
        {
            "request": request,
            "active_page": "review_records",
        },
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> RedirectResponse:
    return RedirectResponse(url="/static/favicon.svg", status_code=307)


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    return templates.TemplateResponse(
        "chat.html",
        {
            "request": request,
            "active_page": "chat",
        },
    )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/tools/search", response_model=WebSearchResponse)
def tools_search(
    q: str = Query(min_length=1),
    max_results: int = Query(default=5, ge=1, le=10),
) -> WebSearchResponse:
    try:
        result = search_service.search(query=q, max_results=max_results)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return WebSearchResponse(
        query=result.query,
        provider=result.provider,
        items=[item.__dict__ for item in result.items],
        notice=result.notice,
    )


@app.post("/api/plan", response_model=PlanResponse)
def create_plan(payload: PlanRequest) -> PlanResponse:
    try:
        return plan_service.create_plan(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/plan/draft/generate", response_model=PlanDraftGenerateResponse)
def generate_plan_draft_pool(payload: PlanDraftGenerateRequest) -> PlanDraftGenerateResponse:
    try:
        return plan_service.generate_draft_pool(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/plan/draft/assign", response_model=PlanDraftAssignResponse)
def assign_plan_draft_tasks(payload: PlanDraftAssignRequest) -> PlanDraftAssignResponse:
    try:
        return plan_service.assign_draft_tasks(payload)
    except RuntimeError as exc:
        detail = str(exc)
        status = 404 if "No daily plan found" in detail else 400
        raise HTTPException(status_code=status, detail=detail) from exc


@app.get("/api/plan/day", response_model=PlanResponse)
def get_plan_by_date(date: str = Query(min_length=10, max_length=10)) -> PlanResponse:
    try:
        target = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
    return plan_service.get_plan(target)


@app.put("/api/plan/item", response_model=PlanResponse)
def update_plan_item(payload: PlanItemProgressUpdateRequest) -> PlanResponse:
    try:
        return plan_service.update_plan_item_progress(payload)
    except RuntimeError as exc:
        detail = str(exc)
        status = 404 if "No daily plan found" in detail else 400
        raise HTTPException(status_code=status, detail=detail) from exc


@app.put("/api/plan/day", response_model=PlanResponse)
def commit_plan_day(payload: PlanDayCommitRequest) -> PlanResponse:
    try:
        return plan_service.commit_plan_items(payload)
    except RuntimeError as exc:
        detail = str(exc)
        status = 404 if "No daily plan found" in detail else 400
        raise HTTPException(status_code=status, detail=detail) from exc


@app.post("/api/plan/item/regenerate", response_model=PlanResponse)
def regenerate_plan_item(payload: PlanItemRegenerateRequest) -> PlanResponse:
    try:
        return plan_service.regenerate_plan_item(payload)
    except RuntimeError as exc:
        detail = str(exc)
        status = 404 if "No daily plan found" in detail else 400
        raise HTTPException(status_code=status, detail=detail) from exc


@app.post("/api/plan/item/manual", response_model=PlanResponse)
def create_manual_plan_item(payload: PlanItemCreateRequest) -> PlanResponse:
    try:
        return plan_service.create_plan_item(payload)
    except RuntimeError as exc:
        detail = str(exc)
        status = 404 if "No daily plan found" in detail else 400
        raise HTTPException(status_code=status, detail=detail) from exc


@app.delete("/api/plan/item", response_model=PlanResponse)
def delete_plan_item(
    date: str = Query(min_length=10, max_length=10),
    item_index: int = Query(ge=0, le=200),
) -> PlanResponse:
    try:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
    try:
        return plan_service.delete_plan_item(
            PlanItemDeleteRequest(date=target_date, item_index=item_index),
        )
    except RuntimeError as exc:
        detail = str(exc)
        status = 404 if "No daily plan found" in detail else 400
        raise HTTPException(status_code=status, detail=detail) from exc


@app.post("/api/plan/item/reschedule", response_model=PlanItemMoveResponse)
def reschedule_plan_item(payload: PlanItemRescheduleRequest) -> PlanItemMoveResponse:
    try:
        return plan_service.reschedule_plan_item(payload)
    except RuntimeError as exc:
        detail = str(exc)
        status = 404 if "No daily plan found" in detail else 400
        raise HTTPException(status_code=status, detail=detail) from exc


@app.post("/api/plan/item/split-migrate", response_model=PlanItemMoveResponse)
def split_and_reschedule_plan_item(payload: PlanItemSplitRescheduleRequest) -> PlanItemMoveResponse:
    try:
        return plan_service.split_reschedule_checklist(payload)
    except RuntimeError as exc:
        detail = str(exc)
        status = 404 if "No daily plan found" in detail else 400
        raise HTTPException(status_code=status, detail=detail) from exc


@app.post("/api/review", response_model=ReviewResponse)
def create_review(payload: ReviewRequest) -> ReviewResponse:
    try:
        return review_service.create_review(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/review/records", response_model=ReviewRecordsResponse)
def list_review_records(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str = Query(default="", max_length=200),
    start_date: str | None = Query(default=None, min_length=10, max_length=10),
    end_date: str | None = Query(default=None, min_length=10, max_length=10),
) -> ReviewRecordsResponse:
    for label, raw in (("start_date", start_date), ("end_date", end_date)):
        if raw:
            try:
                datetime.strptime(raw, "%Y-%m-%d")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"{label} must be YYYY-MM-DD") from exc
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be earlier than or equal to end_date")

    rows = memory_service.query_reviews(
        limit=limit,
        offset=offset,
        query=q,
        start_date=start_date,
        end_date=end_date,
    )
    return ReviewRecordsResponse(items=rows)


@app.put("/api/review/records/{record_id}", response_model=ReviewRecordResponse)
def update_review_record(record_id: int, payload: ReviewRecordUpdateRequest) -> ReviewRecordResponse:
    item = memory_service.update_review(
        record_id=record_id,
        summary=payload.summary,
        content=payload.content,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="review record not found")
    return ReviewRecordResponse(item=item)


@app.delete("/api/review/records/{record_id}", response_model=ReviewRecordMutationResponse)
def delete_review_record(record_id: int) -> ReviewRecordMutationResponse:
    deleted = memory_service.delete_review(record_id=record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="review record not found")
    return ReviewRecordMutationResponse(status="ok", id=record_id)


@app.get("/api/schedule/fixed", response_model=FixedScheduleListResponse)
def list_fixed_schedules() -> FixedScheduleListResponse:
    return FixedScheduleListResponse(items=schedule_service.list_fixed_schedules())


@app.post("/api/schedule/fixed", response_model=FixedScheduleResponse)
def create_fixed_schedule(payload: FixedScheduleCreateRequest) -> FixedScheduleResponse:
    try:
        item = schedule_service.create_fixed_schedule(payload.model_dump())
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FixedScheduleResponse(item=item)


@app.put("/api/schedule/fixed/{schedule_id}", response_model=FixedScheduleResponse)
def update_fixed_schedule(schedule_id: int, payload: FixedScheduleUpdateRequest) -> FixedScheduleResponse:
    try:
        item = schedule_service.update_fixed_schedule(schedule_id, payload.model_dump())
    except RuntimeError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status, detail=detail) from exc
    return FixedScheduleResponse(item=item)


@app.delete("/api/schedule/fixed/{schedule_id}", response_model=FixedScheduleDeleteResponse)
def delete_fixed_schedule(schedule_id: int) -> FixedScheduleDeleteResponse:
    try:
        schedule_service.delete_fixed_schedule(schedule_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FixedScheduleDeleteResponse(status="ok", id=schedule_id)


@app.get("/api/schedule/day", response_model=DayScheduleResponse)
def get_day_schedule(date: str = Query(min_length=10, max_length=10)) -> DayScheduleResponse:
    try:
        target = datetime.strptime(date, "%Y-%m-%d").date()
        items = schedule_service.get_day_schedule(target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DayScheduleResponse(date=date, items=items)


@app.post("/api/schedule/events", response_model=ScheduleEventResponse)
def create_schedule_event(payload: ScheduleEventCreateRequest) -> ScheduleEventResponse:
    try:
        item = schedule_service.create_schedule_event(payload.model_dump())
    except RuntimeError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status, detail=detail) from exc
    return ScheduleEventResponse(item=item)


@app.put("/api/schedule/events/{event_id}", response_model=ScheduleEventResponse)
def update_schedule_event(event_id: int, payload: ScheduleEventUpdateRequest) -> ScheduleEventResponse:
    try:
        item = schedule_service.update_schedule_event(
            event_id=event_id,
            payload=payload.model_dump(exclude_none=True),
        )
    except RuntimeError as exc:
        detail = str(exc)
        status = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status, detail=detail) from exc
    return ScheduleEventResponse(item=item)


@app.delete("/api/schedule/events/{event_id}", response_model=ScheduleEventDeleteResponse)
def delete_schedule_event(event_id: int) -> ScheduleEventDeleteResponse:
    try:
        schedule_service.delete_schedule_event(event_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ScheduleEventDeleteResponse(status="ok", id=event_id)


@app.post("/api/memory/write", response_model=MemoryWriteResponse)
def write_memory(payload: MemoryWriteRequest) -> MemoryWriteResponse:
    memory_id = memory_service.write_memory(
        type_=payload.type,
        content=payload.content,
        summary=payload.summary,
        memory_date=payload.date,
        metadata=payload.metadata,
    )
    return MemoryWriteResponse(memory_id=memory_id, status="ok")


@app.post("/api/memory/ingest", response_model=MemoryIngestResponse)
def ingest_memory(payload: MemoryIngestRequest) -> MemoryIngestResponse:
    memory_id, structured_memory = memory_service.ingest_dialogue_memory(
        dialogue_text=payload.dialogue_text,
        memory_date=payload.date,
    )
    return MemoryIngestResponse(
        memory_id=memory_id,
        status="ok",
        structured_memory=structured_memory,
    )


@app.post("/api/memory/search", response_model=MemorySearchResponse)
def search_memory(payload: MemorySearchRequest) -> MemorySearchResponse:
    rows = memory_service.search(payload.query, payload.top_k)
    return MemorySearchResponse(items=rows)


def _chat_fallback_reply(message: str, err_text: str) -> tuple[str, str]:
    preview = message.strip()[:120]
    reply = (
        "当前模型暂时不可用，我先给你一个保底回复。\n"
        f"你刚才说的是：{preview}\n"
        "你可以继续补充上下文，我会先按已有信息帮你推进。"
    )
    return f"{reply}\n\n[fallback reason] {err_text}", "rule-based"


def _build_chat_memory_block(message: str) -> str:
    refs = memory_service.build_memory_refs(
        query=message,
        top_k=6,
        fallback_recent=3,
        types=("goal", "plan", "review", "dialogue", "profile"),
    )
    return "\n".join(f"- {row}" for row in refs) if refs else "- N/A"


def _should_capture_dialogue_memory(message: str) -> bool:
    text = str(message or "").strip()
    if len(text) < 8:
        return False
    if re.fullmatch(r"[\W_]+", text):
        return False
    if text.lower() in {"hi", "hello", "你好", "在吗", "谢谢", "ok"}:
        return False
    return True



def _normalize_conversation_title(raw: str) -> str:
    text = str(raw or "").replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r'["\'`~!@#$%^&*()+=\[\]{}<>\\/|:;,.?！？。；，、]+', " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_")
    if not text:
        return "新会话"
    return text[:40]


def _fallback_conversation_title(first_user_message: str) -> str:
    text = str(first_user_message or "").strip()
    if not text:
        return "新会话"
    first_line = text.splitlines()[0].strip()
    first_chunk = re.split(r"[。！？!?；;，,]", first_line)[0].strip()
    return _normalize_conversation_title(first_chunk or first_line)


def _generate_conversation_title(
    *,
    first_user_message: str,
    first_assistant_reply: str,
) -> str:
    fallback_title = _fallback_conversation_title(first_user_message)
    if not llm_service.is_ready():
        return fallback_title

    system_prompt = (
        "You generate short chat session titles.\n"
        "Return only one concise title in Simplified Chinese.\n"
        "No quotes, no markdown, no punctuation at the end.\n"
        "Prefer 6-16 Chinese characters."
    )
    user_prompt = (
        "Please create a session title based on the first turn.\n"
        f"User: {first_user_message.strip()}\n"
        f"Assistant: {first_assistant_reply.strip()[:200]}\n"
        "Return title only."
    )
    try:
        title, _ = llm_service.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            history=None,
        )
        normalized = _normalize_conversation_title(title)
        return normalized or fallback_title
    except Exception:
        return fallback_title


def _build_unique_conversation_id(base_title: str, current_id: str) -> str:
    normalized = _normalize_conversation_title(base_title)
    if not normalized:
        return current_id
    if normalized == current_id:
        return current_id
    if not chat_service.conversation_exists(conversation_id=normalized):
        return normalized

    for idx in range(2, 100):
        suffix = f"-{idx}"
        cut_len = max(1, 100 - len(suffix))
        candidate = f"{normalized[:cut_len]}{suffix}"
        if candidate == current_id:
            return current_id
        if not chat_service.conversation_exists(conversation_id=candidate):
            return candidate

    return current_id


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    history = chat_service.context_messages(conversation_id=payload.conversation_id, limit=20)
    is_first_turn = len(history) == 0
    turn_id = uuid4().hex
    chat_service.append_message(
        conversation_id=payload.conversation_id,
        turn_id=turn_id,
        role="user",
        content=payload.message,
    )

    memory_block = _build_chat_memory_block(payload.message)
    system_prompt = (
        "You are a practical personal assistant.\n"
        "Respond in concise Simplified Chinese by default.\n"
        "Give actionable suggestions and keep formatting lightweight.\n"
        "When relevant, use the following local context:\n"
        f"{build_local_context_block(settings)}\n"
        "Also use the provided long-term memory snippets when they are relevant."
    )
    user_prompt = (
        "[User message]\n"
        f"{payload.message}\n\n"
        "[Local context]\n"
        f"{build_local_context_block(settings)}\n\n"
        "[Long-term memory snippets]\n"
        f"{memory_block}"
    )
    fallback = False
    try:
        reply_text, model_name = llm_service.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            history=history,
        )
    except Exception as exc:  # keep chat usable when LLM unavailable
        fallback = True
        reply_text, model_name = _chat_fallback_reply(payload.message, str(exc))

    chat_service.append_message(
        conversation_id=payload.conversation_id,
        turn_id=turn_id,
        role="assistant",
        content=reply_text,
        model=model_name,
        fallback=fallback,
    )
    if _should_capture_dialogue_memory(payload.message):
        try:
            memory_service.ingest_dialogue_memory(
                dialogue_text=f"用户：{payload.message.strip()}\n助手：{reply_text.strip()}",
                memory_date=datetime.now().date(),
            )
        except Exception:
            # Keep chat response stable even if memory ingest fails.
            pass

    final_conversation_id = payload.conversation_id
    if is_first_turn:
        generated_title = _generate_conversation_title(
            first_user_message=payload.message,
            first_assistant_reply=reply_text,
        )
        target_conversation_id = _build_unique_conversation_id(
            base_title=generated_title,
            current_id=payload.conversation_id,
        )
        if target_conversation_id != payload.conversation_id:
            affected = chat_service.rename_conversation(
                conversation_id=payload.conversation_id,
                new_conversation_id=target_conversation_id,
            )
            if affected > 0:
                final_conversation_id = target_conversation_id

    return ChatResponse(
        reply=reply_text,
        model=model_name,
        fallback=fallback,
        conversation_id=final_conversation_id,
        turn_id=turn_id,
    )


@app.get("/api/chat/history", response_model=ChatHistoryResponse)
def chat_history(
    conversation_id: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=200, ge=1, le=500),
) -> ChatHistoryResponse:
    items = chat_service.list_messages(conversation_id=conversation_id, limit=limit)
    return ChatHistoryResponse(
        conversation_id=conversation_id,
        items=[item.__dict__ for item in items],
    )


@app.get("/api/chat/conversations", response_model=ChatConversationsResponse)
def chat_conversations(limit: int = Query(default=100, ge=1, le=500)) -> ChatConversationsResponse:
    items = chat_service.list_conversations(limit=limit)
    return ChatConversationsResponse(items=[item.__dict__ for item in items])


@app.post("/api/chat/conversations/rename", response_model=ChatConversationMutationResponse)
def chat_conversation_rename(
    payload: ChatConversationRenameRequest,
) -> ChatConversationMutationResponse:
    if payload.conversation_id == payload.new_conversation_id:
        raise HTTPException(status_code=400, detail="new_conversation_id must be different")
    if not chat_service.conversation_exists(conversation_id=payload.conversation_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    if chat_service.conversation_exists(conversation_id=payload.new_conversation_id):
        raise HTTPException(status_code=409, detail="new_conversation_id already exists")

    affected = chat_service.rename_conversation(
        conversation_id=payload.conversation_id,
        new_conversation_id=payload.new_conversation_id,
    )
    return ChatConversationMutationResponse(
        status="ok",
        conversation_id=payload.new_conversation_id,
        affected_rows=affected,
    )


@app.post("/api/chat/conversations/delete", response_model=ChatConversationMutationResponse)
def chat_conversation_delete(
    payload: ChatConversationDeleteRequest,
) -> ChatConversationMutationResponse:
    if not chat_service.conversation_exists(conversation_id=payload.conversation_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    affected = chat_service.delete_conversation(conversation_id=payload.conversation_id)
    return ChatConversationMutationResponse(
        status="ok",
        conversation_id=payload.conversation_id,
        affected_rows=affected,
    )
