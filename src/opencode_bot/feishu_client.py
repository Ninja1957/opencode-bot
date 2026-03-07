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
        if not sessions:
            await self.send_text(receive_id=receive_id, receive_id_type=receive_id_type, text="当前没有在线 session。")
            return

        page_size = 20
        total = len(sessions)
        total_pages = (total + page_size - 1) // page_size
        for page_idx in range(total_pages):
            begin = page_idx * page_size
            end = min(begin + page_size, total)
            chunk = sessions[begin:end]

            elements = []
            for item in chunk:
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

            title = "请选择要控制的 Session"
            if total_pages > 1:
                title = f"请选择要控制的 Session ({page_idx + 1}/{total_pages})"

            card = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title}
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

    async def send_session_bind_prompt(
        self,
        receive_id: str,
        receive_id_type: str,
        session_id: str,
        preview: str,
    ) -> None:
        content = preview if len(preview) <= 200 else f"{preview[:200]}..."
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "检测到新的 Session 更新"}
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"Session `{session_id}` 有新消息：\n> {content}\n\n是否绑定到这个 session 继续对话？",
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "绑定并继续"},
                            "type": "primary",
                            "value": {"action": "bind_session", "session_id": session_id},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "暂不绑定"},
                            "type": "default",
                            "value": {"action": "ignore_session_prompt", "session_id": session_id},
                        },
                    ],
                },
            ],
        }
        payload = {
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }
        await self._send_message(receive_id_type=receive_id_type, payload=payload)
