# Date Agent MVP

Date Agent MVP is a FastAPI + Jinja2 task management assistant with 4 main modules:

- Generate Task Draft Pool (`/plan/generate`)
- Daily Task Board (`/plan/daily`)
- Review Generator (`/review`)
- Review Records (`/review/records`)
- Multi-session Chat (`/chat`)

It supports LLM-first generation with rule-based fallback, day-level task editing, cross-day task migration, review drafting/saving, and chat history management.

## 1) Quick Start

Requirements:

- Python 3.10+
- Windows PowerShell / Bash

Setup:

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Run:

```bash
python -m uvicorn app.main:app --reload
```

Open:

- Home (redirect): <http://127.0.0.1:8000>
- Generate: <http://127.0.0.1:8000/plan/generate>
- Daily board: <http://127.0.0.1:8000/plan/daily>
- Review: <http://127.0.0.1:8000/review>
- Review records: <http://127.0.0.1:8000/review/records>
- Chat: <http://127.0.0.1:8000/chat>

## 2) Current Product Flows

### 2.1 Generate Task Draft Pool

- Select `start_date` and `end_date`
- Enter one `goal_text`
- Generate a multi-day draft task inbox
- Select tasks and assign them to a target date
- Assigned tasks are removed from draft inbox and persisted into that date's plan

### 2.2 Daily Task Board

- Browse day by day
- Edit task title / priority / estimate / definition / checklist / notes
- Progress is checklist-driven (auto status + percent)
- Manual create and physical delete supported
- Whole-task reschedule to another date
- Split selected subtasks into a new task and migrate to another date

### 2.3 Review

- Supports day / last 3 days / last week / custom range
- Auto-loads related task context (done/undone/blockers)
- Generates review content into local draft first
- Persist to DB only when clicking "Save review record"

### 2.4 Review Records

- Search/filter list
- Read/copy/edit/delete records

### 2.5 Chat

- Conversation list and rename/delete
- Fallback-safe responses when model is unavailable

## 3) API Overview

### 3.1 Health and Search

- `GET /api/health`
- `GET /api/tools/search?q=...&max_results=...`

### 3.2 Plan APIs

- `POST /api/plan`
- `POST /api/plan/draft/generate`
- `POST /api/plan/draft/assign`
- `GET /api/plan/day?date=YYYY-MM-DD`
- `PUT /api/plan/item`
- `POST /api/plan/item/manual`
- `DELETE /api/plan/item?date=YYYY-MM-DD&item_index=N`
- `POST /api/plan/item/regenerate`
- `POST /api/plan/item/reschedule`
- `POST /api/plan/item/split-migrate`
- `PUT /api/plan/day`

### 3.3 Review APIs

- `POST /api/review`
- `GET /api/review/records`
- `PUT /api/review/records/{record_id}`
- `DELETE /api/review/records/{record_id}`

### 3.4 Schedule APIs

- `GET /api/schedule/fixed`
- `POST /api/schedule/fixed`
- `PUT /api/schedule/fixed/{schedule_id}`
- `DELETE /api/schedule/fixed/{schedule_id}`
- `GET /api/schedule/day?date=YYYY-MM-DD`
- `POST /api/schedule/events`
- `PUT /api/schedule/events/{event_id}`
- `DELETE /api/schedule/events/{event_id}`

### 3.5 Memory and Chat APIs

- `POST /api/memory/write`
- `POST /api/memory/ingest`
- `POST /api/memory/search`
- `POST /api/chat`
- `GET /api/chat/history?conversation_id=...&limit=...`
- `GET /api/chat/conversations`
- `POST /api/chat/conversations/rename`
- `POST /api/chat/conversations/delete`

## 4) Example Requests

Generate draft pool:

```bash
curl -X POST "http://127.0.0.1:8000/api/plan/draft/generate" \
  -H "Content-Type: application/json" \
  -d "{\"start_date\":\"2026-04-19\",\"end_date\":\"2026-04-21\",\"goal_text\":\"Prepare backend interview\"}"
```

Assign selected draft tasks to one date:

```bash
curl -X POST "http://127.0.0.1:8000/api/plan/draft/assign" \
  -H "Content-Type: application/json" \
  -d "{\"target_date\":\"2026-04-20\",\"goal_text\":\"Prepare backend interview\",\"tasks\":[{\"draft_id\":\"draft-1\",\"title\":\"Review OS basics\",\"priority\":\"P1\",\"estimate_hours\":1.5,\"done_definition\":\"Finish notes\",\"checklist\":[{\"content\":\"Read chapter 1\",\"is_done\":false}],\"progress_status\":\"todo\",\"progress_percent\":0,\"progress_note\":\"\"}]}"
```

## 5) Configuration

Main values come from `.env` (see `.env.example`):

- `DB_PATH` default: `data/date_agent.db`
- `FIXED_SCHEDULE_FILE` default: `data/fixed_schedules.json`
- `GLM_API_KEY`, `GLM_BASE_URL`, `GLM_CHAT_MODEL`
- `TAVILY_API_KEY` (optional for richer web search)

Notes:

- If GLM is not configured or call fails, plan/review/chat fall back to local logic.
- DB and runtime files are local-only (see `.gitignore`).

Fixed schedule JSON item example:

```json
{
  "title": "Lunch",
  "category": "meal",
  "repeat_rule": "daily",
  "start_time": "12:00:00",
  "end_time": "12:45:00",
  "is_active": true
}
```

`repeat_rule` supports: `daily`, `workday`, `weekend`.

## 6) Development Utilities

Pre-commit encoding check:

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

Smoke chat test:

```bash
python scripts/smoke_chat.py
python scripts/smoke_chat.py --message "Hello, plan my week"
```
