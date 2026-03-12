import asyncio
import json
import logging
import sqlite3
import threading
import time
import urllib.error
from typing import Dict, List, Tuple

from .config import Settings
from .feishu_client import FeishuClient
from .feishu_client import FeishuSendError
from .opencode_client import OpenCodeClient
from .storage import Storage


logger = logging.getLogger(__name__)


class SessionMonitor:
    def __init__(
        self,
        settings: Settings,
        opencode_client: OpenCodeClient,
        feishu_client: FeishuClient,
        storage: Storage,
    ):
        self._settings = settings
        self._opencode_client = opencode_client
        self._feishu_client = feishu_client
        self._storage = storage
        self._last_ts: Dict[str, int] = {}
        self._last_part_ids: Dict[str, str] = {}
        self._peer_suppress_until: Dict[str, float] = {}
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
        for part_id, session_id, role, text, tty in events:
            if role == "assistant" and not bool(self._settings.opencode_watch_include_assistant):
                continue
            if role == "user" and not bool(self._settings.opencode_watch_include_user):
                continue
            await self._send_event(part_id, session_id, role, text, tty)

    def _fetch_new_text_events(self, session_ids: List[str]) -> List[Tuple[str, str, str, str, str]]:
        conn = sqlite3.connect(self._settings.opencode_db_path, timeout=30.0)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        events: List[Tuple[str, str, str, str, str]] = []
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
                    events.append((part_id, session_id, role, text, tty))
                    self._last_part_ids[session_id] = part_id

                self._last_ts[session_id] = int(rows[-1]["part_time"])
            return events
        finally:
            conn.close()

    async def _send_event(self, part_id: str, session_id: str, role: str, text: str, tty: str) -> None:
        summary = text if len(text) <= 800 else f"{text[:800]}..."
        prefix = f"[opencode][{session_id}]"
        if tty:
            prefix = f"{prefix}[{tty}]"
        body = f"{prefix}[{role}]\n{summary}"

        peers = self._storage.list_peers()
        if not peers:
            fallback_id = self._settings.feishu_notify_receive_id
            if not fallback_id:
                return
            fallback_type = self._settings.feishu_notify_receive_id_type or "chat_id"
            peers = [(f"{fallback_type}:{fallback_id}", fallback_id, fallback_type)]

        for peer_key, receive_id, receive_id_type in peers:
            now = time.time()
            suppress_until = self._peer_suppress_until.get(peer_key, 0.0)
            if suppress_until > now:
                continue

            if not self._storage.try_mark_broadcast_sent(session_id, part_id, peer_key):
                continue

            bound = self._storage.get_bound_session(peer_key)
            if role == "user" and bound == session_id:
                continue
            if role == "assistant" and bound == session_id and self._storage.was_recent_response(peer_key, session_id, text):
                continue
            try:
                started = time.perf_counter()
                if bound == session_id:
                    await self._feishu_client.send_text(
                        receive_id=receive_id,
                        receive_id_type=receive_id_type,
                        text=body,
                    )
                else:
                    await self._feishu_client.send_session_bind_prompt(
                        receive_id=receive_id,
                        receive_id_type=receive_id_type,
                        session_id=session_id,
                        preview=summary,
                    )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.debug(
                    "session monitor send ok peer=%s session=%s part=%s role=%s receive_id_type=%s elapsed_ms=%s",
                    peer_key,
                    session_id,
                    part_id,
                    role,
                    receive_id_type,
                    elapsed_ms,
                )
                self._peer_suppress_until.pop(peer_key, None)
            except Exception as exc:
                if isinstance(exc, FeishuSendError) and _looks_like_timeout(exc):
                    self._peer_suppress_until[peer_key] = time.time() + 120
                    logger.warning(
                        "session monitor suppressing peer for 2m due to timeout peer=%s session=%s part=%s detail=%s",
                        peer_key,
                        session_id,
                        part_id,
                        exc.detail,
                    )
                    continue
                if isinstance(exc, FeishuSendError) and exc.error_code == 99992351:
                    self._peer_suppress_until[peer_key] = time.time() + 86400
                    self._storage.remove_peer(peer_key)
                    logger.warning(
                        "session monitor removed invalid peer due to open_id error peer=%s session=%s receive_id_type=%s",
                        peer_key,
                        session_id,
                        receive_id_type,
                    )
                    continue
                if isinstance(exc, urllib.error.HTTPError) and exc.code == 400:
                    self._peer_suppress_until[peer_key] = time.time() + 600
                    logger.warning(
                        "session monitor suppressing peer for 10m due to HTTP 400 peer=%s session=%s",
                        peer_key,
                        session_id,
                    )
                else:
                    logger.error(
                        "session monitor send failed peer=%s session=%s part=%s role=%s receive_id_type=%s err_type=%s err=%s",
                        peer_key,
                        session_id,
                        part_id,
                        role,
                        receive_id_type,
                        type(exc).__name__,
                        exc,
                    )


def _load_json(raw: str) -> Dict[str, object]:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return {}
    except json.JSONDecodeError:
        return {}


def _looks_like_timeout(exc: FeishuSendError) -> bool:
    text = f"{exc.error_message} {exc.detail}".lower()
    return "timed out" in text or "timeout" in text
