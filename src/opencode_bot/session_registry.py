import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from typing import Dict, List, Tuple

from .models import OnlineSession


class SessionRegistry:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._uid = str(os.getuid())
        self._known: Dict[str, OnlineSession] = {}

    def refresh(self) -> List[OnlineSession]:
        now = int(time.time())
        active = self._scan_active_sessions(now)
        active_ids = {item.session_id for item in active}

        for item in active:
            self._known[item.session_id] = item

        for session_id, item in list(self._known.items()):
            if session_id in active_ids:
                continue
            item.status = "offline"
            self._known[session_id] = item

        return self.list_sessions(include_offline=False)

    def list_sessions(self, include_offline: bool) -> List[OnlineSession]:
        values = list(self._known.values())
        if not include_offline:
            values = [item for item in values if item.status == "online"]
        values.sort(key=lambda item: (0 if item.status == "online" else 1, -item.last_seen_ts, item.session_id))
        return values

    def _scan_active_sessions(self, now: int) -> List[OnlineSession]:
        cmd = ["ps", "-eo", "pid,uid,tty,args"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return []

        session_re = re.compile(r"(?:^|\s)-s\s+(ses_[A-Za-z0-9]+)")
        raw: Dict[str, Tuple[int, str]] = {}
        for line in proc.stdout.splitlines()[1:]:
            parts = line.strip().split(None, 3)
            if len(parts) < 4:
                continue
            pid_text, uid, tty, args = parts
            if uid != self._uid:
                continue
            if "opencode" not in args:
                continue
            match = session_re.search(args)
            if not match:
                continue
            session_id = match.group(1)
            try:
                pid = int(pid_text)
            except ValueError:
                pid = 0
            raw[session_id] = (pid, tty)

        if not raw:
            return []

        title_map = self._load_titles(list(raw.keys()))
        sessions: List[OnlineSession] = []
        for session_id, (pid, tty) in raw.items():
            title = title_map.get(session_id) or session_id
            sessions.append(
                OnlineSession(
                    session_id=session_id,
                    display_name=title,
                    status="online",
                    pid=pid,
                    tty=tty,
                    last_seen_ts=now,
                )
            )
        return sessions

    def _load_titles(self, session_ids: List[str]) -> Dict[str, str]:
        db_path = Path(self._db_path)
        if not db_path.exists() or not session_ids:
            return {}
        conn = sqlite3.connect(str(db_path))
        try:
            placeholders = ",".join(["?"] * len(session_ids))
            sql = f"SELECT id, title FROM session WHERE id IN ({placeholders})"
            rows = conn.execute(sql, session_ids).fetchall()
            output: Dict[str, str] = {}
            need_fallback: List[str] = []
            for row in rows:
                session_id = str(row[0])
                title = str(row[1]) if row[1] else session_id
                if self._is_generic_title(title):
                    need_fallback.append(session_id)
                else:
                    output[session_id] = title

            if need_fallback:
                fallback = self._load_content_titles(conn, need_fallback)
                for session_id in need_fallback:
                    fallback_title = fallback.get(session_id)
                    if fallback_title:
                        output[session_id] = fallback_title
                    else:
                        output[session_id] = session_id
            return output
        finally:
            conn.close()

    @staticmethod
    def _is_generic_title(title: str) -> bool:
        lowered = title.strip().lower()
        if not lowered:
            return True
        if lowered.startswith("new session"):
            return True
        if lowered.startswith("新会话"):
            return True
        return False

    def _load_content_titles(self, conn: sqlite3.Connection, session_ids: List[str]) -> Dict[str, str]:
        output: Dict[str, str] = {}
        for session_id in session_ids:
            row = conn.execute(
                """
                SELECT p.data
                FROM part p
                JOIN message m ON m.id = p.message_id
                WHERE p.session_id = ?
                  AND json_extract(m.data, '$.role') = 'user'
                  AND json_extract(p.data, '$.type') = 'text'
                ORDER BY p.time_created DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                continue
            raw = str(row[0]) if row[0] else ""
            text = self._extract_text(raw)
            if not text:
                continue
            output[session_id] = self._compact_title(text)
        return output

    @staticmethod
    def _extract_text(raw: str) -> str:
        match = re.search(r'"text"\s*:\s*"(.*?)"', raw)
        if not match:
            return ""
        text = match.group(1)
        text = text.replace("\\n", " ").replace("\\t", " ").replace('\\"', '"')
        return " ".join(text.split()).strip()

    @staticmethod
    def _compact_title(text: str) -> str:
        limit = 40
        if len(text) <= limit:
            return text
        return f"{text[:limit]}..."
