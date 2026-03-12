import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.opencode_bot.config import Settings
from src.opencode_bot.models import OnlineSession
from src.opencode_bot.opencode_client import OpenCodeClient


class FakeCompletedProcess:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _settings() -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8080,
        storage_path="./data/test.db",
        feishu_app_id="",
        feishu_app_secret="",
        feishu_verify_token="",
        feishu_event_mode="http",
        feishu_encrypt_key="",
        opencode_base_url="http://127.0.0.1:4096",
        opencode_transport="cli",
        opencode_bin="/nonexistent/opencode",
        opencode_db_path="/tmp/opencode.db",
        opencode_list_sessions_path="/api/sessions/list",
        opencode_list_sessions_path_alt="/api/claw/sessions/list",
        opencode_send_message_path="/api/sessions/send",
        opencode_send_message_path_alt="/api/claw/sessions/send",
        opencode_api_key="",
        opencode_request_timeout_s=30,
        opencode_fast_ack_s=3,
        opencode_session_title_refresh_s=7200,
        opencode_session_title_max_len=18,
        opencode_title_agent_enabled=1,
        opencode_title_agent_session_id="ses_title_agent_00001",
        opencode_title_agent_timeout_s=20,
        opencode_watch_enabled=0,
        opencode_watch_interval_s=5,
        opencode_watch_include_assistant=1,
        opencode_watch_include_user=1,
        feishu_notify_receive_id="",
        feishu_notify_receive_id_type="chat_id",
    )


def test_send_to_session_cli_uses_resolved_cwd_and_env(monkeypatch):
    client = OpenCodeClient(_settings())
    monkeypatch.setattr(
        client,
        "_find_target_session",
        lambda session_id: OnlineSession(
            session_id=session_id,
            display_name="target",
            status="online",
            pid=123,
            tty="pts/1",
            directory="/tmp/demo-workdir",
        ),
    )
    monkeypatch.setattr(
        client,
        "_resolve_cli_context_from_target",
        lambda target: ("/tmp/demo-workdir", {"VIRTUAL_ENV": "/tmp/demo-env", "CUSTOM_ENV": "1"}),
    )

    captured = {}

    def fake_run(cmd, capture_output, text, check, timeout, cwd, env):
        _ = (capture_output, text, check, timeout)
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        return FakeCompletedProcess(stdout="ok")

    monkeypatch.setattr("opencode_bot.opencode_client.subprocess.run", fake_run)

    output = client._send_to_session_by_cli("ses_target", "hello")
    assert output == "ok"
    assert captured["cmd"][0] == "opencode"
    assert captured["cwd"] == "/tmp/demo-workdir"
    assert captured["env"] is not None
    assert captured["env"]["VIRTUAL_ENV"] == "/tmp/demo-env"
    assert captured["env"]["CUSTOM_ENV"] == "1"


def test_send_to_session_cli_falls_back_when_no_context(monkeypatch):
    client = OpenCodeClient(_settings())
    monkeypatch.setattr(client, "_find_target_session", lambda session_id: None)
    monkeypatch.setattr(client, "_resolve_cli_context_from_target", lambda target: (None, None))

    captured = {}

    def fake_run(cmd, capture_output, text, check, timeout, cwd, env):
        _ = (cmd, capture_output, text, check, timeout)
        captured["cwd"] = cwd
        captured["env"] = env
        return FakeCompletedProcess(stdout="ok")

    monkeypatch.setattr("opencode_bot.opencode_client.subprocess.run", fake_run)

    output = client._send_to_session_by_cli("ses_target", "hello")
    assert "目标 session 不在线或不可解析" in output
    assert captured == {}


def test_send_to_session_cli_passes_session_directory_as_dir_flag(monkeypatch, tmp_path):
    client = OpenCodeClient(_settings())
    workdir = tmp_path / "session-dir"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        client,
        "_find_target_session",
        lambda session_id: OnlineSession(
            session_id=session_id,
            display_name="target",
            status="online",
            pid=123,
            tty="pts/1",
            directory=str(workdir),
        ),
    )
    monkeypatch.setattr(
        client,
        "_resolve_cli_context_from_target",
        lambda target: (str(workdir), {"PATH": "/usr/bin"}),
    )

    captured = {}

    def fake_run(cmd, capture_output, text, check, timeout, cwd, env):
        _ = (capture_output, text, check, timeout, cwd, env)
        captured["cmd"] = cmd
        return FakeCompletedProcess(stdout="ok")

    monkeypatch.setattr("opencode_bot.opencode_client.subprocess.run", fake_run)

    output = client._send_to_session_by_cli("ses_target", "hello")
    assert output == "ok"
    assert "--dir" in captured["cmd"]
    idx = captured["cmd"].index("--dir")
    assert captured["cmd"][idx + 1] == str(workdir)


