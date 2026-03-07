from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass
class Settings:
    host: str
    port: int
    storage_path: str
    feishu_app_id: str
    feishu_app_secret: str
    feishu_verify_token: str
    opencode_base_url: str
    opencode_transport: str
    opencode_bin: str
    opencode_db_path: str
    opencode_list_sessions_path: str
    opencode_list_sessions_path_alt: str
    opencode_send_message_path: str
    opencode_send_message_path_alt: str
    opencode_api_key: str
    opencode_request_timeout_s: float
    opencode_watch_enabled: int
    opencode_watch_interval_s: float
    opencode_watch_include_assistant: int
    opencode_watch_include_user: int
    feishu_notify_receive_id: str
    feishu_notify_receive_id_type: str

    @staticmethod
    def load() -> "Settings":
        return Settings(
            host=os.getenv("BOT_HOST", "0.0.0.0"),
            port=int(os.getenv("BOT_PORT", "8080")),
            storage_path=os.getenv("BOT_STORAGE_PATH", "./data/opencode_bot.db"),
            feishu_app_id=os.getenv("FEISHU_APP_ID", ""),
            feishu_app_secret=os.getenv("FEISHU_APP_SECRET", ""),
            feishu_verify_token=os.getenv("FEISHU_VERIFY_TOKEN", ""),
            opencode_base_url=os.getenv("OPENCODE_BASE_URL", "http://127.0.0.1:4096"),
            opencode_transport=os.getenv("OPENCODE_TRANSPORT", "cli"),
            opencode_bin=os.getenv("OPENCODE_BIN", os.path.expanduser("~/.opencode/bin/opencode")),
            opencode_db_path=os.getenv(
                "OPENCODE_DB_PATH",
                os.path.expanduser("~/.local/share/opencode/opencode.db"),
            ),
            opencode_list_sessions_path=os.getenv("OPENCODE_LIST_SESSIONS_PATH", "/api/sessions/list"),
            opencode_list_sessions_path_alt=os.getenv("OPENCODE_LIST_SESSIONS_PATH_ALT", "/api/claw/sessions/list"),
            opencode_send_message_path=os.getenv("OPENCODE_SEND_MESSAGE_PATH", "/api/sessions/send"),
            opencode_send_message_path_alt=os.getenv("OPENCODE_SEND_MESSAGE_PATH_ALT", "/api/claw/sessions/send"),
            opencode_api_key=os.getenv("OPENCODE_API_KEY", ""),
            opencode_request_timeout_s=float(os.getenv("OPENCODE_REQUEST_TIMEOUT_S", "30")),
            opencode_watch_enabled=int(os.getenv("OPENCODE_WATCH_ENABLED", "0")),
            opencode_watch_interval_s=float(os.getenv("OPENCODE_WATCH_INTERVAL_S", "5")),
            opencode_watch_include_assistant=int(os.getenv("OPENCODE_WATCH_INCLUDE_ASSISTANT", "1")),
            opencode_watch_include_user=int(os.getenv("OPENCODE_WATCH_INCLUDE_USER", "1")),
            feishu_notify_receive_id=os.getenv("FEISHU_NOTIFY_RECEIVE_ID", ""),
            feishu_notify_receive_id_type=os.getenv("FEISHU_NOTIFY_RECEIVE_ID_TYPE", "chat_id"),
        )
