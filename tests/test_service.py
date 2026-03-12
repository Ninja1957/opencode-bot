import asyncio
from typing import Any, cast

from opencode_bot.models import FeishuInbound, OnlineSession
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
    service = RelayService(storage=storage, opencode_client=cast(Any, client))

    first = asyncio.run(service.handle_inbound(make_inbound("m1", "/bind s-1")))
    assert "已绑定" in str(first)

    second = asyncio.run(service.handle_inbound(make_inbound("m2", "hello world")))
    assert second == "reply:s-1:hello world"
    assert client.calls == [("s-1", "hello world")]


def test_dedup_same_message(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=cast(Any, client))

    response_1 = asyncio.run(service.handle_inbound(make_inbound("same", "/sessions")))
    response_2 = asyncio.run(service.handle_inbound(make_inbound("same", "/sessions")))

    assert isinstance(response_1, str)
    assert response_2 is None


def test_send_one_shot_target_command(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=cast(Any, client))

    response = asyncio.run(service.handle_inbound(make_inbound("m3", "/send s-2 run health check")))
    assert response == "reply:s-2:run health check"
    assert client.calls == [("s-2", "run health check")]


def test_send_target_by_session_prefix(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=cast(Any, client))

    response = asyncio.run(service.handle_inbound(make_inbound("m4", "@s-1 do this")))
    assert response == "reply:s-1:do this"
    assert client.calls == [("s-1", "do this")]


def test_plain_two_word_message_is_not_targeted(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=cast(Any, client))

    response = asyncio.run(service.handle_inbound(make_inbound("m4a", "hello world")))
    assert response == "当前未绑定 session。请先发送 /session_list (/sl) 查看，再 /bind <序号> 或 /bind <session_id> 绑定。"


def test_unbind_after_bind(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=cast(Any, client))

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
    service = RelayService(storage=storage, opencode_client=cast(Any, client))

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
    service = RelayService(storage=storage, opencode_client=cast(Any, client))

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
    service = RelayService(storage=storage, opencode_client=cast(Any, client))

    response = asyncio.run(service.handle_inbound(make_inbound("m11", "/bind s-1")))
    assert "未找到在线 session" in str(response)


def test_fast_ack_returns_waiting_message_for_slow_session(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeSlowOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=cast(Any, client), fast_ack_s=0.01)

    bind_res = asyncio.run(service.handle_inbound(make_inbound("m9", "/bind s-1")))
    assert "已绑定" in str(bind_res)

    response = asyncio.run(service.handle_inbound(make_inbound("m10", "long work")))
    assert "正在等待 opencode 处理" in str(response)
