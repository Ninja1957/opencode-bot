import sqlite3
import threading
from typing import Any, cast

from opencode_bot.storage import Storage


def test_storage_enables_wal_and_busy_timeout(tmp_path):
    db_path = tmp_path / "bot.db"
    storage = Storage(str(db_path))

    with storage._lock:
        mode = storage._conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = storage._conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert str(mode).lower() == "wal"
    assert int(busy_timeout) == 30000


def test_storage_duplicate_insert_rolls_back_cleanly(tmp_path):
    db_path = tmp_path / "bot.db"
    storage = Storage(str(db_path))

    assert storage.try_mark_processed("m1", "chat:1") is True
    assert storage.try_mark_processed("m1", "chat:1") is False

    assert storage.try_mark_processed("m2", "chat:1") is True


def test_execute_write_retries_when_locked():
    class FakeConn:
        def __init__(self):
            self.calls = 0
            self.commits = 0
            self.rollbacks = 0

        def execute(self, sql, params):
            _ = (sql, params)
            self.calls += 1
            if self.calls < 3:
                raise sqlite3.OperationalError("database is locked")

            class _Cur:
                rowcount = 1

            return _Cur()

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    fake = FakeConn()
    storage = cast(Any, Storage.__new__(Storage))
    storage._conn = fake
    storage._lock = threading.Lock()

    cur = storage._execute_write("INSERT INTO t(a) VALUES(?)", ("x",))
    assert cur.rowcount == 1
    assert fake.calls == 3
    assert fake.rollbacks == 2
    assert fake.commits == 1
