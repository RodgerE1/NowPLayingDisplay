"""Windows system-tray controller for the ESP32 Room + PC Display sender."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError as exc:
    pystray = None
    Image = None
    ImageDraw = None
    IMPORT_ERROR: Exception | None = exc
else:
    IMPORT_ERROR = None


PROJECT_FOLDER = Path(__file__).resolve().parent
PYTHONW_EXECUTABLE = PROJECT_FOLDER / ".venv" / "Scripts" / "pythonw.exe"
SENDER_SCRIPT = PROJECT_FOLDER / "pc_sender.py"
LOG_FILE = PROJECT_FOLDER / "pc_sender.log"

MUTEX_NAME = "Local\\ESP32RoomPCDisplayTray_Rodger"
ERROR_ALREADY_EXISTS = 183
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_mutex_handle: int | None = None
_sender_process: subprocess.Popen[Any] | None = None
_process_lock = threading.RLock()
_shutdown_event = threading.Event()
_tray_icon: Any = None


def show_message(
    message: str,
    title: str = "ESP32 Room + PC Display",
    error: bool = False,
) -> None:
    """Display a message even though this program runs under pythonw.exe."""
    if sys.platform == "win32":
        icon_flag = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, title, icon_flag)


def acquire_single_instance() -> bool:
    """Prevent duplicate tray icons and duplicate sender processes."""
    global _mutex_handle

    if sys.platform != "win32":
        return True

    kernel32 = ctypes.windll.kernel32
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    create_mutex.restype = ctypes.c_void_p

    handle = create_mutex(None, False, MUTEX_NAME)
    if not handle:
        show_message("Windows could not create the tray-app lock.", error=True)
        return False

    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        show_message("The ESP32 display tray app is already running.")
        return False

    _mutex_handle = int(handle)
    return True


def release_single_instance() -> None:
    global _mutex_handle

    if sys.platform == "win32" and _mutex_handle is not None:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(_mutex_handle))
    _mutex_handle = None


def make_tray_image() -> Any:
    """Create a small monitor icon without requiring a separate image file."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        (3, 4, 61, 51),
        radius=8,
        fill=(17, 25, 35, 255),
        outline=(84, 230, 223, 255),
        width=3,
    )
    draw.rounded_rectangle(
        (10, 11, 54, 43),
        radius=3,
        fill=(21, 40, 57, 255),
        outline=(158, 179, 194, 255),
        width=2,
    )
    draw.line(
        (14, 35, 22, 27, 29, 33, 39, 19, 50, 25),
        fill=(84, 230, 223, 255),
        width=3,
    )
    draw.polygon((44, 15, 44, 28, 53, 21), fill=(255, 193, 90, 255))
    draw.rectangle((28, 52, 36, 58), fill=(158, 179, 194, 255))
    draw.rounded_rectangle(
        (19, 57, 45, 61), radius=2, fill=(158, 179, 194, 255)
    )
    return image


def sender_is_running() -> bool:
    with _process_lock:
        return _sender_process is not None and _sender_process.poll() is None


def update_tray_status() -> None:
    if _tray_icon is None:
        return

    running = sender_is_running()
    _tray_icon.title = (
        "ESP32 Room + PC Display - Running"
        if running
        else "ESP32 Room + PC Display - Sender stopped"
    )
    try:
        _tray_icon.update_menu()
    except (AttributeError, RuntimeError):
        pass


def start_sender() -> bool:
    global _sender_process

    with _process_lock:
        if _sender_process is not None and _sender_process.poll() is None:
            return True

        if not PYTHONW_EXECUTABLE.exists():
            show_message(
                "Python environment not found.\n\n"
                "Run 1_INSTALL_PC_SENDER.bat first.",
                error=True,
            )
            return False

        if not SENDER_SCRIPT.exists():
            show_message("pc_sender.py was not found.", error=True)
            return False

        try:
            _sender_process = subprocess.Popen(
                [str(PYTHONW_EXECUTABLE), str(SENDER_SCRIPT)],
                cwd=str(PROJECT_FOLDER),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
                close_fds=True,
            )
        except OSError as exc:
            _sender_process = None
            show_message(f"Could not start the sender:\n\n{exc}", error=True)
            return False

    update_tray_status()
    return True


def stop_sender() -> None:
    global _sender_process

    with _process_lock:
        process = _sender_process
        _sender_process = None

    if process is None or process.poll() is not None:
        update_tray_status()
        return

    process.terminate()
    try:
        process.wait(timeout=4.0)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass

    update_tray_status()


def restart_sender() -> None:
    stop_sender()
    time.sleep(0.25)
    start_sender()


def status_menu_text(_item: Any) -> str:
    return "Status: Running" if sender_is_running() else "Status: Stopped"


def start_restart_menu_text(_item: Any) -> str:
    return "Restart sender" if sender_is_running() else "Start sender"


def open_log(_icon: Any = None, _item: Any = None) -> None:
    try:
        LOG_FILE.touch(exist_ok=True)
        subprocess.Popen(["notepad.exe", str(LOG_FILE)], close_fds=True)
    except OSError as exc:
        show_message(f"Could not open the log:\n\n{exc}", error=True)


def open_project_folder(_icon: Any = None, _item: Any = None) -> None:
    try:
        os.startfile(PROJECT_FOLDER)  # type: ignore[attr-defined]
    except OSError as exc:
        show_message(f"Could not open the project folder:\n\n{exc}", error=True)


def start_or_restart(_icon: Any = None, _item: Any = None) -> None:
    target = restart_sender if sender_is_running() else start_sender
    threading.Thread(target=target, daemon=True).start()


def stop_and_exit(icon: Any, _item: Any = None) -> None:
    _shutdown_event.set()
    stop_sender()
    icon.stop()


def monitor_sender() -> None:
    previous_running = sender_is_running()

    while not _shutdown_event.wait(1.0):
        running = sender_is_running()
        if running == previous_running:
            continue

        update_tray_status()
        if previous_running and not running and _tray_icon is not None:
            try:
                _tray_icon.notify(
                    "The display sender stopped. Right-click the tray icon "
                    "to start it again.",
                    "ESP32 Room + PC Display",
                )
            except (AttributeError, NotImplementedError, RuntimeError):
                pass
        previous_running = running


def build_menu() -> Any:
    return pystray.Menu(
        pystray.MenuItem(status_menu_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("View log", open_log, default=True),
        pystray.MenuItem("Open project folder", open_project_folder),
        pystray.MenuItem(start_restart_menu_text, start_or_restart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Stop and exit", stop_and_exit),
    )


def setup_tray(icon: Any) -> None:
    """Finish startup after Windows has created the notification-area icon."""
    icon.visible = True
    start_sender()
    threading.Thread(target=monitor_sender, daemon=True).start()


def main() -> int:
    global _tray_icon

    if IMPORT_ERROR is not None or pystray is None:
        show_message(
            "The system-tray package is not installed.\n\n"
            "Run 1_INSTALL_PC_SENDER.bat, then try again.",
            error=True,
        )
        return 1

    if not acquire_single_instance():
        return 0

    try:
        _tray_icon = pystray.Icon(
            "ESP32RoomPCDisplay",
            make_tray_image(),
            "ESP32 Room + PC Display",
            build_menu(),
        )

        _tray_icon.run(setup=setup_tray)
        return 0
    finally:
        _shutdown_event.set()
        stop_sender()
        release_single_instance()


if __name__ == "__main__":
    raise SystemExit(main())
