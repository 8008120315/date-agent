from contextlib import contextmanager
from datetime import datetime
import sqlite3

from .config import get_settings


def _ensure_db_parent() -> None:
    db_file = get_settings().db_file
    db_file.parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    _ensure_db_parent()
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                summary TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                goal_text TEXT,
                plan_json TEXT,
                review_text TEXT,
                summary_text TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fixed_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                repeat_rule TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schedule_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                start_at TEXT NOT NULL,
                end_at TEXT NOT NULL,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                plan_id INTEGER,
                editable INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_checklist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                is_done INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(event_id) REFERENCES schedule_events(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                model TEXT,
                fallback INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_schedule_events_date_start
            ON schedule_events(date, start_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_event_checklist_event_sort
            ON event_checklist_items(event_id, sort_order)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_fixed_schedules_repeat_active
            ON fixed_schedules(repeat_rule, is_active)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation_id
            ON chat_messages(conversation_id, id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_messages_turn_id
            ON chat_messages(turn_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memories_type_date
            ON memories(type, date, id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memories_created_at
            ON memories(created_at, id)
            """
        )
        conn.commit()


@contextmanager
def get_connection():
    _ensure_db_parent()
    db_file = get_settings().db_file
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
