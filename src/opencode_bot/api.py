import asyncio
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

from .config import Settings
from .feishu_client import FeishuClient
from .models import FeishuInbound
from .opencode_client import OpenCodeClient
from .session_monitor import SessionMonitor
from .service import RelayService
from .storage import Storage


def create_server(settings: Settings) -> ThreadingHTTPServer:
    storage = Storage(settings.storage_path)
    opencode_client = OpenCodeClient(settings)
    relay_service = RelayService(storage=storage, opencode_client=opencode_client)
    feishu_client = FeishuClient(settings)
    monitor = SessionMonitor(settings=settings, opencode_client=opencode_client, feishu_client=feishu_client)
    if settings.opencode_transport == "cli" or settings.opencode_watch_enabled:
        monitor.start()

    handler = _make_handler(settings, relay_service, feishu_client, opencode_client)
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    setattr(server, "session_monitor", monitor)
    return server


def _make_handler(
    settings: Settings,
    relay_service: RelayService,
    feishu_client: FeishuClient,
    opencode_client: OpenCodeClient,
):
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._send_json(HTTPStatus.OK, {"status": "ok"})
                return
            if self.path.startswith("/opencode/sessions"):
                include_offline = "include_offline=1" in self.path
                refresh = "refresh=1" in self.path
                if refresh:
                    sessions = asyncio.run(opencode_client.list_online_sessions())
                else:
                    sessions = opencode_client.list_cached_sessions(include_offline=include_offline)
                    if not sessions:
                        sessions = asyncio.run(opencode_client.list_online_sessions())
                payload = {
                    "count": len(sessions),
                    "sessions": [
                        {
                            "session_id": item.session_id,
                            "display_name": item.display_name,
                            "status": item.status,
                            "tty": item.tty,
                            "pid": item.pid,
                            "last_seen_ts": item.last_seen_ts,
                        }
                        for item in sessions
                    ],
                }
                self._send_json(HTTPStatus.OK, payload)
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path != "/feishu/events":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return

            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
                return

            if settings.feishu_verify_token:
                token = payload.get("token")
                if token != settings.feishu_verify_token:
                    self._send_json(HTTPStatus.FORBIDDEN, {"error": "invalid_token"})
                    return

            if payload.get("type") == "url_verification":
                challenge = payload.get("challenge")
                if not isinstance(challenge, str):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_challenge"})
                    return
                self._send_json(HTTPStatus.OK, {"challenge": challenge})
                return

            event = payload.get("event")
            if not isinstance(event, dict):
                self._send_json(HTTPStatus.OK, {"ok": True})
                return

            card_action = _extract_card_bind_action(event)
            if card_action is not None:
                peer_key, receive_id, receive_id_type, session_id = card_action
                reply = asyncio.run(relay_service.bind_peer_to_session(peer_key, session_id))
                asyncio.run(
                    feishu_client.send_text(
                        receive_id=receive_id,
                        receive_id_type=receive_id_type,
                        text=reply,
                    )
                )
                self._send_json(HTTPStatus.OK, {"ok": True, "action": "bind_session"})
                return

            sender = event.get("sender", {})
            sender_id = sender.get("sender_id", {})
            message = event.get("message", {})
            if not isinstance(sender, dict) or not isinstance(sender_id, dict) or not isinstance(message, dict):
                self._send_json(HTTPStatus.OK, {"ok": True})
                return

            message_id = str(message.get("message_id") or "")
            chat_id = str(message.get("chat_id") or "")
            chat_type = str(message.get("chat_type") or "")
            open_id = str(sender_id.get("open_id") or "")
            if not message_id or not open_id:
                self._send_json(HTTPStatus.OK, {"ok": True})
                return

            if sender.get("sender_type") == "bot":
                self._send_json(HTTPStatus.OK, {"ok": True})
                return

            raw_content = message.get("content")
            text = ""
            if isinstance(raw_content, str):
                try:
                    content_obj = json.loads(raw_content)
                    if isinstance(content_obj, dict):
                        text = str(content_obj.get("text") or "")
                except json.JSONDecodeError:
                    text = raw_content

            inbound = FeishuInbound(
                message_id=message_id,
                chat_id=chat_id,
                open_id=open_id,
                chat_type=chat_type,
                text=text,
            )

            reply = asyncio.run(relay_service.handle_inbound(inbound))
            if reply is None:
                self._send_json(HTTPStatus.OK, {"ok": True, "dedup": True})
                return

            target = relay_service.to_reply_target(inbound)

            if _is_sessions_command(text):
                sessions = asyncio.run(relay_service.list_online_sessions())
                if sessions:
                    asyncio.run(
                        feishu_client.send_session_picker(
                            receive_id=target.receive_id,
                            receive_id_type=target.receive_id_type,
                            sessions=sessions,
                        )
                    )

            asyncio.run(
                feishu_client.send_text(
                    receive_id=target.receive_id,
                    receive_id_type=target.receive_id_type,
                    text=reply,
                )
            )
            self._send_json(HTTPStatus.OK, {"ok": True})

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def _is_sessions_command(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered in {"/sessions", "sessions"}


def _extract_card_bind_action(event: Dict[str, Any]) -> Optional[Tuple[str, str, str, str]]:
    event_type = str(event.get("type") or "")
    if event_type and event_type != "card.action.trigger":
        return None

    action = event.get("action")
    if not isinstance(action, dict):
        return None
    value = action.get("value")
    if not isinstance(value, dict):
        return None
    if value.get("action") != "bind_session":
        return None
    session_id = str(value.get("session_id") or "").strip()
    if not session_id:
        return None

    context = event.get("context")
    operator = event.get("operator")

    chat_id = ""
    if isinstance(context, dict):
        chat_id = str(context.get("open_chat_id") or "")
    if not chat_id:
        chat_id = str(event.get("open_chat_id") or "")

    open_id = ""
    if isinstance(operator, dict):
        operator_id = operator.get("operator_id")
        if isinstance(operator_id, dict):
            open_id = str(operator_id.get("open_id") or "")
        if not open_id:
            open_id = str(operator.get("open_id") or "")
    if not open_id:
        open_id = str(event.get("open_id") or "")

    if chat_id:
        return f"chat:{chat_id}", chat_id, "chat_id", session_id
    if open_id:
        return f"user:{open_id}", open_id, "open_id", session_id
    return None
