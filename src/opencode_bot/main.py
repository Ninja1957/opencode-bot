import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import time

from .api import create_server
from .config import Settings
from .feishu_client import FeishuClient
from .feishu_long_connection import FeishuLongConnectionRunner
from .opencode_client import OpenCodeClient
from .session_monitor import SessionMonitor
from .service import RelayService
from .storage import Storage


def _bootstrap_internal_session(env_key: str, enabled_key: str, prefix: str) -> None:
    env_path = Path(".env")
    env_lines: list[str] = []
    env_map: dict[str, str] = {}
    if env_path.exists():
        env_lines = env_path.read_text(encoding="utf-8").splitlines()
        for line in env_lines:
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            env_map[key.strip()] = value.strip()

    enabled_text = os.environ.get(enabled_key) or env_map.get(enabled_key, "1")
    if str(enabled_text).strip() in {"0", "false", "False", "no", "NO"}:
        return

    old_session_id = env_map.get(env_key, "").strip()
    if old_session_id:
        _kill_opencode_session_processes(old_session_id)

    new_session_id = f"{prefix}_{int(time.time())}"
    opencode_bin = (
        os.environ.get("OPENCODE_BIN")
        or env_map.get("OPENCODE_BIN")
        or "/home/SENSETIME/fuzhenxin/.opencode/bin/opencode"
    )
    if not Path(opencode_bin).exists():
        opencode_bin = "opencode"

    try:
        subprocess.Popen(
            [opencode_bin, "-s", new_session_id],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(Path.cwd()),
        )
        logging.getLogger(__name__).info("title-agent started session=%s", new_session_id)
    except Exception as exc:
        logging.getLogger(__name__).warning("title-agent start failed session=%s err=%s", new_session_id, exc)
        return

    if env_lines:
        updated = False
        output: list[str] = []
        for line in env_lines:
            if line.startswith(f"{env_key}="):
                output.append(f"{env_key}={new_session_id}")
                updated = True
            else:
                output.append(line)
        if not updated:
            output.append(f"{env_key}={new_session_id}")
        env_path.write_text("\n".join(output) + "\n", encoding="utf-8")
    else:
        env_path.write_text(f"{env_key}={new_session_id}\n", encoding="utf-8")


def _kill_opencode_session_processes(session_id: str) -> None:
    cmd = ["ps", "-eo", "pid,uid,args"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return

    uid_text = str(os.getuid())
    session_re = re.compile(rf"(?:^|\s)(?:-s|--session)(?:\s+|=){re.escape(session_id)}(?:\s|$)")
    pids: list[int] = []
    for line in proc.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid_text, uid, args = parts
        if uid != uid_text:
            continue
        if "opencode" not in args:
            continue
        if not session_re.search(args):
            continue
        try:
            pids.append(int(pid_text))
        except ValueError:
            continue

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
    time.sleep(0.4)
    for pid in pids:
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            continue


def _run_long_connection(settings: Settings) -> None:
    storage = Storage(settings.storage_path)
    opencode_client = OpenCodeClient(settings)
    feishu_client = FeishuClient(settings)
    relay_service = RelayService(
        storage=storage,
        opencode_client=opencode_client,
        settings=settings,
        feishu_client=feishu_client,
        fast_ack_s=settings.opencode_fast_ack_s,
    )
    monitor = SessionMonitor(
        settings=settings,
        opencode_client=opencode_client,
        feishu_client=feishu_client,
        storage=storage,
    )
    if settings.opencode_transport == "cli" or settings.opencode_watch_enabled:
        monitor.start()

    runner = FeishuLongConnectionRunner(
        settings=settings,
        relay_service=relay_service,
        feishu_client=feishu_client,
    )
    runner.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        runner.stop()
    finally:
        monitor.stop()


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    _bootstrap_internal_session("OPENCODE_TITLE_AGENT_SESSION_ID", "OPENCODE_TITLE_AGENT_ENABLED", "ses_title_agent")
    _bootstrap_internal_session("OPENCODE_INTENT_AGENT_SESSION_ID", "OPENCODE_INTENT_AGENT_ENABLED", "ses_intent_agent")
    settings = Settings.load()
    if settings.feishu_event_mode == "long_conn":
        _run_long_connection(settings)
        return

    server = create_server(settings)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    finally:
        monitor = getattr(server, "session_monitor", None)
        if monitor is not None:
            monitor.stop()


if __name__ == "__main__":
    run()
