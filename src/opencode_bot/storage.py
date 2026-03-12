import sqlite3
from datetime import datetime, timezone
import logging
from pathlib import Path
import threading
import time
from typing import List, Optional, Tuple


logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, db_path: str):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    @staticmethod
    def _is_locked_error(exc: sqlite3.OperationalError) -> bool:
        return "locked" in str(exc).lower() or "busy" in str(exc).lower()

    def _execute_write(self, sql: str, params: Tuple[object, ...]) -> sqlite3.Cursor:
        retries = 5
        for attempt in range(retries):
            try:
                with self._lock:
                    cur = self._conn.execute(sql, params)
                    self._conn.commit()
                return cur
            except sqlite3.OperationalError as exc:
                with self._lock:
                    self._conn.rollback()
                if not self._is_locked_error(exc) or attempt + 1 >= retries:
                    raise
                sleep_s = 0.05 * (attempt + 1)
                logger.warning("storage write locked, retry=%s sleep=%.2fs", attempt + 1, sleep_s)
                time.sleep(sleep_s)
            except sqlite3.IntegrityError:
                with self._lock:
                    self._conn.rollback()
                raise

        raise RuntimeError("unreachable write retry state")

    def _fetch_one(self, sql: str, params: Tuple[object, ...]) -> Optional[sqlite3.Row]:
        retries = 5
        for attempt in range(retries):
            try:
                with self._lock:
                    return self._conn.execute(sql, params).fetchone()
            except sqlite3.OperationalError as exc:
                if not self._is_locked_error(exc) or attempt + 1 >= retries:
                    raise
                sleep_s = 0.05 * (attempt + 1)
                logger.warning("storage read locked, retry=%s sleep=%.2fs", attempt + 1, sleep_s)
                time.sleep(sleep_s)
        return None

    def _fetch_all(self, sql: str, params: Tuple[object, ...] = ()) -> List[sqlite3.Row]:
        retries = 5
        for attempt in range(retries):
            try:
                with self._lock:
                    return list(self._conn.execute(sql, params).fetchall())
            except sqlite3.OperationalError as exc:
                if not self._is_locked_error(exc) or attempt + 1 >= retries:
                    raise
                sleep_s = 0.05 * (attempt + 1)
                logger.warning("storage read-all locked, retry=%s sleep=%.2fs", attempt + 1, sleep_s)
                time.sleep(sleep_s)
        return []

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS feishu_session_bindings (
                    peer_key TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_messages (
                    message_id TEXT PRIMARY KEY,
                    peer_key TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS relay_rounds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    peer_key TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    request_text TEXT NOT NULL,
                    response_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS feishu_peers (
                    peer_key TEXT PRIMARY KEY,
                    receive_id TEXT NOT NULL,
                    receive_id_type TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS broadcast_dedup (
                    session_id TEXT NOT NULL,
                    part_id TEXT NOT NULL,
                    peer_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, part_id, peer_key)
                )
                """
            )
            self._conn.commit()

    def try_mark_processed(self, message_id: str, peer_key: str) -> bool:
        try:
            self._execute_write(
                "INSERT INTO processed_messages(message_id, peer_key, created_at) VALUES(?, ?, ?)",
                (message_id, peer_key, utc_now()),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def bind_session(self, peer_key: str, session_id: str) -> None:
        self._execute_write(
            """
            INSERT INTO feishu_session_bindings(peer_key, session_id, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(peer_key) DO UPDATE SET
                session_id=excluded.session_id,
                updated_at=excluded.updated_at
            """,
            (peer_key, session_id, utc_now()),
        )

    def get_bound_session(self, peer_key: str) -> Optional[str]:
        row = self._fetch_one(
            "SELECT session_id FROM feishu_session_bindings WHERE peer_key = ?",
            (peer_key,),
        )
        if row is None:
            return None
        return str(row["session_id"])

    def unbind_session(self, peer_key: str) -> bool:
        cur = self._execute_write(
            "DELETE FROM feishu_session_bindings WHERE peer_key = ?",
            (peer_key,),
        )
        return cur.rowcount > 0

    def remove_peer(self, peer_key: str) -> bool:
        cur = self._execute_write(
            "DELETE FROM feishu_peers WHERE peer_key = ?",
            (peer_key,),
        )
        return cur.rowcount > 0

    def save_round(
        self,
        message_id: str,
        peer_key: str,
        session_id: str,
        request_text: str,
        response_text: str,
    ) -> None:
        self._execute_write(
            """
            INSERT INTO relay_rounds(message_id, peer_key, session_id, request_text, response_text, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (message_id, peer_key, session_id, request_text, response_text, utc_now()),
        )

    def upsert_peer(self, peer_key: str, receive_id: str, receive_id_type: str) -> None:
        self._execute_write(
            """
            INSERT INTO feishu_peers(peer_key, receive_id, receive_id_type, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(peer_key) DO UPDATE SET
                receive_id=excluded.receive_id,
                receive_id_type=excluded.receive_id_type,
                updated_at=excluded.updated_at
            """,
            (peer_key, receive_id, receive_id_type, utc_now()),
        )

    def list_peers(self) -> "List[Tuple[str, str, str]]":
        rows = self._fetch_all("SELECT peer_key, receive_id, receive_id_type FROM feishu_peers")
        output = []
        for row in rows:
            output.append((str(row["peer_key"]), str(row["receive_id"]), str(row["receive_id_type"])))
        return output

    def try_mark_broadcast_sent(self, session_id: str, part_id: str, peer_key: str) -> bool:
        try:
            self._execute_write(
                "INSERT INTO broadcast_dedup(session_id, part_id, peer_key, created_at) VALUES(?, ?, ?, ?)",
                (session_id, part_id, peer_key, utc_now()),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def was_recent_request(
        self,
        peer_key: str,
        session_id: str,
        request_text: str,
        within_seconds: int = 120,
    ) -> bool:
        target = request_text.strip()
        if not target:
            return False

        rows = self._fetch_all(
            """
            SELECT request_text, created_at
            FROM relay_rounds
            WHERE peer_key = ? AND session_id = ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (peer_key, session_id),
        )

        now = datetime.now(timezone.utc)
        for row in rows:
            if str(row["request_text"]).strip() != target:
                continue
            try:
                created_at = datetime.fromisoformat(str(row["created_at"]))
            except ValueError:
                continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if (now - created_at).total_seconds() <= max(1, within_seconds):
                return True
        return False


    def was_recent_response(
        self,
        peer_key: str,
        session_id: str,
        response_text: str,
        within_seconds: int = 120,
    ) -> bool:
        target = response_text.strip()
        if not target:
            return False

        rows = self._fetch_all(
            """
            SELECT response_text, created_at
            FROM relay_rounds
            WHERE peer_key = ? AND session_id = ?
            ORDER BY id DESC
            LIMIT 20
            """,
            (peer_key, session_id),
        )

        now = datetime.now(timezone.utc)
        for row in rows:
            if str(row["response_text"]).strip() != target:
                continue
            try:
                created_at = datetime.fromisoformat(str(row["created_at"]))
            except ValueError:
                continue
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if (now - created_at).total_seconds() <= max(1, within_seconds):
                return True
        return False
