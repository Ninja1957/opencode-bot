import asyncio
import sqlite3
import urllib.error
from typing import Any, cast

from opencode_bot.config import Settings
from opencode_bot.feishu_client import FeishuSendError
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


class FakeBadRequestFeishuClient(FakeFeishuClient):
    async def send_text(self, receive_id: str, receive_id_type: str, text: str):
        _ = (receive_id, receive_id_type, text)
        raise urllib.error.HTTPError(
            url="https://open.feishu.cn/open-apis/im/v1/messages",
            code=400,
            msg="Bad Request",
            hdrs=cast(Any, None),
            fp=None,
        )


class FakeInvalidOpenIdFeishuClient(FakeFeishuClient):
    async def send_text(self, receive_id: str, receive_id_type: str, text: str):
        _ = (receive_id, receive_id_type, text)
        raise FeishuSendError(
            status_code=400,
            error_code=99992351,
            error_message="not a valid open_id",
            receive_id="ou_test",
            receive_id_type="open_id",
            msg_type="interactive",
            detail='{"code":99992351}',
        )


class FakeTimeoutFeishuClient(FakeFeishuClient):
    async def send_text(self, receive_id: str, receive_id_type: str, text: str):
        _ = (receive_id, receive_id_type, text)
        raise FeishuSendError(
            status_code=0,
            error_code=None,
            error_message="timed out",
            receive_id="ou_xxx",
            receive_id_type="open_id",
            msg_type="text",
            detail="timed out",
        )


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
        opencode_fast_ack_s=3,
        opencode_session_title_refresh_s=7200,
        opencode_session_title_max_len=18,
        opencode_title_agent_enabled=1,
        opencode_title_agent_session_id="ses_title_agent_00001",
        opencode_title_agent_timeout_s=20,
        opencode_intent_agent_enabled=1,
        opencode_intent_agent_session_id="ses_intent_agent_00001",
        opencode_intent_agent_timeout_s=20,
        opencode_send_files_enabled=1,
        opencode_file_allowed_ext="png,jpg,jpeg,gif,pdf,zip,txt,log",
        opencode_file_roots="/data,/home",
        opencode_file_max_mb=20,
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
        opencode_client=cast(Any, FakeOpenCodeClient(session_id=session_id)),
        feishu_client=cast(Any, feishu),
        storage=storage,
    )
    asyncio.run(monitor._poll_once())

    assert len(feishu.prompts) == 1
    prompt = feishu.prompts[0]
    assert prompt[0] == "oc_x"
    assert prompt[1] == "chat_id"
    assert prompt[2] == session_id
    assert "hello from watched session" in prompt[3]


def _prepare_opencode_db(db_path, session_id: str, role: str, text: str):
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")
    conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT)")
    message_data = '{"role":"%s"}' % role
    part_data = '{"type":"text","text":"%s"}' % text
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


def _watch_settings(tmp_path, db_path: str) -> Settings:
    return Settings(
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
        opencode_db_path=db_path,
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
        opencode_intent_agent_enabled=1,
        opencode_intent_agent_session_id="ses_intent_agent_00001",
        opencode_intent_agent_timeout_s=20,
        opencode_send_files_enabled=1,
        opencode_file_allowed_ext="png,jpg,jpeg,gif,pdf,zip,txt,log",
        opencode_file_roots="/data,/home",
        opencode_file_max_mb=20,
        opencode_watch_enabled=1,
        opencode_watch_interval_s=1,
        opencode_watch_include_assistant=1,
        opencode_watch_include_user=1,
        feishu_notify_receive_id="",
        feishu_notify_receive_id_type="chat_id",
    )


def test_session_monitor_skips_recent_user_echo_for_bound_peer(tmp_path):
    session_id = "ses_test"
    user_text = "please check this"
    db_path = tmp_path / "opencode.db"
    _prepare_opencode_db(db_path, session_id=session_id, role="user", text=user_text)

    settings = _watch_settings(tmp_path, str(db_path))
    feishu = FakeFeishuClient()
    storage = Storage(str(tmp_path / "bot.db"))
    peer_key = "open_id:ou_1"
    storage.upsert_peer(peer_key=peer_key, receive_id="ou_1", receive_id_type="open_id")
    storage.bind_session(peer_key=peer_key, session_id=session_id)
    storage.save_round(
        message_id="msg_in_1",
        peer_key=peer_key,
        session_id=session_id,
        request_text=user_text,
        response_text="ok",
    )

    monitor = SessionMonitor(
        settings=settings,
        opencode_client=cast(Any, FakeOpenCodeClient(session_id=session_id)),
        feishu_client=cast(Any, feishu),
        storage=storage,
    )
    asyncio.run(monitor._poll_once())

    assert feishu.sent == []
    assert feishu.prompts == []


