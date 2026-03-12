import asyncio
from dataclasses import dataclass
import logging
import json
import os
import re
import time
from typing import List, Optional, Sequence, Tuple

from .models import FeishuInbound, OnlineSession
from .feishu_client import FeishuClient
from .config import Settings
from .opencode_client import OpenCodeClient
from .storage import Storage


logger = logging.getLogger(__name__)


@dataclass
class ReplyTarget:
    receive_id: str
    receive_id_type: str


class RelayService:
    def __init__(
        self,
        storage: Storage,
        opencode_client: OpenCodeClient,
        settings: "Settings",
        feishu_client: Optional[FeishuClient] = None,
        fast_ack_s: float = 3.0,
    ):
        self._storage = storage
        self._opencode = opencode_client
        self._settings = settings
        self._feishu_client = feishu_client
        self._fast_ack_s = max(0.5, float(fast_ack_s))

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
                return "用法：/bind <session_id> 或 /bind <序号>（序号从 /session_list 获取）"
            arg = target[1].strip()
            if arg.isdigit():
                idx = int(arg)
                sessions = await self._list_bindable_sessions()
                if idx < 1 or idx > len(sessions):
                    return f"序号 {idx} 无效，请先用 /session_list 查看可用 session。"
                session_id = sessions[idx - 1].session_id
                return await self._bind_session(inbound.peer_key, session_id)
            return await self._bind_session(inbound.peer_key, arg)

        targeted = self._parse_targeted_message(text)
        if targeted is not None:
            session_id, payload = targeted
            return await self._send_to_specific_session(inbound, session_id, payload)

        file_reply = await self._handle_file_intent(inbound, target)
        if file_reply is not None:
            return file_reply

        bound = self._storage.get_bound_session(inbound.peer_key)
        if not bound:
            return "当前未绑定 session。请先发送 /session_list (/sl) 查看，再 /bind <序号> 或 /bind <session_id> 绑定。"

        return await self._send_with_fast_ack(
            message_id=inbound.message_id,
            peer_key=inbound.peer_key,
            session_id=bound,
            request_text=text,
        )

    async def _send_to_specific_session(self, inbound: FeishuInbound, session_id: str, text: str) -> str:
        target = await self._resolve_online_session(session_id)
        if target is None:
            return f"未找到在线 session: {self._display_session_id(session_id)}。请先 /sessions 查看可用列表。"

        return await self._send_with_fast_ack(
            message_id=inbound.message_id,
            peer_key=inbound.peer_key,
            session_id=target.session_id,
            request_text=text,
        )

    async def _send_with_fast_ack(
        self,
        message_id: str,
        peer_key: str,
        session_id: str,
        request_text: str,
    ) -> str:
        task = asyncio.create_task(self._opencode.send_to_session(session_id, request_text))
        try:
            response = await asyncio.wait_for(asyncio.shield(task), timeout=self._fast_ack_s)
            self._storage.save_round(
                message_id=message_id,
                peer_key=peer_key,
                session_id=session_id,
                request_text=request_text,
                response_text=response,
            )
            return response
        except asyncio.TimeoutError:
            logger.info(
                "relay fast-ack timeout peer=%s session=%s message_id=%s ack_s=%.1f",
                peer_key,
                session_id,
                message_id,
                self._fast_ack_s,
            )
            task.add_done_callback(
                lambda fut: self._persist_background_result(
                    fut=fut,
                    message_id=message_id,
                    peer_key=peer_key,
                    session_id=session_id,
                    request_text=request_text,
                )
            )
            return "已收到，正在等待 opencode 处理（若耗时较长，将稍后推送结果）。"

    def _persist_background_result(
        self,
        fut: "asyncio.Future[str]",
        message_id: str,
        peer_key: str,
        session_id: str,
        request_text: str,
    ) -> None:
        try:
            response = fut.result()
        except Exception as exc:
            logger.error(
                "relay background result failed peer=%s session=%s message_id=%s err=%s",
                peer_key,
                session_id,
                message_id,
                exc,
            )
            response = f"后台执行失败: {exc}"
        bg_message_id = f"{message_id}#bg#{int(time.time() * 1000)}"
        self._storage.save_round(
            message_id=bg_message_id,
            peer_key=peer_key,
            session_id=session_id,
            request_text=request_text,
            response_text=response,
        )

    async def _bind_session(self, peer_key: str, session_id: str) -> str:
        return await self.bind_peer_to_session(peer_key, session_id)

    async def list_online_sessions(self) -> List[OnlineSession]:
        return await self._opencode.list_online_sessions()

    async def bind_peer_to_session(self, peer_key: str, session_id: str) -> str:
        target = await self._resolve_online_session(session_id)
        if target is None:
            return f"未找到在线 session: {self._display_session_id(session_id)}。请先 /sessions 查看可用列表。"
        self._storage.bind_session(peer_key, target.session_id)
        return f"已绑定 session: {self._display_session_id(target.session_id)} ({target.display_name})"

    def unbind_peer(self, peer_key: str) -> str:
        return self._unbind_session(peer_key)

    async def _resolve_online_session(self, session_id: str) -> Optional[OnlineSession]:
        sessions = await self._list_bindable_sessions()
        return next((s for s in sessions if s.session_id == session_id), None)

    async def _list_bindable_sessions(self) -> List[OnlineSession]:
        sessions = await self._opencode.list_online_sessions()
        return [item for item in sessions if item.workdir_available]

    async def _list_sessions_text(self) -> str:
        refresher = getattr(self._opencode, "refresh_session_titles_now", None)
        if callable(refresher):
            maybe = refresher()
            if asyncio.iscoroutine(maybe):
                await maybe
        sessions = await self._opencode.list_online_sessions()
        bindable = [item for item in sessions if item.workdir_available]
        if not bindable:
            return "当前没有可用在线 session。"
        lines = ["在线 session 列表："]
        for idx, session in enumerate(bindable, start=1):
            lines.append(self._render_session_line(idx, session))
        lines.append("发送 /bind <session_id> 进行绑定。")
        return "\n".join(lines)

    @staticmethod
    def _render_session_line(idx: int, session: OnlineSession) -> str:
        display_id = RelayService._display_session_id(session.session_id)
        return f"{idx}. {display_id} | {session.display_name}"

    def _current_binding_text(self, peer_key: str) -> str:
        bound = self._storage.get_bound_session(peer_key)
        if not bound:
            return "当前未绑定 session。"
        return f"当前绑定 session: {self._display_session_id(bound)}"

    async def _handle_file_intent(self, inbound: FeishuInbound, target: ReplyTarget) -> Optional[str]:
        if not bool(self._settings.opencode_send_files_enabled):
            return None
        if self._feishu_client is None:
            return None

        paths = await self._extract_file_paths(inbound.text)
        if not paths:
            return None

        allowed_roots = _split_csv(self._settings.opencode_file_roots)
        allowed_ext = [ext.lower().lstrip(".") for ext in _split_csv(self._settings.opencode_file_allowed_ext)]
        max_bytes = int(float(self._settings.opencode_file_max_mb) * 1024 * 1024)

        sent = []
        rejected = []
        for path in paths:
            if not _is_path_allowed(path, allowed_roots):
                rejected.append(f"{path} (路径不允许)")
                continue
            if not _is_extension_allowed(path, allowed_ext):
                rejected.append(f"{path} (类型不允许)")
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                rejected.append(f"{path} (无法读取)")
                continue
            if size > max_bytes:
                rejected.append(f"{path} (超过大小限制)")
                continue

            try:
                if _is_image(path):
                    await self._feishu_client.send_image(
                        receive_id=target.receive_id,
                        receive_id_type=target.receive_id_type,
                        image_path=path,
                    )
                else:
                    await self._feishu_client.send_file(
                        receive_id=target.receive_id,
                        receive_id_type=target.receive_id_type,
                        file_path=path,
                    )
                sent.append(path)
            except Exception as exc:
                rejected.append(f"{path} (发送失败: {exc})")

        if sent and not rejected:
            return f"已发送文件: {len(sent)} 个"
        if sent and rejected:
            return f"已发送文件: {len(sent)} 个，未发送: {len(rejected)} 个"
        if rejected:
            return "未发送文件：" + "; ".join(rejected[:3])
        return None

    async def _extract_file_paths(self, text: str) -> List[str]:
        paths = await self._extract_file_paths_via_intent_agent(text)
        if paths:
            return paths
        return _extract_paths_from_text(text)

    async def _extract_file_paths_via_intent_agent(self, text: str) -> List[str]:
        if not bool(self._settings.opencode_intent_agent_enabled):
            return []
        session_id = self._settings.opencode_intent_agent_session_id
        if not session_id:
            return []

        prompt = (
            "你是文件路径提取器。请从用户输入中提取所有本地文件路径，并仅返回 JSON 数组。"
            "如果没有路径，返回 []。不要解释。\n"
            f"用户输入: {text}"
        )

        cmd = _opencode_cmd(self._settings.opencode_bin, session_id, prompt)
        try:
            proc = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: __import__("subprocess").run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=max(3, int(self._settings.opencode_intent_agent_timeout_s)),
                ),
            )
        except Exception:
            return []

        if proc.returncode != 0:
            return []
        raw = (proc.stdout or "").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        output = []
        for item in data:
            if not isinstance(item, str):
                continue
            value = item.strip().strip('"').strip("'")
            if value:
                output.append(value)
        return output

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
            "/bind <session_id> (或 /bind <序号>) 绑定会话\n"
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

    @staticmethod
    def _display_session_id(session_id: str) -> str:
        if session_id.startswith("ses_") and len(session_id) > 9:
            return f"{session_id[:9]}..."
        return session_id


