import asyncio
import json

from opencode_bot.config import Settings
from opencode_bot.feishu_client import FeishuClient
from opencode_bot.models import OnlineSession


def _make_settings() -> Settings:
    return Settings(
        host="0.0.0.0",
        port=8080,
        storage_path="./data/opencode_bot.db",
        feishu_app_id="cli_x",
        feishu_app_secret="sec_x",
        feishu_verify_token="",
        feishu_event_mode="http",
        feishu_encrypt_key="",
        opencode_base_url="http://127.0.0.1:4096",
        opencode_transport="cli",
        opencode_bin="opencode",
        opencode_db_path="./data/opencode.db",
        opencode_list_sessions_path="/api/sessions/list",
        opencode_list_sessions_path_alt="/api/claw/sessions/list",
        opencode_send_message_path="/api/sessions/send",
        opencode_send_message_path_alt="/api/claw/sessions/send",
        opencode_api_key="",
        opencode_request_timeout_s=30.0,
        opencode_watch_enabled=0,
        opencode_watch_interval_s=5.0,
        opencode_watch_include_assistant=1,
        opencode_watch_include_user=1,
        feishu_notify_receive_id="",
        feishu_notify_receive_id_type="chat_id",
    )


def test_send_session_picker_sends_all_pages(monkeypatch):
    client = FeishuClient(_make_settings())
    sent = []

    async def fake_send_message(receive_id_type, payload):
        sent.append((receive_id_type, payload))

    monkeypatch.setattr(client, "_send_message", fake_send_message)

    sessions = []
    for idx in range(25):
        sessions.append(
            OnlineSession(
                session_id=f"ses_{idx}",
                display_name=f"Session {idx}",
                status="online",
            )
        )

    asyncio.run(
        client.send_session_picker(
            receive_id="oc_x",
            receive_id_type="chat_id",
            sessions=sessions,
        )
    )

    assert len(sent) == 2
    first_card = json.loads(sent[0][1]["content"])
    second_card = json.loads(sent[1][1]["content"])
    assert "(1/2)" in first_card["header"]["title"]["content"]
    assert "(2/2)" in second_card["header"]["title"]["content"]
    assert len(first_card["elements"]) == 20
    assert len(second_card["elements"]) == 5
