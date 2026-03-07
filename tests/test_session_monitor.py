import asyncio
import sqlite3

from opencode_bot.config import Settings
from opencode_bot.models import OnlineSession
from opencode_bot.session_monitor import SessionMonitor
from opencode_bot.storage import Storage


class FakeOpenCodeClient:
    def __init__(self, session_id: str):
        self._session_id = session_id

    async def list_online_sessions(self):
        return [
            OnlineSession(
                session_id=self._session_id,
                display_name="Demo",
                status="online",
                pid=1,
                tty="pts/1",
                last_seen_ts=1,
            )
        ]

    def list_cached_sessions(self, include_offline: bool):
        _ = include_offline
        return [
            OnlineSession(
                session_id=self._session_id,
                display_name="Demo",
                status="online",
                pid=1,
                tty="pts/1",
                last_seen_ts=1,
            )
        ]


class FakeFeishuClient:
    def __init__(self):
        self.sent = []
        self.prompts = []

    async def send_text(self, receive_id: str, receive_id_type: str, text: str):
        self.sent.append((receive_id, receive_id_type, text))

    async def send_session_bind_prompt(
        self,
        receive_id: str,
        receive_id_type: str,
        session_id: str,
        preview: str,
    ):
        self.prompts.append((receive_id, receive_id_type, session_id, preview))


def test_session_monitor_pushes_new_text_part(tmp_path):
    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")
    conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")

    session_id = "ses_test"
    message_data = '{"role":"assistant"}'
    part_data = '{"type":"text","text":"hello from watched session"}'
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
        ("msg_1", session_id, 100, 100, message_data),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
        ("prt_1", "msg_1", session_id, 101, 101, part_data),
    )
    conn.commit()
    conn.close()

    settings = Settings(
        host="127.0.0.1",
        port=8080,
        storage_path=str(tmp_path / "bot.db"),
        feishu_app_id="",
        feishu_app_secret="",
        feishu_verify_token="",
        feishu_event_mode="http",
        feishu_encrypt_key="",
        opencode_base_url="http://127.0.0.1:4096",
        opencode_transport="cli",
        opencode_bin="opencode",
        opencode_db_path=str(db_path),
        opencode_list_sessions_path="/api/sessions/list",
        opencode_list_sessions_path_alt="/api/claw/sessions/list",
        opencode_send_message_path="/api/sessions/send",
        opencode_send_message_path_alt="/api/claw/sessions/send",
        opencode_api_key="",
        opencode_request_timeout_s=30,
        opencode_watch_enabled=1,
        opencode_watch_interval_s=1,
        opencode_watch_include_assistant=1,
        opencode_watch_include_user=0,
        feishu_notify_receive_id="oc_x",
        feishu_notify_receive_id_type="chat_id",
    )

    feishu = FakeFeishuClient()
    storage = Storage(str(tmp_path / "bot.db"))
    monitor = SessionMonitor(
        settings=settings,
        opencode_client=FakeOpenCodeClient(session_id=session_id),
        feishu_client=feishu,
        storage=storage,
    )
    asyncio.run(monitor._poll_once())

    assert len(feishu.prompts) == 1
    prompt = feishu.prompts[0]
    assert prompt[0] == "oc_x"
    assert prompt[1] == "chat_id"
    assert prompt[2] == session_id
    assert "hello from watched session" in prompt[3]
