#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


DEFAULT_MESSAGE = "你好，我想准备暑期转正实习的简历和项目计划"


def _safe_print(label: str, payload: object) -> None:
    try:
        print(f"{label}{payload}")
    except UnicodeEncodeError:
        encoded = str(payload).encode("unicode_escape").decode("ascii")
        print(f"{label}{encoded}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test chat endpoint with Chinese-safe UTF-8 script input."
    )
    parser.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help="User message to send to /api/chat (default: a Chinese sample message).",
    )
    parser.add_argument(
        "--conversation-id",
        default=f"chat-smoke-{uuid.uuid4().hex[:8]}",
        help="Initial conversation_id.",
    )
    args = parser.parse_args()

    client = TestClient(app, raise_server_exceptions=False)
    chat_resp = client.post(
        "/api/chat",
        json={
            "conversation_id": args.conversation_id,
            "message": args.message,
        },
    )
    _safe_print("chat status: ", chat_resp.status_code)
    if chat_resp.status_code != 200:
        _safe_print("chat body: ", chat_resp.text)
        return 1

    payload = chat_resp.json()
    _safe_print("chat response: ", json.dumps(payload, ensure_ascii=False, indent=2))

    final_conversation_id = payload.get("conversation_id", args.conversation_id)
    history_resp = client.get(
        "/api/chat/history",
        params={"conversation_id": final_conversation_id, "limit": 10},
    )
    _safe_print("history status: ", history_resp.status_code)
    if history_resp.status_code != 200:
        _safe_print("history body: ", history_resp.text)
        return 1

    items = history_resp.json().get("items", [])
    _safe_print("history message count: ", len(items))
    for item in items[-2:]:
        role = item.get("role", "")
        content = item.get("content", "")
        _safe_print(f"{role}: ", content)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

