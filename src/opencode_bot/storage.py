import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import List, Optional, Tuple


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, db_path: str):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

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
            with self._lock:
                self._conn.execute(
                    "INSERT INTO processed_messages(message_id, peer_key, created_at) VALUES(?, ?, ?)",
                    (message_id, peer_key, utc_now()),
                )
                self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def bind_session(self, peer_key: str, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO feishu_session_bindings(peer_key, session_id, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(peer_key) DO UPDATE SET
                    session_id=excluded.session_id,
                    updated_at=excluded.updated_at
                """,
                (peer_key, session_id, utc_now()),
            )
            self._conn.commit()

    def get_bound_session(self, peer_key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id FROM feishu_session_bindings WHERE peer_key = ?",
                (peer_key,),
            ).fetchone()
        if row is None:
            return None
        return str(row["session_id"])

    def unbind_session(self, peer_key: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM feishu_session_bindings WHERE peer_key = ?",
                (peer_key,),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def save_round(
        self,
        message_id: str,
        peer_key: str,
        session_id: str,
        request_text: str,
        response_text: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO relay_rounds(message_id, peer_key, session_id, request_text, response_text, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (message_id, peer_key, session_id, request_text, response_text, utc_now()),
            )
            self._conn.commit()

    def upsert_peer(self, peer_key: str, receive_id: str, receive_id_type: str) -> None:
        with self._lock:
            self._conn.execute(
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
            self._conn.commit()

    def list_peers(self) -> "List[Tuple[str, str, str]]":
        with self._lock:
            rows = self._conn.execute(
                "SELECT peer_key, receive_id, receive_id_type FROM feishu_peers"
            ).fetchall()
        output = []
        for row in rows:
            output.append((str(row["peer_key"]), str(row["receive_id"]), str(row["receive_id_type"])))
        return output

    def try_mark_broadcast_sent(self, session_id: str, part_id: str, peer_key: str) -> bool:
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO broadcast_dedup(session_id, part_id, peer_key, created_at) VALUES(?, ?, ?, ?)",
                    (session_id, part_id, peer_key, utc_now()),
                )
                self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
