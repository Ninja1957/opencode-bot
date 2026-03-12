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
    assert sessions[0].display_name.startswith("飞书与opencode多")


def test_refresh_parses_double_dash_session_arg(monkeypatch, tmp_path):
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO session (id, title) VALUES (?, ?)", ("ses_c", "C title"))
    conn.commit()
    conn.close()

    ps_text = "PID UID TT ARGS\n100 1000 pts/3 opencode run --session ses_c --format default hello\n"

    def fake_run(cmd, capture_output, text, check):
        _ = (cmd, capture_output, text, check)
        return FakeCompletedProcess(ps_text, 0)

    monkeypatch.setattr("opencode_bot.session_registry.os.getuid", lambda: 1000)
    monkeypatch.setattr("opencode_bot.session_registry.subprocess.run", fake_run)

    registry = SessionRegistry(str(db_path))
    sessions = registry.refresh()
    assert len(sessions) == 1
    assert sessions[0].session_id == "ses_c"
    assert sessions[0].status == "online"


def test_refresh_prefers_interactive_session_process_over_run_process(monkeypatch, tmp_path):
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO session (id, title) VALUES (?, ?)", ("ses_d", "D title"))
    conn.commit()
    conn.close()

    ps_text = (
        "PID UID TT ARGS\n"
        "220 1000 pts/9 opencode run --session ses_d --format default hello\n"
        "110 1000 pts/3 opencode -s ses_d\n"
    )

    def fake_run(cmd, capture_output, text, check):
        _ = (cmd, capture_output, text, check)
        return FakeCompletedProcess(ps_text, 0)

    monkeypatch.setattr("opencode_bot.session_registry.os.getuid", lambda: 1000)
    monkeypatch.setattr("opencode_bot.session_registry.subprocess.run", fake_run)

    registry = SessionRegistry(str(db_path))
    sessions = registry.refresh()
    assert len(sessions) == 1
    assert sessions[0].session_id == "ses_d"
    assert sessions[0].pid == 110


def test_refresh_reads_session_directory_when_column_exists(monkeypatch, tmp_path):
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT)")
    conn.execute(
        "INSERT INTO session (id, title, directory) VALUES (?, ?, ?)",
        ("ses_e", "E title", "/tmp/e-workdir"),
    )
    conn.commit()
    conn.close()

    ps_text = "PID UID TT ARGS\n111 1000 pts/7 opencode -s ses_e\n"

    def fake_run(cmd, capture_output, text, check):
        _ = (cmd, capture_output, text, check)
        return FakeCompletedProcess(ps_text, 0)

    monkeypatch.setattr("opencode_bot.session_registry.os.getuid", lambda: 1000)
    monkeypatch.setattr("opencode_bot.session_registry.subprocess.run", fake_run)

    registry = SessionRegistry(str(db_path))
    sessions = registry.refresh()
    assert len(sessions) == 1
    assert sessions[0].session_id == "ses_e"
    assert sessions[0].directory == "/tmp/e-workdir"


def test_refresh_marks_workdir_unavailable_when_dir_and_proc_cwd_invalid(monkeypatch, tmp_path):
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT)")
    conn.execute(
        "INSERT INTO session (id, title, directory) VALUES (?, ?, ?)",
        ("ses_f", "F title", "/path/not-exists-xyz"),
    )
    conn.commit()
    conn.close()

    ps_text = "PID UID TT ARGS\n111 1000 pts/7 opencode -s ses_f\n"

    def fake_run(cmd, capture_output, text, check):
        _ = (cmd, capture_output, text, check)
        return FakeCompletedProcess(ps_text, 0)

    monkeypatch.setattr("opencode_bot.session_registry.os.getuid", lambda: 1000)
    monkeypatch.setattr("opencode_bot.session_registry.subprocess.run", fake_run)
    monkeypatch.setattr("opencode_bot.session_registry.os.readlink", lambda p: "/missing/proc/cwd")

    registry = SessionRegistry(str(db_path))
    sessions = registry.refresh()
    assert len(sessions) == 1
    assert sessions[0].session_id == "ses_f"
    assert sessions[0].workdir_available is False


