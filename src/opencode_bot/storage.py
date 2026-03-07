import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, db_path: str):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
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
        self._conn.commit()

    def try_mark_processed(self, message_id: str, peer_key: str) -> bool:
        try:
            self._conn.execute(
                "INSERT INTO processed_messages(message_id, peer_key, created_at) VALUES(?, ?, ?)",
                (message_id, peer_key, utc_now()),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def bind_session(self, peer_key: str, session_id: str) -> None:
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
        row = self._conn.execute(
            "SELECT session_id FROM feishu_session_bindings WHERE peer_key = ?",
            (peer_key,),
        ).fetchone()
        if row is None:
            return None
        return str(row["session_id"])

    def save_round(
        self,
        message_id: str,
        peer_key: str,
        session_id: str,
        request_text: str,
        response_text: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO relay_rounds(message_id, peer_key, session_id, request_text, response_text, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (message_id, peer_key, session_id, request_text, response_text, utc_now()),
        )
        self._conn.commit()
