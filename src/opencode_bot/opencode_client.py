import os
import subprocess
from typing import Any, Dict, List, Optional

from .config import Settings
from .http_client import request_json
from .models import OnlineSession
from .session_registry import SessionRegistry


class OpenCodeClient:
    def __init__(self, settings: Settings):
        self._transport = settings.opencode_transport
        self._opencode_bin = settings.opencode_bin
        self._session_registry = SessionRegistry(settings.opencode_db_path)
        self._base_url = settings.opencode_base_url.rstrip("/")
        self._list_path = settings.opencode_list_sessions_path
        self._list_path_alt = settings.opencode_list_sessions_path_alt
        self._send_path = settings.opencode_send_message_path
        self._send_path_alt = settings.opencode_send_message_path_alt
        self._timeout = settings.opencode_request_timeout_s
        self._api_key = settings.opencode_api_key

    def _headers(self) -> Dict[str, str]:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    async def list_online_sessions(self) -> List[OnlineSession]:
        if self._transport == "cli":
            return self._session_registry.refresh()
        payload = await self._fetch_sessions_payload()

        raw = payload.get("sessions", payload)
        sessions: List[OnlineSession] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                session_id = str(item.get("session_id") or item.get("sessionId") or "")
                if not session_id:
                    continue
                display_name = str(item.get("display_name") or item.get("title") or session_id)
                status = str(item.get("status") or "online")
                sessions.append(
                    OnlineSession(session_id=session_id, display_name=display_name, status=status)
                )
        return sessions

    async def _fetch_sessions_payload(self) -> Any:
        urls = [f"{self._base_url}{self._list_path}"]
        if self._list_path_alt:
            urls.append(f"{self._base_url}{self._list_path_alt}")

        last_error: Optional[Exception] = None
        for url in urls:
            try:
                return await request_json("GET", url, headers=self._headers(), timeout=self._timeout)
            except Exception as exc:
                last_error = exc
                continue
        if last_error:
            raise last_error
        return {}

    async def send_to_session(self, session_id: str, text: str) -> str:
        if self._transport == "cli":
            return self._send_to_session_by_cli(session_id, text)

        url = f"{self._base_url}{self._send_path}"
        body = {
            "session_id": session_id,
            "sessionId": session_id,
            "message": text,
            "content": text,
        }
        payload: Any = await self._send_with_fallback(url, body)

        if isinstance(payload, dict):
            candidates = [
                payload.get("reply"),
                payload.get("message"),
                payload.get("content"),
                payload.get("text"),
            ]
            for value in candidates:
                if isinstance(value, str) and value.strip():
                    return value
        if isinstance(payload, str) and payload.strip():
            return payload
        return "会话已收到消息，但未返回可展示文本。"

    def list_cached_sessions(self, include_offline: bool) -> List[OnlineSession]:
        if self._transport != "cli":
            return []
        return self._session_registry.list_sessions(include_offline=include_offline)

    def _send_to_session_by_cli(self, session_id: str, text: str) -> str:
        binary = self._opencode_bin
        if not os.path.exists(binary):
            binary = "opencode"
        cmd = [binary, "run", "--session", session_id, "--format", "default", text]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=max(1, int(self._timeout)),
            )
        except subprocess.TimeoutExpired:
            return "发送到会话超时，请稍后重试；如持续超时请检查目标 session 是否仍在线。"
        except OSError as exc:
            return f"调用 opencode 失败: {exc}"
        output = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode == 0 and output:
            return output
        if err:
            return err
        return "会话调用完成，但未返回文本输出。"

    async def _send_with_fallback(self, url: str, body: Dict[str, Any]) -> Any:
        urls = [url]
        if self._send_path_alt:
            urls.append(f"{self._base_url}{self._send_path_alt}")
        last_error: Optional[Exception] = None
        for target in urls:
            try:
                return await request_json(
                    "POST",
                    target,
                    headers=self._headers(),
                    body=body,
                    timeout=self._timeout,
                )
            except Exception as exc:
                last_error = exc
                continue
        if last_error:
            raise last_error
        return {}
