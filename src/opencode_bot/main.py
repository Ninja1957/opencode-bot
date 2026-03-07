from .api import create_server
from .config import Settings


def run() -> None:
    settings = Settings.load()
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
