"""Start the local SheetFlow service, or open the dashboard if it is already running."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}/"


def data_dir() -> Path:
    override = os.environ.get("SHEETFLOW_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SheetFlow"
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        root = Path(local) if local else Path.home() / "AppData" / "Local"
        return root / "SheetFlow"
    return Path.home() / ".local" / "share" / "SheetFlow"


def _backend_root() -> Path:
    here = Path(__file__).resolve().parent
    if (here / "app").is_dir():
        return here
    bundled = here / "backend"
    if (bundled / "app").is_dir():
        return bundled
    return here.parent / "backend"


def _acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return None
        return handle
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _port_open() -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((HOST, PORT)) == 0


def _open_when_ready() -> None:
    for _ in range(100):
        if _port_open():
            webbrowser.open(URL)
            return
        time.sleep(0.1)


def _log_to_file(path: Path) -> None:
    if sys.stdout.isatty():
        return
    handle = path.open("a", encoding="utf-8")
    sys.stdout = handle
    sys.stderr = handle


def main() -> int:
    data = data_dir()
    data.mkdir(parents=True, exist_ok=True)
    lock = _acquire_lock(data / "sheetflow.lock")
    if lock is None:
        for _ in range(50):
            if _port_open():
                webbrowser.open(URL)
                return 0
            time.sleep(0.1)
        print("SheetFlow is already starting. If the page does not open, quit it from the Dock and try again.", file=sys.stderr)
        return 1

    os.environ["SHEETFLOW_DATA_DIR"] = str(data)
    os.environ["DATABASE_URL"] = "sqlite:///" + (data / "sheetflow.db").resolve().as_posix()
    os.environ["STORAGE_DIR"] = str(data / "storage")
    (data / "storage").mkdir(parents=True, exist_ok=True)
    _log_to_file(data / "sheetflow.log")

    backend = _backend_root()
    sys.path.insert(0, str(backend))
    from alembic import command
    from alembic.config import Config

    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(config, "head")

    import uvicorn

    threading.Thread(target=_open_when_ready, daemon=True).start()
    server = uvicorn.Server(uvicorn.Config("app.main:app", host=HOST, port=PORT, log_level="info"))
    try:
        server.run()
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
