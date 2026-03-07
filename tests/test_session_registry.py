import sqlite3

from opencode_bot.session_registry import SessionRegistry


class FakeCompletedProcess:
    def __init__(self, stdout: str, returncode: int):
        self.stdout = stdout
        self.returncode = returncode


def test_refresh_online_and_offline(monkeypatch, tmp_path):
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO session (id, title) VALUES (?, ?)", ("ses_a", "A title"))
    conn.commit()
    conn.close()

    ps_first = "PID UID TT ARGS\n100 1000 pts/1 opencode -s ses_a\n"
    ps_second = "PID UID TT ARGS\n"

    state = {"step": 0}

    def fake_run(cmd, capture_output, text, check):
        _ = (cmd, capture_output, text, check)
        if state["step"] == 0:
            state["step"] = 1
            return FakeCompletedProcess(ps_first, 0)
        return FakeCompletedProcess(ps_second, 0)

    monkeypatch.setattr("opencode_bot.session_registry.os.getuid", lambda: 1000)
    monkeypatch.setattr("opencode_bot.session_registry.subprocess.run", fake_run)

    registry = SessionRegistry(str(db_path))
    online = registry.refresh()
    assert len(online) == 1
    assert online[0].session_id == "ses_a"
    assert online[0].status == "online"
    assert online[0].display_name == "A title"

    online_second = registry.refresh()
    assert online_second == []

    cached = registry.list_sessions(include_offline=True)
    assert len(cached) == 1
    assert cached[0].status == "offline"


def test_title_fallback_from_latest_user_text(monkeypatch, tmp_path):
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")
    conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")
    conn.execute("INSERT INTO session (id, title) VALUES (?, ?)", ("ses_b", "New session - 2026-03-07"))
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
        ("msg_b", "ses_b", 1, 1, '{"role":"user"}'),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "prt_b",
            "msg_b",
            "ses_b",
            2,
            2,
            '{"type":"text","text":"飞书与opencode多session自动管理需求讨论"}',
        ),
    )
    conn.commit()
    conn.close()

    ps_text = "PID UID TT ARGS\n100 1000 pts/9 opencode -s ses_b\n"

    def fake_run(cmd, capture_output, text, check):
        _ = (cmd, capture_output, text, check)
        return FakeCompletedProcess(ps_text, 0)

    monkeypatch.setattr("opencode_bot.session_registry.os.getuid", lambda: 1000)
    monkeypatch.setattr("opencode_bot.session_registry.subprocess.run", fake_run)

    registry = SessionRegistry(str(db_path))
    sessions = registry.refresh()
    assert len(sessions) == 1
    assert sessions[0].session_id == "ses_b"
    assert "飞书与opencode多session自动管理需求讨论" in sessions[0].display_name