def test_title_max_len_config_is_applied(monkeypatch, tmp_path):
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO session (id, title) VALUES (?, ?)", ("ses_g", "A very very long title"))
    conn.commit()
    conn.close()

    ps_text = "PID UID TT ARGS\n111 1000 pts/7 opencode -s ses_g\n"

    def fake_run(cmd, capture_output, text, check):
        _ = (cmd, capture_output, text, check)
        return FakeCompletedProcess(ps_text, 0)

    monkeypatch.setattr("opencode_bot.session_registry.os.getuid", lambda: 1000)
    monkeypatch.setattr("opencode_bot.session_registry.subprocess.run", fake_run)

    registry = SessionRegistry(str(db_path), title_max_len=10)
    sessions = registry.refresh()
    assert len(sessions) == 1
    assert sessions[0].display_name == "A very ver..."


def test_title_agent_uses_recent_logs_for_summary(monkeypatch, tmp_path):
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")
    conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")
    conn.execute("INSERT INTO session (id, title) VALUES (?, ?)", ("ses_h", "New session"))
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
        ("msg_h1", "ses_h", 1, 1, '{"role":"user"}'),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        ("prt_h1", "msg_h1", "ses_h", 1, 1, '{"type":"text","text":"检查评测脚本是否可清理"}'),
    )
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
        ("msg_h2", "ses_h", 2, 2, '{"role":"assistant"}'),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        ("prt_h2", "msg_h2", "ses_h", 2, 2, '{"type":"text","text":"清理后需要保留关键日志"}'),
    )
    conn.commit()
    conn.close()

    ps_text = "PID UID TT ARGS\n111 1000 pts/7 opencode -s ses_h\n"

    def fake_ps(cmd, capture_output, text, check):
        _ = (cmd, capture_output, text, check)
        return FakeCompletedProcess(ps_text, 0)

    captured = {"cmd": None}

    def fake_run(cmd, capture_output, text, check=False, timeout=None):
        _ = (capture_output, text, check, timeout)
        if cmd[:2] == ["ps", "-eo"]:
            return fake_ps(cmd, capture_output, text, check)
        captured["cmd"] = cmd
        return FakeCompletedProcess("清理评测脚本", 0)

    monkeypatch.setattr("opencode_bot.session_registry.os.getuid", lambda: 1000)
    monkeypatch.setattr("opencode_bot.session_registry.subprocess.run", fake_run)

    registry = SessionRegistry(
        str(db_path),
        title_agent_enabled=True,
        title_agent_session_id="ses_title_agent_x",
        title_agent_timeout_s=10,
    )
    sessions = registry.refresh()
    assert len(sessions) == 1
    assert sessions[0].display_name == "清理评测脚本"

    cmd = captured["cmd"]
    assert cmd is not None
    assert cmd[0] in {"opencode", "/bin/opencode", "./opencode"} or "opencode" in cmd[0]
    assert "--session" in cmd
    idx = cmd.index("--session")
    assert cmd[idx + 1] == "ses_title_agent_x"
    prompt = cmd[-1]
    assert "检查评测脚本是否可清理" in prompt
    assert "清理后需要保留关键日志" in prompt


