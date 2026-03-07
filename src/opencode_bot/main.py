import logging
import time

from .api import create_server
from .config import Settings
from .feishu_client import FeishuClient
from .feishu_long_connection import FeishuLongConnectionRunner
from .opencode_client import OpenCodeClient
from .session_monitor import SessionMonitor
from .service import RelayService
from .storage import Storage


def _run_long_connection(settings: Settings) -> None:
    storage = Storage(settings.storage_path)
    opencode_client = OpenCodeClient(settings)
    relay_service = RelayService(storage=storage, opencode_client=opencode_client)
    feishu_client = FeishuClient(settings)
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
