import asyncio
from typing import Any, cast

from opencode_bot.models import FeishuInbound, OnlineSession
from opencode_bot.config import Settings
from opencode_bot.service import RelayService
from opencode_bot.storage import Storage


class FakeOpenCodeClient:
    def __init__(self):
        self.sessions = [
            OnlineSession(session_id="s-1", display_name="Session 1", status="online"),
            OnlineSession(session_id="s-2", display_name="Session 2", status="online"),
        ]
        self.calls: list[tuple[str, str]] = []
        self.refresh_called = 0

    async def list_online_sessions(self):
        return self.sessions

    async def send_to_session(self, session_id: str, text: str) -> str:
        self.calls.append((session_id, text))
        return f"reply:{session_id}:{text}"

    async def refresh_session_titles_now(self):
        self.refresh_called += 1


class FakeFeishuClient:
    def __init__(self) -> None:
        self.sent_files: list[str] = []
        self.sent_images: list[str] = []

    async def send_file(self, receive_id: str, receive_id_type: str, file_path: str) -> None:
        _ = (receive_id, receive_id_type)
        self.sent_files.append(file_path)

    async def send_image(self, receive_id: str, receive_id_type: str, image_path: str) -> None:
        _ = (receive_id, receive_id_type)
        self.sent_images.append(image_path)


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
        opencode_bin="opencode",
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
        opencode_intent_agent_enabled=1,
        opencode_intent_agent_session_id="ses_intent_agent_00001",
        opencode_intent_agent_timeout_s=20,
        opencode_send_files_enabled=1,
        opencode_file_allowed_ext="png,jpg,jpeg,gif,pdf,zip,txt,log",
        opencode_file_roots="/data,/home",
        opencode_file_max_mb=20,
        opencode_watch_enabled=0,
        opencode_watch_interval_s=5,
        opencode_watch_include_assistant=1,
        opencode_watch_include_user=1,
        feishu_notify_receive_id="",
        feishu_notify_receive_id_type="chat_id",
    )


class FakeSlowOpenCodeClient(FakeOpenCodeClient):
    async def send_to_session(self, session_id: str, text: str) -> str:
        await asyncio.sleep(0.8)
        self.calls.append((session_id, text))
        return f"reply:{session_id}:{text}"


def make_inbound(message_id: str, text: str) -> FeishuInbound:
    return FeishuInbound(
        message_id=message_id,
        chat_id="oc_x",
        open_id="ou_x",
        chat_type="group",
        text=text,
    )


def test_bind_and_relay_flow(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=cast(Any, client), settings=_settings())

    first = asyncio.run(service.handle_inbound(make_inbound("m1", "/bind s-1")))
    assert "已绑定" in str(first)

    second = asyncio.run(service.handle_inbound(make_inbound("m2", "hello world")))
    assert second == "reply:s-1:hello world"
    assert client.calls == [("s-1", "hello world")]


def test_dedup_same_message(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=cast(Any, client), settings=_settings())

    response_1 = asyncio.run(service.handle_inbound(make_inbound("same", "/sessions")))
    response_2 = asyncio.run(service.handle_inbound(make_inbound("same", "/sessions")))

    assert isinstance(response_1, str)
    assert response_2 is None


def test_send_one_shot_target_command(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=cast(Any, client), settings=_settings())

    response = asyncio.run(service.handle_inbound(make_inbound("m3", "/send s-2 run health check")))
    assert response == "reply:s-2:run health check"
    assert client.calls == [("s-2", "run health check")]


def test_send_target_by_session_prefix(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=cast(Any, client), settings=_settings())

    response = asyncio.run(service.handle_inbound(make_inbound("m4", "@s-1 do this")))
    assert response == "reply:s-1:do this"
    assert client.calls == [("s-1", "do this")]


def test_plain_two_word_message_is_not_targeted(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=cast(Any, client), settings=_settings())

    response = asyncio.run(service.handle_inbound(make_inbound("m4a", "hello world")))
    assert response == "当前未绑定 session。请先发送 /session_list (/sl) 查看，再 /bind <序号> 或 /bind <session_id> 绑定。"


def test_unbind_after_bind(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=cast(Any, client), settings=_settings())

    bind_res = asyncio.run(service.handle_inbound(make_inbound("m5", "/bind s-1")))
    assert "已绑定" in str(bind_res)

    unbind_res = asyncio.run(service.handle_inbound(make_inbound("m6", "/unbind")))
    assert unbind_res == "已解绑当前会话。"

    current_res = asyncio.run(service.handle_inbound(make_inbound("m7", "/current")))
    assert current_res == "当前未绑定 session。"


def test_session_list_marks_workdir_unavailable(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    client.sessions = [
        OnlineSession(session_id="s-1", display_name="Session 1", status="online", workdir_available=True),
        OnlineSession(session_id="s-2", display_name="Session 2", status="online", workdir_available=False),
    ]
    service = RelayService(storage=storage, opencode_client=cast(Any, client), settings=_settings())

    response = asyncio.run(service.handle_inbound(make_inbound("m8", "/sessions")))
    assert "workdir=missing" not in str(response)
    assert "Session 2" not in str(response)


def test_session_list_shows_short_session_id(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    client.sessions = [
        OnlineSession(
            session_id="ses_12345abcdefghijklmnopqrstuvwxyz",
            display_name="Session Long",
            status="online",
            workdir_available=True,
        )
    ]
    service = RelayService(storage=storage, opencode_client=cast(Any, client), settings=_settings())

    response = asyncio.run(service.handle_inbound(make_inbound("m8b", "/sessions")))
    assert "ses_12345..." in str(response)
    assert "ses_12345abcdefghijklmnopqrstuvwxyz" not in str(response)
    assert client.refresh_called == 1


def test_bind_unavailable_session_by_id_is_rejected(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    client.sessions = [
        OnlineSession(session_id="s-1", display_name="Session 1", status="online", workdir_available=False),
    ]
    service = RelayService(storage=storage, opencode_client=cast(Any, client), settings=_settings())

    response = asyncio.run(service.handle_inbound(make_inbound("m11", "/bind s-1")))
    assert "未找到在线 session" in str(response)


def test_fast_ack_returns_waiting_message_for_slow_session(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeSlowOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=cast(Any, client), settings=_settings(), fast_ack_s=0.01)

    bind_res = asyncio.run(service.handle_inbound(make_inbound("m9", "/bind s-1")))
    assert "已绑定" in str(bind_res)

    response = asyncio.run(service.handle_inbound(make_inbound("m10", "long work")))
    assert "正在等待 opencode 处理" in str(response)


def test_file_intent_sends_file_via_feishu(tmp_path, monkeypatch):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    feishu = FakeFeishuClient()
    settings = _settings()
    settings.opencode_file_roots = str(tmp_path)
    settings.opencode_file_allowed_ext = "txt"

    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello", encoding="utf-8")

    service = RelayService(
        storage=storage,
        opencode_client=cast(Any, client),
        settings=settings,
        feishu_client=cast(Any, feishu),
    )

    async def fake_extract(_text: str):
        return [str(file_path)]

    monkeypatch.setattr(service, "_extract_file_paths_via_intent_agent", fake_extract)

    response = asyncio.run(service.handle_inbound(make_inbound("m11", f"路径是 {file_path}")))
    assert "已发送文件" in str(response)
    assert feishu.sent_files == [str(file_path)]
