import asyncio
import json
import sqlite3
import threading
import time
from typing import Dict, List, Tuple

from .config import Settings
from .feishu_client import FeishuClient
from .opencode_client import OpenCodeClient


class SessionMonitor:
    def __init__(self, settings: Settings, opencode_client: OpenCodeClient, feishu_client: FeishuClient):
        self._settings = settings
        self._opencode_client = opencode_client
        self._feishu_client = feishu_client
        self._last_ts: Dict[str, int] = {}
        self._last_part_ids: Dict[str, str] = {}
        self._stop_event = threading.Event()
        self._thread = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        interval = max(1.0, self._settings.opencode_watch_interval_s)
        while not self._stop_event.is_set():
            try:
                asyncio.run(self._poll_once())
            except Exception:
                pass
            self._stop_event.wait(interval)

    async def _poll_once(self) -> None:
        sessions = await self._opencode_client.list_online_sessions()
        session_ids = [item.session_id for item in sessions]
        if not session_ids:
            return

        events = self._fetch_new_text_events(session_ids)
        for session_id, role, text, tty in events:
            if role == "assistant" and not bool(self._settings.opencode_watch_include_assistant):
                continue
            if role == "user" and not bool(self._settings.opencode_watch_include_user):
                continue
            await self._send_event(session_id, role, text, tty)

    def _fetch_new_text_events(self, session_ids: List[str]) -> List[Tuple[str, str, str, str]]:
        conn = sqlite3.connect(self._settings.opencode_db_path)
        conn.row_factory = sqlite3.Row
        events: List[Tuple[str, str, str, str]] = []
        tty_map: Dict[str, str] = {}
        cached = self._opencode_client.list_cached_sessions(include_offline=True)
        for item in cached:
            tty_map[item.session_id] = item.tty
        try:
            for session_id in session_ids:
                last_ts = self._last_ts.get(session_id, 0)
                rows = conn.execute(
                    """
                    SELECT p.id AS part_id, p.time_created AS part_time, p.data AS part_data,
                           m.data AS message_data
                    FROM part p
                    JOIN message m ON m.id = p.message_id
                    WHERE p.session_id = ? AND p.time_created > ?
                    ORDER BY p.time_created ASC
                    """,
                    (session_id, last_ts),
                ).fetchall()
                if not rows:
                    continue

                for row in rows:
                    part_id = str(row["part_id"])
                    if self._last_part_ids.get(session_id) == part_id:
                        continue
                    part_data = _load_json(str(row["part_data"]))
                    if part_data.get("type") != "text":
                        continue
                    text = str(part_data.get("text") or "").strip()
                    if not text:
                        continue
                    message_data = _load_json(str(row["message_data"]))
                    role = str(message_data.get("role") or "unknown")
                    tty = tty_map.get(session_id, "")
                    events.append((session_id, role, text, tty))
                    self._last_part_ids[session_id] = part_id

                self._last_ts[session_id] = int(rows[-1]["part_time"])
            return events
        finally:
            conn.close()

    async def _send_event(self, session_id: str, role: str, text: str, tty: str) -> None:
        receive_id = self._settings.feishu_notify_receive_id
        if not receive_id:
            return
        receive_id_type = self._settings.feishu_notify_receive_id_type or "chat_id"
        summary = text if len(text) <= 800 else f"{text[:800]}..."
        prefix = f"[opencode][{session_id}]"
        if tty:
            prefix = f"{prefix}[{tty}]"
        body = f"{prefix}[{role}]\n{summary}"
        await self._feishu_client.send_text(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            text=body,
        )


def _load_json(raw: str) -> Dict[str, object]:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return {}
    except json.JSONDecodeError:
        return {}
