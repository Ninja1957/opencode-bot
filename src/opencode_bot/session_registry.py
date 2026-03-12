import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
from typing import Dict, List, Optional, Sequence, Set, Tuple
import logging

from .models import OnlineSession


logger = logging.getLogger(__name__)


class SessionRegistry:
    def __init__(
        self,
        db_path: str,
        title_refresh_s: int = 7200,
        title_max_len: int = 18,
        title_agent_enabled: bool = False,
        title_agent_session_id: str = "ses_title_agent_00001",
        title_agent_timeout_s: float = 20.0,
        opencode_bin: str = "opencode",
        hidden_session_ids: Optional[Sequence[str]] = None,
    ):
        self._db_path = db_path
        self._uid = str(os.getuid())
        self._known: Dict[str, OnlineSession] = {}
        self._title_cache: Dict[str, Tuple[str, int]] = {}
        self._title_refreshing: Set[str] = set()
        self._lock = threading.Lock()
        self._title_refresh_s = max(60, int(title_refresh_s))
        self._title_max_len = max(8, int(title_max_len))
        self._title_agent_enabled = bool(title_agent_enabled)
        self._title_agent_session_id = title_agent_session_id or "ses_title_agent_00001"
        self._title_agent_timeout_s = max(3.0, float(title_agent_timeout_s))
        self._opencode_bin = opencode_bin
        self._title_agent_available = False
        self._title_agent_next_check_ts = 0
        self._hidden_session_ids = {item for item in (hidden_session_ids or []) if item}

    def refresh(self, force_title_refresh: bool = False) -> List[OnlineSession]:
        now = int(time.time())
        active = self._scan_active_sessions(now, force_title_refresh=force_title_refresh)
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

    def _scan_active_sessions(self, now: int, force_title_refresh: bool = False) -> List[OnlineSession]:
        cmd = ["ps", "-eo", "pid,uid,tty,args"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return []

        session_re = re.compile(r"(?:^|\s)(?:-s|--session)(?:\s+|=)(ses_[A-Za-z0-9_-]+)(?:\s|$)")
        raw: Dict[str, Tuple[int, str, int]] = {}
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
            if session_id in self._hidden_session_ids:
                continue
            try:
                pid = int(pid_text)
            except ValueError:
                pid = 0
            score = self._session_candidate_score(args, tty)
            prev = raw.get(session_id)
            if prev is None:
                raw[session_id] = (pid, tty, score)
                continue
            prev_pid, prev_tty, prev_score = prev
            if score > prev_score:
                raw[session_id] = (pid, tty, score)
                continue
            if score == prev_score and pid > 0 and (prev_pid <= 0 or pid < prev_pid):
                raw[session_id] = (pid, tty, score)

        if not raw:
            return []

        title_map, directory_map = self._load_session_meta(list(raw.keys()), force_title_refresh=force_title_refresh)
        sessions: List[OnlineSession] = []
        for session_id, (pid, tty, _) in raw.items():
            title = title_map.get(session_id) or session_id
            directory = directory_map.get(session_id, "")
            workdir_available = self._is_workdir_available(pid, directory)
            sessions.append(
                OnlineSession(
                    session_id=session_id,
                    display_name=title,
                    status="online",
                    pid=pid,
                    tty=tty,
                    last_seen_ts=now,
                    directory=directory,
                    workdir_available=workdir_available,
                )
            )
        return sessions

    @staticmethod
    def _is_workdir_available(pid: int, directory: str) -> bool:
        if directory and os.path.isdir(directory):
            return True
        if pid <= 0:
            return False
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return False
        return bool(cwd and os.path.isdir(cwd))

    @staticmethod
    def _session_candidate_score(args: str, tty: str) -> int:
        score = 0
        if re.search(r"(?:^|\s)-s\s+ses_", args):
            score += 100
        if re.search(r"(?:^|\s)--session(?:\s+|=)ses_", args):
            score += 20
        if " opencode run " in f" {args} ":
            score -= 40
        if tty and tty != "?":
            score += 5
        return score

    def _load_session_meta(self, session_ids: List[str], force_title_refresh: bool = False) -> Tuple[Dict[str, str], Dict[str, str]]:
        db_path = Path(self._db_path)
        if not db_path.exists() or not session_ids:
            return {}, {}
        conn = sqlite3.connect(str(db_path))
        try:
            placeholders = ",".join(["?"] * len(session_ids))
            rows = self._load_session_rows(conn, placeholders, session_ids)
            output: Dict[str, str] = {}
            directories: Dict[str, str] = {}
            default_titles: Dict[str, str] = {}
            for row in rows:
                session_id = str(row[0])
                title = str(row[1]) if row[1] else session_id
                directory = str(row[2]) if len(row) > 2 and row[2] else ""
                directories[session_id] = directory
                default_titles[session_id] = title

            now = int(time.time())
            for session_id in session_ids:
                cached = self._title_cache.get(session_id)
                if cached is None or force_title_refresh:
                    title = self._build_short_cn_title(conn, session_id, default_titles.get(session_id, session_id))
                    self._title_cache[session_id] = (title, now)
                    output[session_id] = title
                    continue

                cached_title, updated_ts = cached
                output[session_id] = cached_title
                if now - updated_ts >= self._title_refresh_s:
                    self._refresh_title_async(session_id, default_titles.get(session_id, session_id))

            return output, directories
        finally:
            conn.close()

    def _refresh_title_async(self, session_id: str, default_title: str) -> None:
        with self._lock:
            if session_id in self._title_refreshing:
                return
            self._title_refreshing.add(session_id)

        def runner() -> None:
            title = default_title
            db_path = Path(self._db_path)
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                try:
                    title = self._build_short_cn_title(conn, session_id, default_title)
                finally:
                    conn.close()
            with self._lock:
                self._title_cache[session_id] = (title, int(time.time()))
                self._title_refreshing.discard(session_id)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()

    @staticmethod
    def _load_session_rows(conn: sqlite3.Connection, placeholders: str, session_ids: List[str]) -> List[Tuple[object, ...]]:
        try:
            sql = f"SELECT id, title, directory FROM session WHERE id IN ({placeholders})"
            return conn.execute(sql, session_ids).fetchall()
        except sqlite3.OperationalError:
            legacy_sql = f"SELECT id, title FROM session WHERE id IN ({placeholders})"
            return conn.execute(legacy_sql, session_ids).fetchall()

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

    def _build_short_cn_title(self, conn: sqlite3.Connection, session_id: str, default_title: str) -> str:
        try:
            rows = conn.execute(
                """
                SELECT p.data
                FROM part p
                JOIN message m ON m.id = p.message_id
                WHERE p.session_id = ?
                  AND json_extract(p.data, '$.type') = 'text'
                ORDER BY p.time_created DESC
                LIMIT 20
                """,
                (session_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return self._compact_title(default_title)
        texts: List[str] = []
        for row in rows:
            raw = str(row[0]) if row[0] else ""
            text = self._extract_text(raw)
            if not text:
                continue
            if text.startswith("/"):
                continue
            if self._is_title_agent_noise(text):
                continue
            texts.append(text)
            if len(texts) >= 5:
                break

        if not texts:
            return self._compact_title(default_title)

        generated = self._summarize_with_agent(texts)
        if generated:
            return self._compact_title(generated)

        merged = "；".join(reversed(texts[:2]))
        return self._compact_title(merged)

    def _summarize_with_agent(self, texts: List[str]) -> str:
        if not self._title_agent_enabled:
            logger.debug("title-agent disabled; skip summarization")
            return ""
        if not self._ensure_title_agent_available():
            return ""
        if not texts:
            logger.debug("title-agent skipped: no text snippets")
            return ""

        sample = "\n".join(f"- {item}" for item in texts[:5])
        prompt = (
            "你是会话标题总结器。根据以下最近日志，输出一个简短中文标题。"
            "要求：不超过100个汉字，不要标点，不要解释，不要换行。\n"
            f"日志:\n{sample}"
        )

        binary = self._opencode_bin
        if binary and not os.path.exists(binary):
            binary = "opencode"
        cmd = [
            binary,
            "run",
            "--session",
            self._title_agent_session_id,
            "--format",
            "default",
            prompt,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=max(3, int(self._title_agent_timeout_s)),
            )
        except Exception as exc:
            logger.warning("title-agent invocation failed session=%s err=%s", self._title_agent_session_id, exc)
            return ""

        output = (proc.stdout or "").strip()
        if proc.returncode != 0 or not output:
            err = (proc.stderr or "").strip()
            logger.warning(
                "title-agent returned empty/nonzero session=%s code=%s stderr=%s",
                self._title_agent_session_id,
                proc.returncode,
                err[:300],
            )
            return ""
        line = output.splitlines()[0].strip()
        line = line.replace("`", "").replace("\"", "")
        logger.debug("title-agent summarized session=%s title=%s", self._title_agent_session_id, line[:80])
        return " ".join(line.split())

    def _ensure_title_agent_available(self) -> bool:
        now = int(time.time())
        if now < self._title_agent_next_check_ts:
            return self._title_agent_available

        binary = self._opencode_bin
        if binary and not os.path.exists(binary):
            binary = "opencode"
        probe_cmd = [
            binary,
            "run",
            "--session",
            self._title_agent_session_id,
            "--format",
            "default",
            "请仅回复OK",
        ]
        try:
            proc = subprocess.run(
                probe_cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=max(3, int(min(10, self._title_agent_timeout_s))),
            )
        except Exception as exc:
            self._title_agent_available = False
            self._title_agent_next_check_ts = now + 600
            logger.warning(
                "title-agent healthcheck failed session=%s err=%s; fallback to local title",
                self._title_agent_session_id,
                exc,
            )
            return False

        if proc.returncode == 0:
            self._title_agent_available = True
            self._title_agent_next_check_ts = now + 1800
            logger.info("title-agent healthcheck ok session=%s", self._title_agent_session_id)
            return True

        self._title_agent_available = False
        self._title_agent_next_check_ts = now + 600
        logger.warning(
            "title-agent unavailable session=%s code=%s stderr=%s; fallback to local title",
            self._title_agent_session_id,
            proc.returncode,
            (proc.stderr or "").strip()[:300],
        )
        return False

    @staticmethod
    def _extract_text(raw: str) -> str:
        match = re.search(r'"text"\s*:\s*"(.*?)"', raw)
        if not match:
            return ""
        text = match.group(1)
        text = text.replace("\\n", " ").replace("\\t", " ").replace('\\"', '"')
        return " ".join(text.split()).strip()

    @staticmethod
    def _compact_title_with_limit(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return f"{text[:limit]}..."

    def _compact_title(self, text: str) -> str:
        return self._compact_title_with_limit(text, self._title_max_len)

    @staticmethod
    def _is_title_agent_noise(text: str) -> bool:
        lowered = text.strip().lower()
        compact = " ".join(lowered.split())
        if compact in {"ok", "请仅回复ok", "请仅回复ok。", "请仅回复ok!", "仅回复ok交互"}:
            return True
        if compact.startswith("你是会话标题总结器"):
            return True
        markers = [
            "[search-mode]",
            "[analyze-mode]",
            "analysis mode",
            "maximize search effort",
            "context gathering (parallel)",
            "if complex - do not struggle alone",
            "synthesize findings before proceeding",
            "<system-reminder>",
            "[background task completed]",
            "[all background tasks complete]",
        ]
        for marker in markers:
            if marker in compact:
                return True
        return False