def _split_csv(value: str) -> List[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _is_path_allowed(path: str, roots: Sequence[str]) -> bool:
    if not roots:
        return False
    try:
        abs_path = os.path.abspath(path)
    except Exception:
        return False
    for root in roots:
        try:
            root_abs = os.path.abspath(root)
        except Exception:
            continue
        if abs_path.startswith(root_abs.rstrip(os.sep) + os.sep) or abs_path == root_abs:
            return True
    return False


def _is_extension_allowed(path: str, allowed: Sequence[str]) -> bool:
    if not allowed:
        return False
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return ext in {item.lower().lstrip(".") for item in allowed}


def _is_image(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return ext in {"png", "jpg", "jpeg", "gif", "bmp", "webp"}


def _extract_paths_from_text(text: str) -> List[str]:
    matches = re.findall(r"(/[^\s'\"\)\]]+)", text)
    cleaned = []
    for item in matches:
        value = item.strip().strip('"').strip("'")
        if value:
            cleaned.append(value)
    return cleaned


def _opencode_cmd(opencode_bin: str, session_id: str, prompt: str) -> List[str]:
    binary = opencode_bin if (opencode_bin and os.path.exists(opencode_bin)) else "opencode"
    return [binary, "run", "--session", session_id, "--format", "default", prompt]
