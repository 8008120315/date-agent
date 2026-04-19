# Date Agent MVP

Date Agent MVP is a FastAPI + Jinja2 task management assistant with these modules:

- Generate Task Draft Pool (`/plan/generate`)
- Daily Task Board (`/plan/daily`)
- Review Generator (`/review`)
- Review Records (`/review/records`)
- Multi-session Chat (`/chat`)

It supports:

- AI-first generation with local fallback (plan/review/chat)
- Persistent daily tasks (edit, progress, reschedule, split-migrate)
- Review drafting and saved review records
- Long-term memory retrieval + chat memory ingest

---

## 1. Start From Zero (5-10 minutes)

### 1.1 Prerequisites

- Python 3.10+ (recommended 3.11/3.12)
- Git
- Windows PowerShell / macOS zsh / Linux bash

Check Python:

```bash
python --version
```

### 1.2 Clone

```bash
git clone https://github.com/8008120315/date-agent.git
cd date-agent
```

### 1.3 Create and activate virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 1.4 Configure environment variables

Windows:

```powershell
Copy-Item .env.example .env
```

macOS/Linux:

```bash
cp .env.example .env
```

Minimal `.env` setup:

- Keep `DB_PATH=data/date_agent.db`
- Optional but recommended: set `GLM_API_KEY=...`
- Optional: set `TAVILY_API_KEY=...` for richer web search

If `GLM_API_KEY` is empty, the app still runs with local fallback logic.

### 1.5 Run

```bash
python -m uvicorn app.main:app --reload
```

Open in browser:

- Home: <http://127.0.0.1:8000>
- Generate: <http://127.0.0.1:8000/plan/generate>
- Daily board: <http://127.0.0.1:8000/plan/daily>
- Review: <http://127.0.0.1:8000/review>
- Review records: <http://127.0.0.1:8000/review/records>
- Chat: <http://127.0.0.1:8000/chat>

Health check:

```bash
curl http://127.0.0.1:8000/api/health
```

Expected:

```json
{"status":"ok"}
```

Notes:

- Database tables are auto-created on startup (`app.db.init_db()`).
- Local runtime data is stored under `data/` by default.

---

## 2. Project Structure

```text
app/
  main.py                 # FastAPI routes
  db.py                   # SQLite init + connection
  schemas.py              # Pydantic models
  services/
    plan_service.py       # plan generation + CRUD + reschedule + split migrate
    review_service.py     # review generation and save flow
    memory_service.py     # long-term memory write/search/ingest
    chat_service.py       # chat history persistence
  templates/              # Jinja pages
  static/                 # frontend JS/CSS
data/
  date_agent.db           # sqlite db (auto-created)
```

---

## 3. Main Configuration (`.env`)

From `.env.example`:

- `APP_NAME`, `APP_ENV`
- `DB_PATH` (default: `data/date_agent.db`)
- `GLM_API_KEY`, `GLM_BASE_URL`, `GLM_CHAT_MODEL`, `GLM_CHAT_FALLBACK_MODEL`
- `TAVILY_API_KEY`, `WEB_SEARCH_TIMEOUT_SEC`, `WEB_SEARCH_MAX_RESULTS`
- `LOCAL_ADDRESS`, `DEFAULT_WEATHER_LOCATION`, `LOCAL_TIMEZONE`, `LOCAL_CONTEXT_HINT`
- `TIMELINE_START_HOUR`
- `FIXED_SCHEDULE_FILE` (default: `data/fixed_schedules.json`)

---

## 4. Core Product Flows

### 4.1 Generate Task Draft Pool

- Enter one `goal_text` (can include natural-language duration like "2周/3天/1个月")
- AI returns draft tasks
- Select tasks and assign to a target date (today or future only)

### 4.2 Daily Task Board

- View tasks by date
- Edit task title/priority/estimate/definition/checklist/note
- Progress can be checklist-driven
- Support manual add/delete/reschedule/split-migrate

### 4.3 Review

- Day / last 3 days / last week / custom range
- Auto-load related tasks as context
- Generate review draft first
- Persist only when clicking save

### 4.4 Review Records

- List/search/filter saved review records
- Copy/edit/delete record

### 4.5 Chat + Memory

- Multi-conversation chat history
- Long-term memory snippets are injected into chat prompt
- Qualified chat turns can be ingested as dialogue memory

---

## 5. API Overview

### 5.1 Health/Search

- `GET /api/health`
- `GET /api/tools/search?q=...&max_results=...`

### 5.2 Plan

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

### 5.3 Review

- `POST /api/review`
- `GET /api/review/records`
- `PUT /api/review/records/{record_id}`
- `DELETE /api/review/records/{record_id}`

### 5.4 Schedule

- `GET /api/schedule/fixed`
- `POST /api/schedule/fixed`
- `PUT /api/schedule/fixed/{schedule_id}`
- `DELETE /api/schedule/fixed/{schedule_id}`
- `GET /api/schedule/day?date=YYYY-MM-DD`
- `POST /api/schedule/events`
- `PUT /api/schedule/events/{event_id}`
- `DELETE /api/schedule/events/{event_id}`

### 5.5 Memory + Chat

- `POST /api/memory/write`
- `POST /api/memory/ingest`
- `POST /api/memory/search`
- `POST /api/chat`
- `GET /api/chat/history?conversation_id=...&limit=...`
- `GET /api/chat/conversations`
- `POST /api/chat/conversations/rename`
- `POST /api/chat/conversations/delete`

---

## 6. Example API Calls

Generate draft pool:

```bash
curl -X POST "http://127.0.0.1:8000/api/plan/draft/generate" \
  -H "Content-Type: application/json" \
  -d "{\"goal_text\":\"Prepare backend interview in around 2 weeks\"}"
```

Assign selected tasks to one date:

```bash
curl -X POST "http://127.0.0.1:8000/api/plan/draft/assign" \
  -H "Content-Type: application/json" \
  -d "{\"target_date\":\"2026-04-20\",\"goal_text\":\"Prepare backend interview\",\"tasks\":[{\"draft_id\":\"draft-1\",\"title\":\"Review OS basics\",\"priority\":\"P1\",\"estimate_hours\":1.5,\"done_definition\":\"Finish notes\",\"checklist\":[{\"content\":\"Read chapter 1\",\"is_done\":false}],\"progress_status\":\"todo\",\"progress_percent\":0,\"progress_note\":\"\"}]}"
```

---

## 7. Developer Utilities

Pre-commit encoding check:

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

Smoke test for chat:

```bash
python scripts/smoke_chat.py
python scripts/smoke_chat.py --message "Hello, plan my week"
```

---

## 8. Troubleshooting

### 8.1 `ModuleNotFoundError` when running

- Ensure virtual env is activated (`.venv`).
- Reinstall dependencies:

```bash
pip install -r requirements.txt
```

### 8.2 Port 8000 already in use

Run on another port:

```bash
python -m uvicorn app.main:app --reload --port 8001
```

### 8.3 PowerShell encoding issues (garbled text)

- Prefer PowerShell 7 (`pwsh`) if available.
- Keep workspace UTF-8 (`.editorconfig` + VS Code settings).
- In temporary terminal session:

```powershell
chcp 65001
[Console]::InputEncoding  = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
```

### 8.4 No AI output

- Check `GLM_API_KEY` in `.env`.
- Without API key, app uses local fallback logic by design.
