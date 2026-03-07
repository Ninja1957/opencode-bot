import json
from typing import Dict, List, Optional

from .config import Settings
from .http_client import request_json
from .models import OnlineSession


class FeishuClient:
    def __init__(self, settings: Settings):
        self._app_id = settings.feishu_app_id
        self._app_secret = settings.feishu_app_secret
        self._token: Optional[str] = None

    async def _tenant_token(self) -> str:
        if self._token:
            return self._token
        body = {"app_id": self._app_id, "app_secret": self._app_secret}
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        data = await request_json("POST", url, body=body, timeout=15.0)
        token = data.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("failed to get tenant_access_token")
        self._token = token
        return token

    async def send_text(self, receive_id: str, receive_id_type: str, text: str) -> None:
        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        await self._send_message(receive_id_type=receive_id_type, payload=payload)

    async def send_session_picker(
        self,
        receive_id: str,
        receive_id_type: str,
        sessions: List[OnlineSession],
    ) -> None:
        elements = []
        for item in sessions[:20]:
            title = f"{item.display_name} ({item.session_id})"
            value = {"action": "bind_session", "session_id": item.session_id}
            elements.append(
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": title[:120]},
                            "type": "primary",
                            "value": value,
                        }
                    ],
                }
            )

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "请选择要控制的 Session"}
            },
            "elements": elements,
        }

        payload = {
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }
        await self._send_message(receive_id_type=receive_id_type, payload=payload)

    async def _send_message(self, receive_id_type: str, payload: Dict[str, str]) -> None:
        token = await self._tenant_token()
        url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
        headers: Dict[str, str] = {"Authorization": f"Bearer {token}"}
        await request_json("POST", url, headers=headers, body=payload, timeout=15.0)
