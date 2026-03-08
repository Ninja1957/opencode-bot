from dataclasses import dataclass
import re
from typing import List, Optional, Tuple

from .models import FeishuInbound, OnlineSession
from .opencode_client import OpenCodeClient
from .storage import Storage


@dataclass
class ReplyTarget:
    receive_id: str
    receive_id_type: str


class RelayService:
    def __init__(self, storage: Storage, opencode_client: OpenCodeClient):
        self._storage = storage
        self._opencode = opencode_client

    @staticmethod
    def to_reply_target(inbound: FeishuInbound) -> ReplyTarget:
        if inbound.chat_type == "group" and inbound.chat_id:
            return ReplyTarget(receive_id=inbound.chat_id, receive_id_type="chat_id")
        return ReplyTarget(receive_id=inbound.open_id, receive_id_type="open_id")

    async def handle_inbound(self, inbound: FeishuInbound) -> Optional[str]:
        target = self.to_reply_target(inbound)
        self._storage.upsert_peer(
            peer_key=inbound.peer_key,
            receive_id=target.receive_id,
            receive_id_type=target.receive_id_type,
        )

        if not self._storage.try_mark_processed(inbound.message_id, inbound.peer_key):
            return None

        text = inbound.text.strip()
        if not text:
            return "收到空消息，请输入内容。"

        lowered = text.lower()
        if lowered in {"/help", "help"}:
            return self._help_text()
        if lowered in {"/sessions", "session_list", "/sl", "sl", "sessions"}:
            return await self._list_sessions_text()
        if lowered in {"/current", "/c", "current", "c"}:
            return self._current_binding_text(inbound.peer_key)
        if lowered in {"/unbind", "/su", "session_unbind", "unbind", "su"}:
            return self._unbind_session(inbound.peer_key)
        if lowered.startswith("/bind ") or lowered.startswith("bind "):
            target = text.split(maxsplit=1)
            if len(target) < 2:
                return "用法：/bind <session_id>"
            return await self._bind_session(inbound.peer_key, target[1].strip())

        targeted = self._parse_targeted_message(text)
        if targeted is not None:
            session_id, payload = targeted
            return await self._send_to_specific_session(inbound, session_id, payload)

        bound = self._storage.get_bound_session(inbound.peer_key)
        if not bound:
            return "当前未绑定 session。请先发送 /session_list (/sl) 查看，再 /bind <session_id> 绑定。"

        response = await self._opencode.send_to_session(bound, text)
        self._storage.save_round(
            message_id=inbound.message_id,
            peer_key=inbound.peer_key,
            session_id=bound,
            request_text=text,
            response_text=response,
        )
        return response

    async def _send_to_specific_session(self, inbound: FeishuInbound, session_id: str, text: str) -> str:
        target = await self._resolve_online_session(session_id)
        if target is None:
            return f"未找到在线 session: {session_id}。请先 /sessions 查看可用列表。"

        response = await self._opencode.send_to_session(target.session_id, text)
        self._storage.save_round(
            message_id=inbound.message_id,
            peer_key=inbound.peer_key,
            session_id=target.session_id,
            request_text=text,
            response_text=response,
        )
        return response

    async def _bind_session(self, peer_key: str, session_id: str) -> str:
        return await self.bind_peer_to_session(peer_key, session_id)

    async def list_online_sessions(self) -> List[OnlineSession]:
        return await self._opencode.list_online_sessions()

    async def bind_peer_to_session(self, peer_key: str, session_id: str) -> str:
        target = await self._resolve_online_session(session_id)
        if target is None:
            return f"未找到在线 session: {session_id}。请先 /sessions 查看可用列表。"
        self._storage.bind_session(peer_key, target.session_id)
        return f"已绑定 session: {target.session_id} ({target.display_name})"

    def unbind_peer(self, peer_key: str) -> str:
        return self._unbind_session(peer_key)

    async def _resolve_online_session(self, session_id: str) -> Optional[OnlineSession]:
        sessions = await self._opencode.list_online_sessions()
        return next((s for s in sessions if s.session_id == session_id), None)

    async def _list_sessions_text(self) -> str:
        sessions = await self._opencode.list_online_sessions()
        if not sessions:
            return "当前没有可用在线 session。"
        lines = ["在线 session 列表："]
        for idx, session in enumerate(sessions, start=1):
            lines.append(self._render_session_line(idx, session))
        lines.append("发送 /bind <session_id> 进行绑定。")
        return "\n".join(lines)

    @staticmethod
    def _render_session_line(idx: int, session: OnlineSession) -> str:
        location = session.tty if session.tty else "?"
        pid_text = str(session.pid) if session.pid else "-"
        return f"{idx}. {session.session_id} | {session.display_name} | {session.status} | tty={location} | pid={pid_text}"

    def _current_binding_text(self, peer_key: str) -> str:
        bound = self._storage.get_bound_session(peer_key)
        if not bound:
            return "当前未绑定 session。"
        return f"当前绑定 session: {bound}"

    def _unbind_session(self, peer_key: str) -> str:
        removed = self._storage.unbind_session(peer_key)
        if removed:
            return "已解绑当前会话。"
        return "当前没有已绑定的 session，无需解绑。"

    @staticmethod
    def _help_text() -> str:
        return (
            "可用命令：\n"
            "/session_list (/sl) 查看在线 session\n"
            "/bind <session_id> 绑定会话\n"
            "/session_unbind (/su) 解绑当前会话\n"
            "/send <session_id> <内容> 定向发指令\n"
            "@ses_xxx <内容> 定向发指令\n"
            "/current (/c) 查看当前绑定\n"
            "/help 查看帮助\n"
            "非命令消息会转发到当前绑定 session"
        )

    @staticmethod
    def _parse_targeted_message(text: str) -> "Optional[Tuple[str, str]]":
        stripped = text.strip()
        send_match = re.match(r"^(?:/send|send)\s+([A-Za-z0-9_-]+)\s+(.+)$", stripped)
        if send_match:
            return send_match.group(1), send_match.group(2).strip()

        at_match = re.match(r"^@([A-Za-z0-9_-]+)\s+(.+)$", stripped)
        if at_match:
            return at_match.group(1), at_match.group(2).strip()

        return None
