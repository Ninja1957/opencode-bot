import asyncio
import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, cast

from .api import _extract_card_bind_action, _is_ignore_session_prompt, _is_sessions_command
from .config import Settings
from .feishu_client import FeishuClient
from .models import FeishuInbound
from .service import RelayService


logger = logging.getLogger(__name__)


class FeishuLongConnectionRunner:
    def __init__(self, settings: Settings, relay_service: RelayService, feishu_client: FeishuClient):
        self._settings = settings
        self._relay_service = relay_service
        self._feishu_client = feishu_client
        self._running = False
        self._ws_thread = None
        self._ws_client = None
        self._worker_loop = None
        self._worker_thread = None

    def _ensure_worker_loop(self) -> None:
        if self._worker_loop is not None:
            return

        holder = {}

        def run_loop() -> None:
            loop = asyncio.new_event_loop()
            holder["loop"] = loop
            asyncio.set_event_loop(loop)
            loop.run_forever()

        self._worker_thread = threading.Thread(target=run_loop, daemon=True)
        self._worker_thread.start()

        while "loop" not in holder:
            time.sleep(0.01)
        self._worker_loop = holder["loop"]

    def start(self) -> None:
        import lark_oapi as lark

        self._ensure_worker_loop()

        if not self._settings.feishu_verify_token:
            logger.warning("long_conn: FEISHU_VERIFY_TOKEN is empty; event dispatch may be rejected")
        if not self._settings.feishu_encrypt_key:
            logger.info("long_conn: FEISHU_ENCRYPT_KEY is empty")

        builder = lark.EventDispatcherHandler.builder(
            self._settings.feishu_encrypt_key or "",
            self._settings.feishu_verify_token or "",
        )
        register_message = getattr(builder, "register_p2_im_message_receive_v1")
        builder = register_message(self._on_message_sync)
        register_card = getattr(builder, "register_p2_card_action_trigger", None)
        if callable(register_card):
            builder = register_card(self._on_card_action_sync)
        builder = self._register_optional_event_processors(builder)
        build_handler = getattr(builder, "build")
        event_handler = build_handler()

        self._ws_client = lark.ws.Client(
            self._settings.feishu_app_id,
            self._settings.feishu_app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        self._running = True

        def run_ws() -> None:
            lark_ws_client = __import__("lark_oapi.ws.client", fromlist=["loop"])
            ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_loop)
            lark_ws_client.loop = ws_loop
            ws_client = self._ws_client
            if ws_client is None:
                ws_loop.close()
                return
            try:
                while self._running:
                    try:
                        ws_client.start()
                    except Exception:
                        if self._running:
                            time.sleep(3)
            finally:
                ws_loop.close()

        self._ws_thread = threading.Thread(target=run_ws, daemon=True)
        self._ws_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._worker_loop is not None:
            self._worker_loop.call_soon_threadsafe(self._worker_loop.stop)
            self._worker_loop = None
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=2)
            self._worker_thread = None
        if self._ws_thread is not None:
            self._ws_thread.join(timeout=2)
            self._ws_thread = None

    def _submit_coro(self, coro: Any) -> None:
        loop = self._worker_loop
        if loop is None:
            return
        fut = asyncio.run_coroutine_threadsafe(coro, loop)

        def done_callback(future: Any) -> None:
            try:
                future.result()
            except Exception as exc:
                logger.exception("Feishu long connection event failed type=%s err=%s", type(exc).__name__, exc)

        fut.add_done_callback(done_callback)

    def _register_optional_event_processors(self, builder: Any) -> Any:
        candidates = [
            "register_p2_im_message_message_read_v1",
            "register_p2_im_chat_access_event_bot_p2p_chat_entered_v1",
        ]
        output = builder
        for method_name in candidates:
            method = getattr(output, method_name, None)
            if callable(method):
                output = method(self._on_ignored_event_sync)
        return output

    @staticmethod
    def _on_ignored_event_sync(data: Any) -> None:
        event = getattr(data, "event", None)
        event_type = str(getattr(event, "type", "") or "unknown")
        logger.debug("long_conn: ignored event type=%s", event_type)

    def _on_message_sync(self, data: Any) -> None:
        logger.info("long_conn: received message callback")
        self._submit_coro(self._on_message(data))

    def _on_card_action_sync(self, data: Any) -> None:
        logger.info("long_conn: received card action callback")
        self._submit_coro(self._on_card_action(data))

    async def _on_message(self, data: Any) -> None:
        event = getattr(data, "event", None)
        if event is None:
            logger.warning("long_conn: message event missing event payload")
            return

        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        if message is None or sender is None:
            logger.warning("long_conn: message event missing message/sender")
            return

        sender_type = str(getattr(sender, "sender_type", ""))
        if sender_type == "bot":
            logger.info("long_conn: ignore bot-originated message")
            return

        sender_id = getattr(sender, "sender_id", None)
        open_id = str(getattr(sender_id, "open_id", "") or "")
        message_id = str(getattr(message, "message_id", "") or "")
        chat_id = str(getattr(message, "chat_id", "") or "")
        chat_type = str(getattr(message, "chat_type", "") or "")
        raw_content = getattr(message, "content", "")
        if not open_id or not message_id:
            logger.warning("long_conn: message missing open_id or message_id")
            return

        text = ""
        if isinstance(raw_content, str):
            try:
                content_obj = json.loads(raw_content)
                if isinstance(content_obj, dict):
                    text = str(content_obj.get("text") or "")
            except json.JSONDecodeError:
                text = raw_content
        text = _normalize_command_text(text)

        inbound = FeishuInbound(
            message_id=message_id,
            chat_id=chat_id,
            open_id=open_id,
            chat_type=chat_type,
            text=text,
        )

        reply = await self._relay_service.handle_inbound(inbound)
        if reply is None:
            logger.info("long_conn: dedup or ignored message_id=%s", message_id)
            return

        target = self._relay_service.to_reply_target(inbound)
        if _is_sessions_command(text):
            logger.info("long_conn: /sessions recognized message_id=%s", message_id)
            sessions = await self._relay_service.list_online_sessions()
            if sessions:
                logger.info("long_conn: sending session picker count=%s", len(sessions))
                await self._feishu_client.send_session_picker(
                    receive_id=target.receive_id,
                    receive_id_type=target.receive_id_type,
                    sessions=sessions,
                )
            else:
                logger.info("long_conn: no online sessions for message_id=%s", message_id)

        await self._feishu_client.send_text(
            receive_id=target.receive_id,
            receive_id_type=target.receive_id_type,
            text=reply,
        )
        
        if reply and reply.startswith("当前绑定 session:"):
            await self._feishu_client.send_unbind_button(
                receive_id=target.receive_id,
                receive_id_type=target.receive_id_type,
            )
        
        logger.info("long_conn: replied message_id=%s", message_id)

    async def _on_card_action(self, data: Any) -> None:
        event = getattr(data, "event", None)
        if event is None:
            logger.warning("long_conn: card action missing event payload")
            return
        event_dict = _to_dict(event)
        
        # Handle unbind action
        action_value = event_dict.get("action", {}).get("value", {})
        if action_value.get("action") == "unbind_session":
            context = event_dict.get("context", {})
            operator = event_dict.get("operator", {})
            chat_id = str(context.get("open_chat_id") or "")
            open_id = str(operator.get("operator_id", {}).get("open_id", "") or "")
            peer_keys = []
            if chat_id:
                peer_keys.append(f"chat:{chat_id}")
            if open_id:
                peer_keys.append(f"user:{open_id}")
            reply = ""
            for peer_key in peer_keys:
                reply = self._relay_service.unbind_peer(peer_key)
            await self._feishu_client.send_text(
                receive_id=open_id or chat_id,
                receive_id_type="open_id" if open_id else "chat_id",
                text=reply,
            )
            return
        
        parsed = _extract_card_bind_action(event_dict)
        if parsed is None:
            if _is_ignore_session_prompt(event_dict):
                logger.info("long_conn: ignore_session_prompt action acknowledged")
                return
            logger.warning("long_conn: card action not matched bind_session payload")
            return
        peer_keys_raw, receive_id, receive_id_type, session_id = parsed
        peer_keys = cast(List[str], peer_keys_raw)
        reply = ""
        for peer_key in peer_keys:
            reply = await self._relay_service.bind_peer_to_session(peer_key, session_id)
        await self._feishu_client.send_text(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            text=reply,
        )


def _to_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        output: Dict[str, Any] = {}
        for key, item in value.items():
            output[str(key)] = _to_plain(item)
        return output
    if hasattr(value, "__dict__"):
        output = {}
        for key, item in vars(value).items():
            if str(key).startswith("_"):
                continue
            output[str(key)] = _to_plain(item)
        return output
    return {}


def _to_plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]
    if hasattr(value, "__dict__"):
        return {str(k): _to_plain(v) for k, v in vars(value).items() if not str(k).startswith("_")}
    return value


def _normalize_command_text(text: str) -> str:
    output = text.strip()
    output = re.sub(r"<at\b[^>]*>.*?</at>", " ", output, flags=re.IGNORECASE)
    output = output.replace("\u00a0", " ")
    output = " ".join(output.split())
    return output.strip()
