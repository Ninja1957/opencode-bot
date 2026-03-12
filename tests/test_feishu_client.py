import asyncio
import io
import json
import urllib.error
from typing import Any, cast

from opencode_bot.config import Settings
from opencode_bot.feishu_client import FeishuClient
from opencode_bot.feishu_client import FeishuSendError
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
        opencode_fast_ack_s=3.0,
        opencode_session_title_refresh_s=7200,
        opencode_session_title_max_len=18,
        opencode_title_agent_enabled=1,
        opencode_title_agent_session_id="ses_title_agent_00001",
        opencode_title_agent_timeout_s=20,
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


def test_send_text_retries_once_after_http_400(monkeypatch):
    client = FeishuClient(_make_settings())
    calls = {"auth": 0, "send": 0}

    async def fake_request_json(method, url, headers=None, body=None, timeout=30.0):
        _ = (method, headers, body, timeout)
        if "tenant_access_token" in url:
            calls["auth"] += 1
            return {
                "tenant_access_token": f"token_{calls['auth']}",
                "expire": 7200,
                "code": 0,
                "msg": "ok",
            }
        calls["send"] += 1
        if calls["send"] == 1:
            raise urllib.error.HTTPError(
                url=url,
                code=400,
                msg="Bad Request",
                hdrs=cast(Any, None),
                fp=io.BytesIO(b'{"code":99991663,"msg":"invalid access token"}'),
            )
        return {"code": 0, "msg": "ok", "data": {}}

    monkeypatch.setattr("opencode_bot.feishu_client.request_json", fake_request_json)

    asyncio.run(client.send_text(receive_id="ou_xxx", receive_id_type="open_id", text="hello"))

    assert calls["auth"] == 2
    assert calls["send"] == 2


def test_send_text_does_not_retry_for_invalid_open_id(monkeypatch):
    client = FeishuClient(_make_settings())
    calls = {"auth": 0, "send": 0}

    async def fake_request_json(method, url, headers=None, body=None, timeout=30.0):
        _ = (method, headers, body, timeout)
        if "tenant_access_token" in url:
            calls["auth"] += 1
            return {
                "tenant_access_token": "token_1",
                "expire": 7200,
                "code": 0,
                "msg": "ok",
            }
        calls["send"] += 1
        raise urllib.error.HTTPError(
            url=url,
            code=400,
            msg="Bad Request",
            hdrs=cast(Any, None),
            fp=io.BytesIO(
                b'{"code":99992351,"msg":"not a valid open_id or not exists. Invalid ids: [ou_test]"}'
            ),
        )

    monkeypatch.setattr("opencode_bot.feishu_client.request_json", fake_request_json)

    try:
        asyncio.run(client.send_text(receive_id="ou_test", receive_id_type="open_id", text="hello"))
        assert False
    except FeishuSendError as exc:
        assert exc.error_code == 99992351

    assert calls["auth"] == 1
    assert calls["send"] == 1


def test_send_text_wraps_url_timeout_as_feishu_send_error(monkeypatch):
    client = FeishuClient(_make_settings())

    async def fake_request_json(method, url, headers=None, body=None, timeout=30.0):
        _ = (method, headers, body, timeout)
        if "tenant_access_token" in url:
            return {
                "tenant_access_token": "token_1",
                "expire": 7200,
                "code": 0,
                "msg": "ok",
            }
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr("opencode_bot.feishu_client.request_json", fake_request_json)

    try:
        asyncio.run(client.send_text(receive_id="ou_xxx", receive_id_type="open_id", text="hello"))
        assert False
    except FeishuSendError as exc:
        assert exc.status_code == 0
        assert "timed out" in exc.error_message


def test_send_session_picker_uses_short_session_id_in_button_text(monkeypatch):
    client = FeishuClient(_make_settings())
    sent = []

    async def fake_send_message(receive_id_type, payload):
        sent.append((receive_id_type, payload))

    monkeypatch.setattr(client, "_send_message", fake_send_message)

    sessions = [
        OnlineSession(
            session_id="ses_12345abcdefghijklmnopqrstuvwxyz",
            display_name="Session Long",
            status="online",
        )
    ]

    asyncio.run(
        client.send_session_picker(
            receive_id="oc_x",
            receive_id_type="chat_id",
            sessions=sessions,
        )
    )

    card = json.loads(sent[0][1]["content"])
    text = card["elements"][0]["actions"][0]["text"]["content"]
    assert "ses_12345..." in text
    assert "ses_12345abcdefghijklmnopqrstuvwxyz" not in text