def test_session_monitor_skips_user_event_for_bound_peer(tmp_path):
    session_id = "ses_test"
    db_path = tmp_path / "opencode.db"
    _prepare_opencode_db(db_path, session_id=session_id, role="user", text="from terminal")

    settings = _watch_settings(tmp_path, str(db_path))
    feishu = FakeFeishuClient()
    storage = Storage(str(tmp_path / "bot.db"))
    peer_key = "open_id:ou_1"
    storage.upsert_peer(peer_key=peer_key, receive_id="ou_1", receive_id_type="open_id")
    storage.bind_session(peer_key=peer_key, session_id=session_id)

    monitor = SessionMonitor(
        settings=settings,
        opencode_client=cast(Any, FakeOpenCodeClient(session_id=session_id)),
        feishu_client=cast(Any, feishu),
        storage=storage,
    )
    asyncio.run(monitor._poll_once())

    assert feishu.sent == []
    assert feishu.prompts == []


def test_session_monitor_suppresses_peer_after_http_400(tmp_path):
    session_id = "ses_test"
    db_path = tmp_path / "opencode.db"
    _prepare_opencode_db(db_path, session_id=session_id, role="assistant", text="first")

    settings = _watch_settings(tmp_path, str(db_path))
    feishu = FakeBadRequestFeishuClient()
    storage = Storage(str(tmp_path / "bot.db"))
    peer_key = "user:ou_test"
    storage.upsert_peer(peer_key=peer_key, receive_id="ou_test", receive_id_type="open_id")
    storage.bind_session(peer_key=peer_key, session_id=session_id)

    monitor = SessionMonitor(
        settings=settings,
        opencode_client=cast(Any, FakeOpenCodeClient(session_id=session_id)),
        feishu_client=cast(Any, feishu),
        storage=storage,
    )

    asyncio.run(monitor._send_event("part_1", session_id, "assistant", "first", "pts/1"))
    suppress_until_1 = monitor._peer_suppress_until.get(peer_key, 0.0)
    assert suppress_until_1 > 0

    asyncio.run(monitor._send_event("part_2", session_id, "assistant", "second", "pts/1"))
    suppress_until_2 = monitor._peer_suppress_until.get(peer_key, 0.0)
    assert suppress_until_2 == suppress_until_1


def test_session_monitor_removes_peer_when_open_id_invalid(tmp_path):
    session_id = "ses_test"
    db_path = tmp_path / "opencode.db"
    _prepare_opencode_db(db_path, session_id=session_id, role="assistant", text="first")

    settings = _watch_settings(tmp_path, str(db_path))
    feishu = FakeInvalidOpenIdFeishuClient()
    storage = Storage(str(tmp_path / "bot.db"))
    peer_key = "user:ou_test"
    storage.upsert_peer(peer_key=peer_key, receive_id="ou_test", receive_id_type="open_id")
    storage.bind_session(peer_key=peer_key, session_id=session_id)

    monitor = SessionMonitor(
        settings=settings,
        opencode_client=cast(Any, FakeOpenCodeClient(session_id=session_id)),
        feishu_client=cast(Any, feishu),
        storage=storage,
    )

    asyncio.run(monitor._send_event("part_1", session_id, "assistant", "first", "pts/1"))
    assert storage.get_bound_session(peer_key) == session_id
    peers = [x[0] for x in storage.list_peers()]
    assert peer_key not in peers


def test_session_monitor_suppresses_peer_for_timeout_error(tmp_path):
    session_id = "ses_test"
    db_path = tmp_path / "opencode.db"
    _prepare_opencode_db(db_path, session_id=session_id, role="assistant", text="first")

    settings = _watch_settings(tmp_path, str(db_path))
    feishu = FakeTimeoutFeishuClient()
    storage = Storage(str(tmp_path / "bot.db"))
    peer_key = "open_id:ou_1"
    storage.upsert_peer(peer_key=peer_key, receive_id="ou_1", receive_id_type="open_id")
    storage.bind_session(peer_key=peer_key, session_id=session_id)

    monitor = SessionMonitor(
        settings=settings,
        opencode_client=cast(Any, FakeOpenCodeClient(session_id=session_id)),
        feishu_client=cast(Any, feishu),
        storage=storage,
    )

    asyncio.run(monitor._send_event("part_1", session_id, "assistant", "first", "pts/1"))
    suppress_until = monitor._peer_suppress_until.get(peer_key, 0.0)
    assert suppress_until > 0


def test_session_monitor_skips_recent_assistant_echo_for_bound_peer(tmp_path):
    session_id = "ses_test"
    text = "/data/fuzhenxin/BOT-FOR-OPENCODE/opencode-bot"
    db_path = tmp_path / "opencode.db"
    _prepare_opencode_db(db_path, session_id=session_id, role="assistant", text=text)

    settings = _watch_settings(tmp_path, str(db_path))
    feishu = FakeFeishuClient()
    storage = Storage(str(tmp_path / "bot.db"))
    peer_key = "open_id:ou_1"
    storage.upsert_peer(peer_key=peer_key, receive_id="ou_1", receive_id_type="open_id")
    storage.bind_session(peer_key=peer_key, session_id=session_id)
    storage.save_round(
        message_id="msg_in_1",
        peer_key=peer_key,
        session_id=session_id,
        request_text="pwd",
        response_text=text,
    )

    monitor = SessionMonitor(
        settings=settings,
        opencode_client=cast(Any, FakeOpenCodeClient(session_id=session_id)),
        feishu_client=cast(Any, feishu),
        storage=storage,
    )
    asyncio.run(monitor._poll_once())

    assert feishu.sent == []
    assert feishu.prompts == []