def test_title_builder_filters_agent_noise_texts(monkeypatch, tmp_path):
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")
    conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")
    conn.execute("INSERT INTO session (id, title) VALUES (?, ?)", ("ses_i", "New session"))
    samples = [
        ("m1", "p1", '{"type":"text","text":"请仅回复OK"}'),
        ("m2", "p2", '{"type":"text","text":"OK"}'),
        ("m3", "p3", '{"type":"text","text":"你是会话标题总结器。根据以下最近日志"}'),
        ("m4", "p4", '{"type":"text","text":"评测脚本清理与结果核验"}'),
    ]
    ts = 1
    for mid, pid, pdata in samples:
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
            (mid, "ses_i", ts, ts, '{"role":"user"}'),
        )
        conn.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, mid, "ses_i", ts, ts, pdata),
        )
        ts += 1
    conn.commit()

    captured = {"prompt": ""}

    def fake_run(cmd, capture_output, text, check=False, timeout=None):
        _ = (capture_output, text, check, timeout)
        if cmd[:2] == ["ps", "-eo"]:
            return FakeCompletedProcess("PID UID TT ARGS\n111 1000 pts/7 opencode -s ses_i\n", 0)
        captured["prompt"] = cmd[-1]
        return FakeCompletedProcess("评测脚本清理", 0)

    monkeypatch.setattr("opencode_bot.session_registry.os.getuid", lambda: 1000)
    monkeypatch.setattr("opencode_bot.session_registry.subprocess.run", fake_run)
    monkeypatch.setattr("opencode_bot.session_registry.os.path.exists", lambda p: False)

    registry = SessionRegistry(
        str(db_path),
        title_agent_enabled=True,
        title_agent_session_id="ses_title_agent_x",
        title_agent_timeout_s=10,
    )
    sessions = registry.refresh()
    conn.close()

    assert len(sessions) == 1
    assert sessions[0].display_name == "评测脚本清理"
    prompt = captured["prompt"]
    assert "请仅回复OK" not in prompt
    assert "\n- OK" not in prompt
    assert "你是会话标题总结器" in prompt
    assert "评测脚本清理与结果核验" in prompt


def test_title_builder_filters_mode_templates(monkeypatch, tmp_path):
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")
    conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")
    conn.execute("INSERT INTO session (id, title) VALUES (?, ?)", ("ses_j", "New session"))

    samples = [
        '[search-mode] MAXIMIZE SEARCH EFFORT. Launch multiple background agents IN PARALLEL',
        '[analyze-mode] ANALYSIS MODE. Gather context before diving deep',
        'CONTEXT GATHERING (parallel):',
        '<system-reminder> [BACKGROUND TASK COMPLETED]',
        '检查并清理完成，输出目录已稳定',
    ]
    ts = 1
    for text in samples:
        mid = f"m{ts}"
        pid = f"p{ts}"
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
            (mid, "ses_j", ts, ts, '{"role":"user"}'),
        )
        conn.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, mid, "ses_j", ts, ts, '{"type":"text","text":"' + text + '"}'),
        )
        ts += 1
    conn.commit()

    captured = {"prompt": ""}

    def fake_run(cmd, capture_output, text, check=False, timeout=None):
        _ = (capture_output, text, check, timeout)
        if cmd[:2] == ["ps", "-eo"]:
            return FakeCompletedProcess("PID UID TT ARGS\n111 1000 pts/7 opencode -s ses_j\n", 0)
        captured["prompt"] = cmd[-1]
        return FakeCompletedProcess("目录清理完成", 0)

    monkeypatch.setattr("opencode_bot.session_registry.os.getuid", lambda: 1000)
    monkeypatch.setattr("opencode_bot.session_registry.subprocess.run", fake_run)
    monkeypatch.setattr("opencode_bot.session_registry.os.path.exists", lambda p: False)

    registry = SessionRegistry(
        str(db_path),
        title_agent_enabled=True,
        title_agent_session_id="ses_title_agent_x",
        title_agent_timeout_s=10,
    )
    sessions = registry.refresh()
    conn.close()

    assert len(sessions) == 1
    prompt = captured["prompt"]
    assert "[search-mode]" not in prompt.lower()
    assert "[analyze-mode]" not in prompt.lower()
    assert "context gathering" not in prompt.lower()
    assert "system-reminder" not in prompt.lower()
    assert "检查并清理完成，输出目录已稳定" in prompt
