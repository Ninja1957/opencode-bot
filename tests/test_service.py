import asyncio

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

    async def list_online_sessions(self):
        return self.sessions

    async def send_to_session(self, session_id: str, text: str) -> str:
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
    service = RelayService(storage=storage, opencode_client=client)

    first = asyncio.run(service.handle_inbound(make_inbound("m1", "/bind s-1")))
    assert "已绑定" in str(first)

    second = asyncio.run(service.handle_inbound(make_inbound("m2", "hello")))
    assert second == "reply:s-1:hello"
    assert client.calls == [("s-1", "hello")]


def test_dedup_same_message(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=client)

    response_1 = asyncio.run(service.handle_inbound(make_inbound("same", "/sessions")))
    response_2 = asyncio.run(service.handle_inbound(make_inbound("same", "/sessions")))

    assert isinstance(response_1, str)
    assert response_2 is None


def test_send_one_shot_target_command(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=client)

    response = asyncio.run(service.handle_inbound(make_inbound("m3", "/send s-2 run health check")))
    assert response == "reply:s-2:run health check"
    assert client.calls == [("s-2", "run health check")]


def test_send_target_by_session_prefix(tmp_path):
    storage = Storage(str(tmp_path / "bot.db"))
    client = FakeOpenCodeClient()
    service = RelayService(storage=storage, opencode_client=client)

    response = asyncio.run(service.handle_inbound(make_inbound("m4", "@s-1 do this")))
    assert response == "reply:s-1:do this"
    assert client.calls == [("s-1", "do this")]
