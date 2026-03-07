from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Dict


def _load_env_file(env_path: Path) -> Dict[str, str]:
    if not env_path.exists():
        return {}
    data: Dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        if raw.startswith("export "):
            raw = raw[len("export ") :].strip()
            if "=" not in raw:
                continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            data[key] = value
    return data


def _load_dotenv() -> Dict[str, str]:
    merged: Dict[str, str] = {}
    candidates = [Path("config/bot.env"), Path(".env")]

    custom_path = os.getenv("BOT_CONFIG_PATH", "").strip()
    if custom_path:
        candidates.append(Path(custom_path))

    for path in candidates:
        values = _load_env_file(path)
        if values:
            merged.update(values)
    return merged


@dataclass
class Settings:
    host: str
    port: int
    storage_path: str
    feishu_app_id: str
    feishu_app_secret: str
    feishu_verify_token: str
    feishu_event_mode: str
    feishu_encrypt_key: str
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
        file_env = _load_dotenv()

        def get(name: str, default: str) -> str:
            return os.getenv(name, file_env.get(name, default))

        return Settings(
            host=get("BOT_HOST", "0.0.0.0"),
            port=int(get("BOT_PORT", "8080")),
            storage_path=get("BOT_STORAGE_PATH", "./data/opencode_bot.db"),
            feishu_app_id=get("FEISHU_APP_ID", ""),
            feishu_app_secret=get("FEISHU_APP_SECRET", ""),
            feishu_verify_token=get("FEISHU_VERIFY_TOKEN", ""),
            feishu_event_mode=get("FEISHU_EVENT_MODE", "http"),
            feishu_encrypt_key=get("FEISHU_ENCRYPT_KEY", ""),
            opencode_base_url=get("OPENCODE_BASE_URL", "http://127.0.0.1:4096"),
            opencode_transport=get("OPENCODE_TRANSPORT", "cli"),
            opencode_bin=get("OPENCODE_BIN", os.path.expanduser("~/.opencode/bin/opencode")),
            opencode_db_path=get(
                "OPENCODE_DB_PATH",
                os.path.expanduser("~/.local/share/opencode/opencode.db"),
            ),
            opencode_list_sessions_path=get("OPENCODE_LIST_SESSIONS_PATH", "/api/sessions/list"),
            opencode_list_sessions_path_alt=get("OPENCODE_LIST_SESSIONS_PATH_ALT", "/api/claw/sessions/list"),
            opencode_send_message_path=get("OPENCODE_SEND_MESSAGE_PATH", "/api/sessions/send"),
            opencode_send_message_path_alt=get("OPENCODE_SEND_MESSAGE_PATH_ALT", "/api/claw/sessions/send"),
            opencode_api_key=get("OPENCODE_API_KEY", ""),
            opencode_request_timeout_s=float(get("OPENCODE_REQUEST_TIMEOUT_S", "30")),
            opencode_watch_enabled=int(get("OPENCODE_WATCH_ENABLED", "0")),
            opencode_watch_interval_s=float(get("OPENCODE_WATCH_INTERVAL_S", "5")),
            opencode_watch_include_assistant=int(get("OPENCODE_WATCH_INCLUDE_ASSISTANT", "1")),
            opencode_watch_include_user=int(get("OPENCODE_WATCH_INCLUDE_USER", "1")),
            feishu_notify_receive_id=get("FEISHU_NOTIFY_RECEIVE_ID", ""),
            feishu_notify_receive_id_type=get("FEISHU_NOTIFY_RECEIVE_ID_TYPE", "chat_id"),
        )
