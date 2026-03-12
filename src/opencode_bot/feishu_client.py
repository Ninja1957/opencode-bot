import asyncio
import json
import logging
import mimetypes
import os
import time
import urllib.error
import urllib.request
from typing import Any
from typing import Dict, List, Optional, Tuple

from .config import Settings
from .http_client import request_json
from .models import OnlineSession


logger = logging.getLogger(__name__)


class FeishuClient:
    def __init__(self, settings: Settings):
        self._app_id = settings.feishu_app_id
        self._app_secret = settings.feishu_app_secret
        self._token: Optional[str] = None
        self._token_expire_at: float = 0.0

    async def _tenant_token(self, force_refresh: bool = False) -> str:
        now = time.time()
        if not force_refresh and self._token and now < self._token_expire_at:
            return self._token
        body = {"app_id": self._app_id, "app_secret": self._app_secret}
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        data = await request_json("POST", url, body=body, timeout=15.0)
        token = data.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("failed to get tenant_access_token")
        expire_seconds = int(data.get("expire", 7200) or 7200)
        self._token = token
        self._token_expire_at = now + max(60, expire_seconds - 120)
        return token

    async def send_text(self, receive_id: str, receive_id_type: str, text: str) -> None:
        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        await self._send_message(receive_id_type=receive_id_type, payload=payload)

    async def send_image(self, receive_id: str, receive_id_type: str, image_path: str) -> None:
        image_key = await self._upload_image(image_path)
        payload = {
            "receive_id": receive_id,
            "msg_type": "image",
            "content": json.dumps({"image_key": image_key}, ensure_ascii=False),
        }
        await self._send_message(receive_id_type=receive_id_type, payload=payload)

    async def send_file(self, receive_id: str, receive_id_type: str, file_path: str) -> None:
        file_key = await self._upload_file(file_path)
        payload = {
            "receive_id": receive_id,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
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
                title = f"{item.display_name} ({_display_session_id(item.session_id)})"
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
        url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
        for attempt in range(2):
            token = await self._tenant_token(force_refresh=attempt > 0)
            headers: Dict[str, str] = {"Authorization": f"Bearer {token}"}
            started = time.perf_counter()
            try:
                await request_json("POST", url, headers=headers, body=payload, timeout=30.0)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                logger.debug(
                    "feishu send ok receive_id_type=%s msg_type=%s receive_id=%s elapsed_ms=%s",
                    receive_id_type,
                    payload.get("msg_type", ""),
                    _mask_receive_id(payload.get("receive_id", "")),
                    elapsed_ms,
                )
                return
            except urllib.error.HTTPError as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                detail, err_code, err_msg = _http_error_detail(exc)
                logger.error(
                    "feishu send failed status=%s receive_id_type=%s msg_type=%s receive_id=%s elapsed_ms=%s detail=%s",
                    exc.code,
                    receive_id_type,
                    payload.get("msg_type", ""),
                    _mask_receive_id(payload.get("receive_id", "")),
                    elapsed_ms,
                    detail,
                )
                if attempt == 0 and _should_refresh_token(exc.code, err_code, err_msg):
                    self._token = None
                    self._token_expire_at = 0.0
                    logger.warning("feishu send retrying once after token refresh status=%s", exc.code)
                    continue
                raise FeishuSendError(
                    status_code=exc.code,
                    error_code=err_code,
                    error_message=err_msg,
                    receive_id=str(payload.get("receive_id", "") or ""),
                    receive_id_type=receive_id_type,
                    msg_type=str(payload.get("msg_type", "") or ""),
                    detail=detail,
                ) from exc
            except urllib.error.URLError as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                reason = str(getattr(exc, "reason", exc) or exc)
                logger.error(
                    "feishu send url error receive_id_type=%s msg_type=%s receive_id=%s elapsed_ms=%s reason=%s",
                    receive_id_type,
                    payload.get("msg_type", ""),
                    _mask_receive_id(payload.get("receive_id", "")),
                    elapsed_ms,
                    reason,
                )
                raise FeishuSendError(
                    status_code=0,
                    error_code=None,
                    error_message=reason,
                    receive_id=str(payload.get("receive_id", "") or ""),
                    receive_id_type=receive_id_type,
                    msg_type=str(payload.get("msg_type", "") or ""),
                    detail=reason,
                ) from exc
            except TimeoutError as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                reason = str(exc or "timeout")
                logger.error(
                    "feishu send timeout receive_id_type=%s msg_type=%s receive_id=%s elapsed_ms=%s reason=%s",
                    receive_id_type,
                    payload.get("msg_type", ""),
                    _mask_receive_id(payload.get("receive_id", "")),
                    elapsed_ms,
                    reason,
                )
                raise FeishuSendError(
                    status_code=0,
                    error_code=None,
                    error_message=reason,
                    receive_id=str(payload.get("receive_id", "") or ""),
                    receive_id_type=receive_id_type,
                    msg_type=str(payload.get("msg_type", "") or ""),
                    detail=reason,
                ) from exc

    async def _upload_image(self, file_path: str) -> str:
        token = await self._tenant_token()
        url = "https://open.feishu.cn/open-apis/im/v1/images"
        fields = {"image_type": "message"}
        data, content_type = _build_multipart(fields, {"image": file_path})
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        }
        result = await _request_multipart(url, data, headers)
        image_key = result.get("data", {}).get("image_key")
        if not isinstance(image_key, str) or not image_key:
            raise RuntimeError("failed to upload image")
        return image_key

    async def _upload_file(self, file_path: str) -> str:
        token = await self._tenant_token()
        url = "https://open.feishu.cn/open-apis/im/v1/files"
        fields = {"file_type": "stream", "file_name": os.path.basename(file_path)}
        data, content_type = _build_multipart(fields, {"file": file_path})
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        }
        result = await _request_multipart(url, data, headers)
        file_key = result.get("data", {}).get("file_key")
        if not isinstance(file_key, str) or not file_key:
            raise RuntimeError("failed to upload file")
        return file_key

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

    async def send_unbind_button(self, receive_id: str, receive_id_type: str) -> None:
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "会话管理"}
            },
            "elements": [
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "解绑当前会话"},
                            "type": "danger",
                            "value": {"action": "unbind_session"},
                        }
                    ],
                }
            ],
        }
        payload = {
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }
        await self._send_message(receive_id_type=receive_id_type, payload=payload)


