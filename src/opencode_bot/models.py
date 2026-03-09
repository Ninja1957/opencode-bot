from dataclasses import dataclass
from typing import Optional


@dataclass
class OnlineSession:
    session_id: str
    display_name: str
    status: str
    pid: Optional[int] = None
    tty: str = ""
    last_seen_ts: int = 0
    directory: str = ""


@dataclass
class FeishuInbound:
    message_id: str
    chat_id: str
    open_id: str
    chat_type: str
    text: str

    @property
    def peer_key(self) -> str:
        if self.chat_type == "group" and self.chat_id:
            return f"chat:{self.chat_id}"
        return f"user:{self.open_id}"
