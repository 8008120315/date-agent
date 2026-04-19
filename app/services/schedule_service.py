from __future__ import annotations

import json
from datetime import date as DateType, datetime

from ..config import get_settings
from ..db import get_connection, now_iso


class ScheduleService:
    VALID_REPEAT_RULES = {"daily", "workday", "weekend"}

    def list_fixed_schedules(self) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, title, category, repeat_rule, start_time, end_time,
                       is_active, created_at, updated_at
                FROM fixed_schedules
                ORDER BY start_time ASC, id ASC
                """
            ).fetchall()
        return [self._row_to_fixed_schedule(dict(row)) for row in rows]

    def list_effective_fixed_schedules_for_day(self, target_date: DateType) -> list[dict]:
        items = self._expand_fixed_schedules_for_day(target_date)
        return [
            {
                "title": item["title"],
                "start_at": item["start_at"],
                "end_at": item["end_at"],
                "source": item.get("source", "fixed"),
            }
            for item in items
        ]

    def create_fixed_schedule(self, payload: dict) -> dict:
        title = self._normalize_title(payload["title"], field_name="title")
        start_time = self._normalize_time_text(payload["start_time"])
        end_time = self._normalize_time_text(payload["end_time"])
        self._validate_time_range(start_time, end_time)

        repeat_rule = str(payload["repeat_rule"])
        if repeat_rule not in self.VALID_REPEAT_RULES:
            raise RuntimeError("Invalid repeat_rule.")

        now = now_iso()
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO fixed_schedules
                (title, category, repeat_rule, start_time, end_time, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    payload["category"],
                    repeat_rule,
                    start_time,
                    end_time,
                    1 if payload.get("is_active", True) else 0,
                    now,
                    now,
                ),
            )
            conn.commit()
            schedule_id = int(cur.lastrowid)
        return self.get_fixed_schedule(schedule_id)

    def get_fixed_schedule(self, schedule_id: int) -> dict:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, title, category, repeat_rule, start_time, end_time,
                       is_active, created_at, updated_at
                FROM fixed_schedules
                WHERE id = ?
                LIMIT 1
                """,
                (schedule_id,),
            ).fetchone()
        if not row:
            raise RuntimeError("Fixed schedule not found.")
        return self._row_to_fixed_schedule(dict(row))

    def update_fixed_schedule(self, schedule_id: int, payload: dict) -> dict:
        _ = self.get_fixed_schedule(schedule_id)

        title = self._normalize_title(payload["title"], field_name="title")
        start_time = self._normalize_time_text(payload["start_time"])
        end_time = self._normalize_time_text(payload["end_time"])
        self._validate_time_range(start_time, end_time)

        repeat_rule = str(payload["repeat_rule"])
        if repeat_rule not in self.VALID_REPEAT_RULES:
            raise RuntimeError("Invalid repeat_rule.")

        with get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE fixed_schedules
                SET title = ?,
                    category = ?,
                    repeat_rule = ?,
                    start_time = ?,
                    end_time = ?,
                    is_active = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    payload["category"],
                    repeat_rule,
                    start_time,
                    end_time,
                    1 if payload.get("is_active", True) else 0,
                    now_iso(),
                    schedule_id,
                ),
            )
            conn.commit()
            if cur.rowcount == 0:
                raise RuntimeError("Fixed schedule not found.")

        return self.get_fixed_schedule(schedule_id)

    def delete_fixed_schedule(self, schedule_id: int) -> int:
        with get_connection() as conn:
            cur = conn.execute(
                """
                DELETE FROM fixed_schedules
                WHERE id = ?
                """,
                (schedule_id,),
            )
            conn.commit()
            if cur.rowcount == 0:
                raise RuntimeError("Fixed schedule not found.")
            return int(cur.rowcount)

    def get_day_schedule(self, target_date: DateType) -> list[dict]:
        date_text = target_date.isoformat()

        with get_connection() as conn:
            event_rows = conn.execute(
                """
                SELECT id, date, start_at, end_at, title, source, plan_id, editable
                FROM schedule_events
                WHERE date = ?
                ORDER BY start_at ASC, id ASC
                """,
                (date_text,),
            ).fetchall()

        event_items = []
        for row in event_rows:
            base = dict(row)
            checklists = self._list_event_checklists(int(base["id"]))
            event_items.append(
                {
                    "id": str(base["id"]),
                    "date": base["date"],
                    "start_at": self._normalize_time_text(base["start_at"]),
                    "end_at": self._normalize_time_text(base["end_at"]),
                    "title": base["title"],
                    "source": base["source"],
                    "editable": bool(base["editable"]),
                    "plan_id": base["plan_id"],
                    "checklist": checklists,
                }
            )
        event_items.sort(key=lambda item: (item["start_at"], item["end_at"], item["id"]))
        return event_items

    def create_schedule_event(self, payload: dict) -> dict:
        date_text = payload["date"].isoformat()
        start_at = self._normalize_time_text(payload["start_at"])
        end_at = self._normalize_time_text(payload["end_at"])
        self._validate_time_range(start_at, end_at)
        self._ensure_no_conflicts(
            date_text=date_text,
            start_at=start_at,
            end_at=end_at,
            exclude_event_id=None,
        )

        now = now_iso()
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO schedule_events
                (date, start_at, end_at, title, source, plan_id, editable, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    date_text,
                    start_at,
                    end_at,
                    payload["title"].strip(),
                    payload.get("source", "manual"),
                    payload.get("plan_id"),
                    1 if payload.get("editable", True) else 0,
                    now,
                    now,
                ),
            )
            event_id = int(cur.lastrowid)
            self._replace_checklist_items(
                conn=conn,
                event_id=event_id,
                checklist=payload.get("checklist") or [],
            )
            conn.commit()

        return self.get_schedule_event(event_id)

    def replace_ai_events_from_plan(
        self,
        *,
        target_date: DateType,
        plan_items: list[dict],
        schedule_mode: str = "fixed",
    ) -> list[dict]:
        date_text = target_date.isoformat()
        self._delete_ai_events(date_text)
        self._refresh_fixed_events_for_day(target_date)

        windows = self._build_available_windows(target_date, schedule_mode=schedule_mode)
        if not windows:
            return []

        drafts = self._build_ai_event_drafts(date_text, plan_items, windows)
        created: list[dict] = []
        for draft in drafts:
            created.append(self.create_schedule_event(draft))
        return created

    def get_schedule_event(self, event_id: int) -> dict:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, date, start_at, end_at, title, source, plan_id, editable
                FROM schedule_events
                WHERE id = ?
                LIMIT 1
                """,
                (event_id,),
            ).fetchone()

        if not row:
            raise RuntimeError("Schedule event not found.")

        base = dict(row)
        return {
            "id": str(base["id"]),
            "date": base["date"],
            "start_at": self._normalize_time_text(base["start_at"]),
            "end_at": self._normalize_time_text(base["end_at"]),
            "title": base["title"],
            "source": base["source"],
            "editable": bool(base["editable"]),
            "plan_id": base["plan_id"],
            "checklist": self._list_event_checklists(int(base["id"])),
        }

    def update_schedule_event(self, event_id: int, payload: dict) -> dict:
        existing = self.get_schedule_event(event_id)
        if not existing["editable"] and (
            payload.get("start_at") is not None
            or payload.get("end_at") is not None
            or payload.get("title") is not None
            or payload.get("checklist") is not None
        ):
            raise RuntimeError("Event is not editable.")

        start_at = (
            self._normalize_time_text(payload["start_at"])
            if payload.get("start_at") is not None
            else existing["start_at"]
        )
        end_at = (
            self._normalize_time_text(payload["end_at"])
            if payload.get("end_at") is not None
            else existing["end_at"]
        )
        title = payload.get("title")
        title = title.strip() if isinstance(title, str) else existing["title"]
        editable = payload.get("editable")
        if editable is None:
            editable_flag = existing["editable"]
        else:
            editable_flag = bool(editable)

        self._validate_time_range(start_at, end_at)
        self._ensure_no_conflicts(
            date_text=existing["date"],
            start_at=start_at,
            end_at=end_at,
            exclude_event_id=event_id,
        )

        with get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE schedule_events
                SET start_at = ?, end_at = ?, title = ?, editable = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    start_at,
                    end_at,
                    title,
                    1 if editable_flag else 0,
                    now_iso(),
                    event_id,
                ),
            )
            if cur.rowcount == 0:
                conn.rollback()
                raise RuntimeError("Schedule event not found.")

            if payload.get("checklist") is not None:
                self._replace_checklist_items(
                    conn=conn,
                    event_id=event_id,
                    checklist=payload.get("checklist") or [],
                )
            conn.commit()

        return self.get_schedule_event(event_id)

    def move_schedule_event(self, event_id: int, start_at: str, end_at: str) -> dict:
        return self.update_schedule_event(
            event_id=event_id,
            payload={"start_at": start_at, "end_at": end_at},
        )

    def resize_schedule_event(self, event_id: int, end_at: str) -> dict:
        return self.update_schedule_event(
            event_id=event_id,
            payload={"end_at": end_at},
        )

    def delete_schedule_event(self, event_id: int) -> int:
        with get_connection() as conn:
            cur = conn.execute(
                """
                DELETE FROM schedule_events
                WHERE id = ?
                """,
                (event_id,),
            )
            conn.commit()
            if cur.rowcount == 0:
                raise RuntimeError("Schedule event not found.")
            return int(cur.rowcount)

    def _list_event_checklists(self, event_id: int) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, content, is_done, sort_order
                FROM event_checklist_items
                WHERE event_id = ?
                ORDER BY sort_order ASC, id ASC
                """,
                (event_id,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "content": row["content"],
                "is_done": bool(row["is_done"]),
                "sort_order": int(row["sort_order"]),
            }
            for row in rows
        ]

    def _replace_checklist_items(self, conn, event_id: int, checklist: list[str]) -> None:
        conn.execute(
            """
            DELETE FROM event_checklist_items
            WHERE event_id = ?
            """,
            (event_id,),
        )
        for index, content in enumerate(checklist):
            text = str(content).strip()
            if not text:
                continue
            conn.execute(
                """
                INSERT INTO event_checklist_items
                (event_id, content, is_done, sort_order, created_at)
                VALUES (?, ?, 0, ?, ?)
                """,
                (event_id, text, index, now_iso()),
            )

    def _expand_fixed_schedules_for_day(self, target_date: DateType) -> list[dict]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, title, category, repeat_rule, start_time, end_time, is_active
                FROM fixed_schedules
                WHERE is_active = 1
                ORDER BY start_time ASC, id ASC
                """
            ).fetchall()

        date_text = target_date.isoformat()
        weekday = target_date.weekday()  # Monday=0
        items: list[dict] = []
        db_items = [dict(row) for row in rows]
        file_items = self._load_file_fixed_schedules()

        for data in db_items + file_items:
            repeat_rule = data["repeat_rule"]
            if repeat_rule == "workday" and weekday >= 5:
                continue
            if repeat_rule == "weekend" and weekday < 5:
                continue

            start_time = self._normalize_time_text(data["start_time"])
            end_time = self._normalize_time_text(data["end_time"])
            try:
                self._validate_time_range(start_time, end_time)
            except RuntimeError:
                continue

            items.append(
                {
                    "id": f"fixed-{data.get('id', data.get('title', 'local'))}-{date_text}",
                    "date": date_text,
                    "start_at": start_time,
                    "end_at": end_time,
                    "title": data["title"],
                    "source": "fixed",
                    "editable": True,
                    "plan_id": None,
                    "checklist": [],
                }
            )

        return items

    def _load_file_fixed_schedules(self) -> list[dict]:
        path = get_settings().fixed_schedule_config_file
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []

        items: list[dict] = []
        for idx, row in enumerate(raw):
            if not isinstance(row, dict):
                continue
            title = str(row.get("title", "")).strip()
            category = str(row.get("category", "custom")).strip() or "custom"
            repeat_rule = str(row.get("repeat_rule", "daily")).strip() or "daily"
            start_time = str(row.get("start_time", "")).strip()
            end_time = str(row.get("end_time", "")).strip()
            is_active = bool(row.get("is_active", True))
            if not title or not start_time or not end_time or not is_active:
                continue
            if repeat_rule not in self.VALID_REPEAT_RULES:
                continue
            items.append(
                {
                    "id": f"local-{idx + 1}",
                    "title": title,
                    "category": category,
                    "repeat_rule": repeat_rule,
                    "start_time": start_time,
                    "end_time": end_time,
                    "is_active": 1,
                }
            )
        return items

    def _build_available_windows(self, target_date: DateType, *, schedule_mode: str) -> list[tuple[str, str]]:
        if schedule_mode not in {"fixed", "full_day"}:
            schedule_mode = "fixed"

        if schedule_mode == "full_day":
            base_start, base_end = "00:00:00", "23:59:59"
        else:
            base_start, base_end = "08:00:00", "22:00:00"

        blockers = self._list_event_blocks_for_day(target_date.isoformat())

        windows: list[tuple[str, str]] = []
        cursor = base_start
        for start_at, end_at in blockers:
            if end_at <= base_start or start_at >= base_end:
                continue
            clipped_start = max(start_at, base_start)
            clipped_end = min(end_at, base_end)
            if clipped_start > cursor:
                windows.append((cursor, clipped_start))
            if clipped_end > cursor:
                cursor = clipped_end
            if cursor >= base_end:
                break

        if cursor < base_end:
            windows.append((cursor, base_end))

        return [w for w in windows if w[1] > w[0]]

    def _list_event_blocks_for_day(self, date_text: str) -> list[tuple[str, str]]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT start_at, end_at
                FROM schedule_events
                WHERE date = ?
                ORDER BY start_at ASC, end_at ASC
                """,
                (date_text,),
            ).fetchall()
        return [(self._normalize_time_text(row["start_at"]), self._normalize_time_text(row["end_at"])) for row in rows]

    def _materialize_default_fixed_events_for_day(self, target_date: DateType) -> None:
        date_text = target_date.isoformat()
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM schedule_events
                WHERE date = ?
                  AND source = 'fixed'
                LIMIT 1
                """,
                (date_text,),
            ).fetchone()
        if row:
            return

        defaults = self._expand_fixed_schedules_for_day(target_date)
        for item in defaults:
            try:
                self.create_schedule_event(
                    {
                        "date": target_date,
                        "start_at": item["start_at"],
                        "end_at": item["end_at"],
                        "title": item["title"],
                        "source": "fixed",
                        "editable": True,
                        "checklist": [],
                    }
                )
            except RuntimeError:
                # Skip default blocks that conflict with existing persisted events.
                continue

    def _refresh_fixed_events_for_day(self, target_date: DateType) -> None:
        date_text = target_date.isoformat()
        with get_connection() as conn:
            conn.execute(
                """
                DELETE FROM schedule_events
                WHERE date = ?
                  AND source = 'fixed'
                """,
                (date_text,),
            )
            conn.commit()

        defaults = self._expand_fixed_schedules_for_day(target_date)
        for item in defaults:
            try:
                self.create_schedule_event(
                    {
                        "date": target_date,
                        "start_at": item["start_at"],
                        "end_at": item["end_at"],
                        "title": item["title"],
                        "source": "fixed",
                        "editable": True,
                        "checklist": [],
                    }
                )
            except RuntimeError:
                # Skip default blocks that conflict with existing persisted events.
                continue

    def _build_ai_event_drafts(
        self,
        date_text: str,
        plan_items: list[dict],
        windows: list[tuple[str, str]],
    ) -> list[dict]:
        tasks = [item for item in plan_items if isinstance(item, dict)][:3]
        if not tasks:
            return []

        drafts: list[dict] = []
        available_windows = list(windows)
        period_windows = [
            ("上午", "08:00:00", "12:00:00"),
            ("下午", "13:00:00", "18:00:00"),
            ("晚上", "19:00:00", "22:30:00"),
        ]

        for idx, item in enumerate(tasks):
            period_name, period_start, period_end = period_windows[idx]
            scoped_windows = self._intersect_windows(available_windows, period_start, period_end)
            if not scoped_windows:
                # Keep period semantics strict: morning/afternoon/evening should not fall back to other periods.
                continue

            start_at, end_at = self._pick_longest_window(scoped_windows)
            available_windows = self._reserve_slot(available_windows, start_at, end_at)

            title_raw = str(item.get("title", "")).strip() or f"任务 {idx + 1}"
            title = title_raw if title_raw.startswith(period_name) else f"{period_name}：{title_raw}"
            checklist = self._normalize_event_checklist(item)

            drafts.append(
                {
                    "date": datetime.strptime(date_text, "%Y-%m-%d").date(),
                    "start_at": start_at,
                    "end_at": end_at,
                    "title": title,
                    "source": "ai",
                    "editable": True,
                    "checklist": checklist,
                }
            )

        return drafts

    def _intersect_windows(
        self,
        windows: list[tuple[str, str]],
        scoped_start: str,
        scoped_end: str,
    ) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for start_at, end_at in windows:
            start = max(start_at, scoped_start)
            end = min(end_at, scoped_end)
            if end > start:
                result.append((start, end))
        return result

    def _pick_longest_window(self, windows: list[tuple[str, str]]) -> tuple[str, str]:
        best = windows[0]
        best_len = self._time_to_seconds(best[1]) - self._time_to_seconds(best[0])
        for start_at, end_at in windows[1:]:
            current_len = self._time_to_seconds(end_at) - self._time_to_seconds(start_at)
            if current_len > best_len:
                best = (start_at, end_at)
                best_len = current_len
        return best

    def _reserve_slot(
        self,
        windows: list[tuple[str, str]],
        start_at: str,
        end_at: str,
    ) -> list[tuple[str, str]]:
        updated: list[tuple[str, str]] = []
        for win_start, win_end in windows:
            if end_at <= win_start or start_at >= win_end:
                updated.append((win_start, win_end))
                continue
            if start_at > win_start:
                updated.append((win_start, start_at))
            if end_at < win_end:
                updated.append((end_at, win_end))
        return [w for w in updated if w[1] > w[0]]

    def _normalize_event_checklist(self, item: dict) -> list[str]:
        raw = item.get("checklist", [])
        steps = [str(step).strip() for step in raw if str(step).strip()] if isinstance(raw, list) else []
        if len(steps) >= 2:
            return steps[:6]
        done_def = str(item.get("done_definition", "")).strip()
        if done_def:
            return [done_def]
        return ["完成该时段核心任务并记录结果"]

    def _delete_ai_events(self, date_text: str) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                DELETE FROM schedule_events
                WHERE date = ?
                  AND source = 'ai'
                """,
                (date_text,),
            )
            conn.commit()

    def _estimate_minutes(self, estimate_hours: object) -> int:
        try:
            hours = float(estimate_hours)
            if hours <= 0:
                return 45
            return max(20, int(round(hours * 60)))
        except (TypeError, ValueError):
            return 45

    def _take_slot(
        self,
        *,
        duration_min: int,
        windows: list[tuple[str, str]],
        window_idx: int,
        cursor: str,
    ) -> tuple[str, str, int, str] | None:
        need = duration_min * 60
        i = window_idx
        local_cursor = cursor
        while i < len(windows):
            win_start, win_end = windows[i]
            if local_cursor < win_start:
                local_cursor = win_start
            start_sec = self._time_to_seconds(local_cursor)
            end_limit = self._time_to_seconds(win_end)
            if start_sec + need <= end_limit:
                end_text = self._seconds_to_time(start_sec + need)
                return local_cursor, end_text, i, end_text
            i += 1
            if i < len(windows):
                local_cursor = windows[i][0]
        return None

    def _time_to_seconds(self, value: str) -> int:
        hh, mm, ss = [int(part) for part in value.split(":")]
        return hh * 3600 + mm * 60 + ss

    def _seconds_to_time(self, value: int) -> str:
        safe = max(0, min(24 * 3600 - 1, int(value)))
        hh = safe // 3600
        mm = (safe % 3600) // 60
        ss = safe % 60
        return f"{hh:02d}:{mm:02d}:{ss:02d}"

    def _ensure_no_conflicts(
        self,
        *,
        date_text: str,
        start_at: str,
        end_at: str,
        exclude_event_id: int | None,
    ) -> None:
        with get_connection() as conn:
            if exclude_event_id is None:
                row = conn.execute(
                    """
                    SELECT id
                    FROM schedule_events
                    WHERE date = ?
                      AND start_at < ?
                      AND end_at > ?
                    LIMIT 1
                    """,
                    (date_text, end_at, start_at),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT id
                    FROM schedule_events
                    WHERE date = ?
                      AND id <> ?
                      AND start_at < ?
                      AND end_at > ?
                    LIMIT 1
                    """,
                    (date_text, exclude_event_id, end_at, start_at),
                ).fetchone()

        if row:
            raise RuntimeError("Schedule conflict with an existing event.")

    def _normalize_time_text(self, value: str) -> str:
        text = str(value).strip()
        if text in {"24:00", "24:00:00"}:
            return "23:59:59"
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                return parsed.strftime("%H:%M:%S")
            except ValueError:
                continue
        raise RuntimeError("Invalid time format, expected HH:MM or HH:MM:SS.")

    def _normalize_title(self, value: object, *, field_name: str) -> str:
        text = str(value).strip()
        if not text:
            raise RuntimeError(f"{field_name} cannot be empty.")
        return text

    def _validate_time_range(self, start_at: str, end_at: str) -> None:
        if end_at <= start_at:
            raise RuntimeError("end_at must be later than start_at.")

    def _row_to_fixed_schedule(self, row: dict) -> dict:
        return {
            "id": int(row["id"]),
            "title": row["title"],
            "category": row["category"],
            "repeat_rule": row["repeat_rule"],
            "start_time": self._normalize_time_text(row["start_time"]),
            "end_time": self._normalize_time_text(row["end_time"]),
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