def _http_error_detail(exc: urllib.error.HTTPError) -> "tuple[str, Optional[int], str]":
    raw = b""
    try:
        raw = exc.read() or b""
    except Exception:
        return "", None, ""
    if not raw:
        return "", None, ""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return "", None, ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text[:500], None, text[:500]
    if isinstance(data, dict):
        code = data.get("code")
        msg = data.get("msg")
        out_code: Optional[int] = None
        if isinstance(code, int):
            out_code = code
        elif isinstance(code, str) and code.isdigit():
            out_code = int(code)
        out_msg = str(msg or "")
        return json.dumps({"code": code, "msg": msg}, ensure_ascii=False), out_code, out_msg
    return text[:500], None, text[:500]


def _should_refresh_token(status_code: int, error_code: Optional[int], error_message: str) -> bool:
    if status_code == 401:
        return True
    if status_code != 400:
        return False
    if error_code in {99991661, 99991663}:
        return True
    lowered = error_message.lower()
    return "token" in lowered and ("invalid" in lowered or "expired" in lowered)


def _mask_receive_id(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return text
    return f"{text[:4]}***{text[-4:]}"


def _display_session_id(session_id: str) -> str:
    text = str(session_id or "")
    if text.startswith("ses_") and len(text) > 9:
        return f"{text[:9]}..."
    return text


class FeishuSendError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        error_code: Optional[int],
        error_message: str,
        receive_id: str,
        receive_id_type: str,
        msg_type: str,
        detail: str,
    ):
        super().__init__(
            f"Feishu send failed status={status_code} code={error_code} receive_id_type={receive_id_type}"
        )
        self.status_code = status_code
        self.error_code = error_code
        self.error_message = error_message
        self.receive_id = receive_id
        self.receive_id_type = receive_id_type
        self.msg_type = msg_type
        self.detail = detail


def _build_multipart(fields: Dict[str, str], files: Dict[str, str]) -> Tuple[bytes, str]:
    boundary = f"----opencodebot{int(time.time() * 1000)}"
    lines: list[bytes] = []

    for key, value in fields.items():
        lines.append(f"--{boundary}".encode("utf-8"))
        lines.append(f"Content-Disposition: form-data; name=\"{key}\"".encode("utf-8"))
        lines.append(b"")
        lines.append(str(value).encode("utf-8"))

    for key, path in files.items():
        file_name = os.path.basename(path)
        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        with open(path, "rb") as handle:
            content = handle.read()
        lines.append(f"--{boundary}".encode("utf-8"))
        lines.append(
            f"Content-Disposition: form-data; name=\"{key}\"; filename=\"{file_name}\"".encode("utf-8")
        )
        lines.append(f"Content-Type: {mime_type}".encode("utf-8"))
        lines.append(b"")
        lines.append(content)

    lines.append(f"--{boundary}--".encode("utf-8"))
    lines.append(b"")
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"


async def _request_multipart(url: str, data: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    loop = asyncio.get_running_loop()

    def _run() -> Dict[str, Any]:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}

    return await loop.run_in_executor(None, _run)