def test_send_to_session_cli_ignores_invalid_session_directory(monkeypatch):
    client = OpenCodeClient(_settings())
    monkeypatch.setattr(
        client,
        "_find_target_session",
        lambda session_id: OnlineSession(
            session_id=session_id,
            display_name="target",
            status="online",
            pid=123,
            tty="pts/1",
            directory="/path/not-exists-xyz",
        ),
    )
    monkeypatch.setattr(
        client,
        "_resolve_cli_context_from_target",
        lambda target: (None, {"PATH": "/usr/bin"}),
    )

    called = {"value": False}

    def fake_run(cmd, capture_output, text, check, timeout, cwd, env):
        _ = (capture_output, text, check, timeout, cwd, env)
        called["value"] = True
        return FakeCompletedProcess(stdout="ok")

    monkeypatch.setattr("opencode_bot.opencode_client.subprocess.run", fake_run)

    output = client._send_to_session_by_cli("ses_target", "hello")
    assert "目标 session 工作目录不可用" in output
    assert called["value"] is False


def test_send_to_session_cli_retries_without_dir_when_change_directory_failed(monkeypatch, tmp_path):
    client = OpenCodeClient(_settings())
    workdir = tmp_path / "session-dir"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        client,
        "_find_target_session",
        lambda session_id: OnlineSession(
            session_id=session_id,
            display_name="target",
            status="online",
            pid=123,
            tty="pts/1",
            directory=str(workdir),
        ),
    )
    monkeypatch.setattr(
        client,
        "_resolve_cli_context_from_target",
        lambda target: (str(workdir), {"PATH": "/usr/bin"}),
    )

    calls = {"count": 0, "cmds": []}

    def fake_run(cmd, capture_output, text, check, timeout, cwd, env):
        _ = (capture_output, text, check, timeout, cwd, env)
        calls["count"] += 1
        calls["cmds"].append(cmd)
        if calls["count"] == 1:
            return FakeCompletedProcess(stdout="", stderr="Error: Failed to change directory to /bad", returncode=1)
        return FakeCompletedProcess(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr("opencode_bot.opencode_client.subprocess.run", fake_run)

    output = client._send_to_session_by_cli("ses_target", "hello")
    assert output == "ok"
    assert calls["count"] == 2
    assert "--dir" in calls["cmds"][0]
    assert "--dir" not in calls["cmds"][1]


def test_send_to_session_cli_blocks_execution_when_no_valid_workdir(monkeypatch):
    client = OpenCodeClient(_settings())
    monkeypatch.setattr(
        client,
        "_find_target_session",
        lambda session_id: OnlineSession(
            session_id=session_id,
            display_name="target",
            status="online",
            pid=123,
            tty="pts/1",
            directory="/path/not-exists-xyz",
        ),
    )
    monkeypatch.setattr(client, "_resolve_cli_context_from_target", lambda target: (None, {"PATH": "/usr/bin"}))

    called = {"value": False}

    def fake_run(cmd, capture_output, text, check, timeout, cwd, env):
        _ = (cmd, capture_output, text, check, timeout, cwd, env)
        called["value"] = True
        return FakeCompletedProcess(stdout="ok")

    monkeypatch.setattr("opencode_bot.opencode_client.subprocess.run", fake_run)

    output = client._send_to_session_by_cli("ses_target", "hello")
    assert "目标 session 工作目录不可用" in output
    assert called["value"] is False


def test_resolve_cli_context_uses_online_session_pid(monkeypatch):
    client = OpenCodeClient(_settings())
    monkeypatch.setattr(
        client._session_registry,
        "refresh",
        lambda: [
            OnlineSession(
                session_id="ses_target",
                display_name="target",
                status="online",
                pid=4321,
                tty="pts/1",
            )
        ],
    )
    monkeypatch.setattr(client, "_read_proc_cwd", lambda pid: "/tmp/session-cwd")
    monkeypatch.setattr(client, "_read_proc_env", lambda pid: {"CONDA_PREFIX": "/tmp/conda"})

    cwd, env = client._resolve_cli_context("ses_target")
    assert cwd == "/tmp/session-cwd"
    assert env == {"CONDA_PREFIX": "/tmp/conda"}


def test_resolve_cli_context_prefers_session_directory(monkeypatch, tmp_path):
    client = OpenCodeClient(_settings())
    workdir = tmp_path / "session-dir"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        client._session_registry,
        "refresh",
        lambda: [
            OnlineSession(
                session_id="ses_target",
                display_name="target",
                status="online",
                pid=4321,
                tty="pts/1",
                directory=str(workdir),
            )
        ],
    )
    monkeypatch.setattr(client, "_read_proc_cwd", lambda pid: "/tmp/other-cwd")
    monkeypatch.setattr(client, "_read_proc_env", lambda pid: {"CONDA_PREFIX": "/tmp/conda"})

    cwd, env = client._resolve_cli_context("ses_target")
    assert cwd == str(workdir)
    assert env == {"CONDA_PREFIX": "/tmp/conda"}
