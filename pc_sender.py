"""
Windows companion for the ESP32 Room + PC Display.

It sends these values to the ESP32 every two seconds:
  - CPU package temperature from LibreHardwareMonitor's local web server
  - NVIDIA GPU temperature from nvidia-smi
  - seven system-fan RPM readings from LibreHardwareMonitor
  - two GPU-fan RPM readings from LibreHardwareMonitor
  - up to three SSD composite temperatures from LibreHardwareMonitor
  - the current Windows media-session title, artist, source, and timeline

No readings are sent to the internet by this program. Communication is only
between this PC and the ESP32 on the local network.
"""

from __future__ import annotations

import asyncio
import ctypes
import html
import json
import logging
import re
import socket
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# --------------------------- User settings ---------------------------

# Leave blank for automatic UDP discovery. If discovery does not work, put
# the IP shown on the ESP32 here, for example: "192.168.1.123"
DISPLAY_IP = ""

from pc_secrets import DISPLAY_API_KEY
SEND_INTERVAL_SECONDS = 2.0
UPDATE_TIMEOUT_SECONDS = 5.0
UPDATE_RETRY_DELAY_SECONDS = 0.35
REDISCOVERY_FAILURE_THRESHOLD = 6
UNEXPECTED_RESTART_DELAY_SECONDS = 3.0

# Leave this blank to automatically try localhost and every active local IPv4
# address. If automatic detection ever fails, enter the address shown in
# LibreHardwareMonitor's Set Port window, for example: "10.0.0.64"
LIBRE_HARDWARE_MONITOR_HOST = ""
LIBRE_HARDWARE_MONITOR_PORT = 8085


# --------------------------- Protocol values -------------------------

DISCOVERY_PORT = 4210
DISCOVERY_REQUEST = b"CYD_ROOM_DISPLAY_DISCOVER_V1"
DISCOVERY_REPLY = b"CYD_ROOM_DISPLAY_V1"


try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
    )
except ImportError:
    MediaManager = None
    PlaybackStatus = None

SYSTEM_FAN_COUNT = 7
GPU_FAN_COUNT = 2
STORAGE_TEMPERATURE_COUNT = 3
LOG_FILE = Path(__file__).resolve().with_name("pc_sender.log")
LOG_MAX_ENTRIES = 1000
LOG_RETAIN_ENTRIES = 800
SENDER_MUTEX_NAME = "Local\\ESP32RoomPCDisplaySender_Rodger"
ERROR_ALREADY_EXISTS = 183


LOGGER = logging.getLogger("room_pc_display")
_sender_mutex_handle: int | None = None


def acquire_sender_instance() -> bool:
    """Allow only one copy of pc_sender.py in this Windows session."""
    global _sender_mutex_handle

    if sys.platform != "win32":
        return True

    kernel32 = ctypes.windll.kernel32
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    create_mutex.restype = ctypes.c_void_p

    handle = create_mutex(None, False, SENDER_MUTEX_NAME)
    if not handle:
        return False

    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False

    _sender_mutex_handle = int(handle)
    return True


def release_sender_instance() -> None:
    global _sender_mutex_handle

    if sys.platform == "win32" and _sender_mutex_handle is not None:
        ctypes.windll.kernel32.CloseHandle(
            ctypes.c_void_p(_sender_mutex_handle)
        )
    _sender_mutex_handle = None


class EntryCappedFileHandler(logging.FileHandler):
    """Keep one log file containing only a bounded number of recent entries."""

    def __init__(
        self,
        filename: Path,
        max_entries: int,
        retain_entries: int,
    ) -> None:
        self.log_path = Path(filename)
        self.max_entries = max(1, int(max_entries))
        self.retain_entries = max(
            1, min(int(retain_entries), self.max_entries)
        )
        self.entry_count = self._prune_existing_log()
        super().__init__(self.log_path, mode="a", encoding="utf-8")

    def _recent_lines(self) -> tuple[int, deque[str]]:
        recent: deque[str] = deque(maxlen=self.retain_entries)
        total = 0

        if not self.log_path.exists():
            return total, recent

        with self.log_path.open(
            "r", encoding="utf-8", errors="replace"
        ) as source:
            for line in source:
                total += 1
                recent.append(line)

        return total, recent

    def _rewrite_recent_lines(self, recent: deque[str]) -> int:
        with self.log_path.open("w", encoding="utf-8", newline="") as output:
            output.writelines(recent)
        return len(recent)

    def _prune_existing_log(self) -> int:
        try:
            total, recent = self._recent_lines()
            if total > self.max_entries:
                return self._rewrite_recent_lines(recent)
            return total
        except OSError:
            # Logging must never stop the sensor sender from running.
            return 0

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.entry_count += 1

        if self.entry_count <= self.max_entries:
            return

        try:
            self.flush()
            if self.stream is not None:
                self.stream.close()
                self.stream = None

            _, recent = self._recent_lines()
            self.entry_count = self._rewrite_recent_lines(recent)
        except OSError:
            self.handleError(record)
        finally:
            if self.stream is None:
                self.stream = self._open()


def configure_logging() -> None:
    """Log important events while retaining only the newest entries."""
    if LOGGER.handlers:
        return

    LOGGER.setLevel(logging.DEBUG)
    LOGGER.propagate = False

    # Remove backup files created by versions that rotated logs by byte size.
    for legacy_backup in ("pc_sender.log.1", "pc_sender.log.2"):
        try:
            LOG_FILE.with_name(legacy_backup).unlink(missing_ok=True)
        except OSError:
            pass

    file_handler = EntryCappedFileHandler(
        LOG_FILE,
        max_entries=LOG_MAX_ENTRIES,
        retain_entries=LOG_RETAIN_ENTRIES,
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    LOGGER.addHandler(file_handler)

    if sys.stdout is not None:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        LOGGER.addHandler(console_handler)


@dataclass
class MediaState:
    title: str = "Nothing playing"
    artist: str = ""
    source: str = "PC"
    playing: bool = False
    position: int = 0
    duration: int = 0
    album: str = ""
    is_spotify: bool = False


@dataclass
class StorageTemperature:
    name: str
    temperature_c: float | None


@dataclass
class HardwareState:
    cpu_temperature_c: float | None = None
    gpu_temperature_c: float | None = None
    system_fan_rpm: list[int | None] = field(
        default_factory=lambda: [None] * SYSTEM_FAN_COUNT
    )
    gpu_fan_rpm: list[int | None] = field(
        default_factory=lambda: [None] * GPU_FAN_COUNT
    )
    storage_temperatures: list[StorageTemperature] = field(default_factory=list)


def ascii_text(value: Any, maximum_length: int) -> str:
    """Make Windows media text safe for TFT_eSPI's built-in fonts."""
    if value is None:
        return ""

    text = html.unescape(str(value))
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", errors="ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:maximum_length]


def timespan_seconds(value: Any) -> int:
    """Convert either a datetime.timedelta or a WinRT TimeSpan to seconds."""
    if value is None:
        return 0

    if hasattr(value, "total_seconds"):
        return max(0, int(value.total_seconds()))

    if hasattr(value, "duration"):
        # WinRT TimeSpan.duration is measured in 100-nanosecond ticks.
        return max(0, int(value.duration / 10_000_000))

    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def friendly_source(app_id: str) -> str:
    lowered = app_id.lower()
    known_sources = (
        ("operagx", "Opera GX"),
        ("opera", "Opera GX"),
        ("spotify", "Spotify"),
        ("chrome", "Chrome"),
        ("firefox", "Firefox"),
        ("msedge", "Edge"),
        ("vlc", "VLC"),
        ("foobar", "foobar2000"),
        ("itunes", "iTunes"),
        ("musicbee", "MusicBee"),
    )

    for needle, label in known_sources:
        if needle in lowered:
            return label

    cleaned = app_id.rsplit("!", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = re.sub(r"\.exe$", "", cleaned, flags=re.IGNORECASE)
    return ascii_text(cleaned, 32) or "PC"


async def read_media_state(manager: Any) -> MediaState:
    if manager is None:
        return MediaState(artist="Windows media support is unavailable")

    try:
        session = manager.get_current_session()
        if session is None:
            return MediaState()

        properties = await session.try_get_media_properties_async()
        playback_info = session.get_playback_info()
        timeline = session.get_timeline_properties()

        title = ascii_text(getattr(properties, "title", ""), 120)
        artist = ascii_text(getattr(properties, "artist", ""), 100)
        album = ascii_text(getattr(properties, "album_title", ""), 100)

        if not artist and album:
            artist = album

        raw_app_id = str(session.source_app_user_model_id or "")
        app_id = ascii_text(raw_app_id, 100)
        source = friendly_source(app_id)
        is_spotify = "spotify" in raw_app_id.lower()

        status = getattr(playback_info, "playback_status", None)
        playing = status == PlaybackStatus.PLAYING

        start = timespan_seconds(getattr(timeline, "start_time", None))
        end = timespan_seconds(getattr(timeline, "end_time", None))
        position = timespan_seconds(getattr(timeline, "position", None))
        duration = max(0, end - start)
        position = max(0, position - start)

        return MediaState(
            title=title or "Nothing playing",
            artist=artist,
            source=source,
            playing=playing,
            position=position,
            duration=duration,
            album=album,
            is_spotify=is_spotify,
        )
    except Exception as exc:  # A media app can vanish while it is queried.
        return MediaState(artist=f"Media read error: {type(exc).__name__}")


def parse_temperature(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        parsed = float(value)
    else:
        match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
        if match is None:
            return None
        parsed = float(match.group(0))

    if -30.0 <= parsed <= 150.0:
        return parsed
    return None


def parse_rpm(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        parsed = float(value)
    else:
        match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
        if match is None:
            return None
        parsed = float(match.group(0))

    if 0.0 <= parsed <= 50_000.0:
        return int(round(parsed))
    return None


def sensor_value(node: dict[str, Any], parser: Any) -> Any | None:
    for key in ("RawValue", "Value"):
        if key in node:
            parsed = parser(node[key])
            if parsed is not None:
                return parsed
    return None


def walk_sensor_tree(node: Any, ancestors: tuple[str, ...] = ()):
    if not isinstance(node, dict):
        return

    name = str(node.get("Text", ""))
    current_path = ancestors + ((name,) if name else ())
    yield node, current_path

    for child in node.get("Children", []) or []:
        yield from walk_sensor_tree(child, current_path)


_working_lhm_url: str | None = None


def libre_hardware_monitor_urls() -> list[str]:
    """Return local LHM addresses, including the active LAN interface."""
    manual_host = LIBRE_HARDWARE_MONITOR_HOST.strip()
    if manual_host:
        if manual_host.startswith("http://") or manual_host.startswith("https://"):
            base = manual_host.rstrip("/")
            if base.endswith("/data.json"):
                return [base]
            return [base + "/data.json"]
        return [
            f"http://{manual_host}:{LIBRE_HARDWARE_MONITOR_PORT}/data.json"
        ]

    hosts: list[str] = ["127.0.0.1", "localhost"]

    try:
        for result in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM
        ):
            hosts.append(result[4][0])
    except OSError:
        pass

    # A UDP connect selects the computer's active IPv4 interface without
    # transmitting any data. This catches PCs whose hostname resolves only
    # to localhost.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route_socket:
            route_socket.connect(("192.0.2.1", 9))
            hosts.append(route_socket.getsockname()[0])
    except OSError:
        pass

    unique_hosts = list(dict.fromkeys(host for host in hosts if host))
    return [
        f"http://{host}:{LIBRE_HARDWARE_MONITOR_PORT}/data.json"
        for host in unique_hosts
    ]


def read_lhm_sensor_data() -> Any | None:
    global _working_lhm_url

    candidates = libre_hardware_monitor_urls()
    if _working_lhm_url:
        candidates = [_working_lhm_url] + [
            url for url in candidates if url != _working_lhm_url
        ]

    for url in candidates:
        try:
            with urllib.request.urlopen(url, timeout=0.6) as response:
                data = json.load(response)
            _working_lhm_url = url
            return data
        except (OSError, ValueError, urllib.error.URLError):
            continue

    _working_lhm_url = None
    return None


def is_storage_device_name(name: str) -> bool:
    lowered = name.lower()
    storage_markers = (
        "ssd",
        "nvme",
        "990 evo",
        "mp700",
        "solid state",
        "pcie",
    )
    return any(marker in lowered for marker in storage_markers)


def concise_storage_name(name: str) -> str:
    upper = name.upper()
    if "990 EVO" in upper:
        return "990 EVO+"
    if "MP700" in upper:
        return "MP700"
    if name.strip().lower() == "pcie ssd":
        return "PCIe SSD"

    cleaned = re.sub(
        r"\b(SAMSUNG|CORSAIR|SOLID STATE DRIVE|SSD)\b",
        "",
        name,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return ascii_text(cleaned or name, 18)


def read_lhm_hardware_state() -> HardwareState:
    """Read the exact CPU Package sensor plus fan and SSD monitoring data."""
    state = HardwareState()
    data = read_lhm_sensor_data()
    if data is None:
        return state

    seen_storage_paths: set[tuple[str, ...]] = set()

    for node, path in walk_sensor_tree(data):
        name = str(node.get("Text", "")).strip()
        lowered_name = name.lower()
        lowered_path = tuple(part.lower() for part in path)

        # Rodge selected the exact LibreHardwareMonitor CPU Package reading.
        # Do not substitute Core Max, individual cores, or Tctl/Tdie.
        if (
            state.cpu_temperature_c is None
            and lowered_name == "cpu package"
            and "temperatures" in lowered_path
        ):
            state.cpu_temperature_c = sensor_value(node, parse_temperature)

        gpu_fan_match = re.fullmatch(
            r"gpu\s*fan\s*#?\s*(\d+)", name, flags=re.IGNORECASE
        )
        if gpu_fan_match and "fans" in lowered_path:
            index = int(gpu_fan_match.group(1)) - 1
            if 0 <= index < GPU_FAN_COUNT:
                state.gpu_fan_rpm[index] = sensor_value(node, parse_rpm)
            continue

        system_fan_match = re.fullmatch(
            r"fan\s*#\s*(\d+)", name, flags=re.IGNORECASE
        )
        if system_fan_match and "fans" in lowered_path:
            path_text = " / ".join(lowered_path)
            if not any(word in path_text for word in ("gpu", "nvidia", "radeon")):
                index = int(system_fan_match.group(1)) - 1
                if 0 <= index < SYSTEM_FAN_COUNT:
                    state.system_fan_rpm[index] = sensor_value(node, parse_rpm)
            continue

        if lowered_name != "composite temperature" or "temperatures" not in lowered_path:
            continue

        temperature_category_index = max(
            index
            for index, part in enumerate(lowered_path)
            if part == "temperatures"
        )
        if temperature_category_index == 0:
            continue

        device_name = path[temperature_category_index - 1]
        device_path = path[:temperature_category_index]
        if (
            device_path in seen_storage_paths
            or not is_storage_device_name(device_name)
        ):
            continue

        temperature = sensor_value(node, parse_temperature)
        if temperature is None:
            continue

        seen_storage_paths.add(device_path)
        state.storage_temperatures.append(
            StorageTemperature(
                name=concise_storage_name(device_name),
                temperature_c=temperature,
            )
        )

    state.storage_temperatures = state.storage_temperatures[
        :STORAGE_TEMPERATURE_COUNT
    ]
    return state


def read_nvidia_gpu_temperature() -> float | None:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    first_line = result.stdout.strip().splitlines()
    return parse_temperature(first_line[0]) if first_line else None


def discover_display(timeout: float = 1.25) -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        sock.bind(("", 0))

        for broadcast_address in ("255.255.255.255",):
            try:
                sock.sendto(DISCOVERY_REQUEST, (broadcast_address, DISCOVERY_PORT))
            except OSError:
                continue

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, address = sock.recvfrom(256)
            except socket.timeout:
                break
            if data.startswith(DISCOVERY_REPLY):
                return f"http://{address[0]}/update"
    return None


def configured_display_url() -> str | None:
    ip = DISPLAY_IP.strip()
    if not ip:
        return None

    if ip.startswith("http://") or ip.startswith("https://"):
        return ip.rstrip("/") + "/update"
    return f"http://{ip}/update"


def send_update(
    url: str,
    hardware: HardwareState,
    media: MediaState,
) -> None:
    # Browser and video-player timelines are intentionally hidden. Only the
    # Spotify desktop session sends position and duration to the display.
    position = media.position if media.is_spotify else 0
    duration = media.duration if media.is_spotify else 0

    payload = {
        "key": DISPLAY_API_KEY,
        "cpu": "" if hardware.cpu_temperature_c is None
        else f"{hardware.cpu_temperature_c:.1f}",
        "gpu": "" if hardware.gpu_temperature_c is None
        else f"{hardware.gpu_temperature_c:.1f}",
        "title": media.title,
        "artist": media.artist,
        "source": media.source,
        "playing": "1" if media.playing else "0",
        "position": str(position),
        "duration": str(duration),
    }

    for index in range(SYSTEM_FAN_COUNT):
        rpm = hardware.system_fan_rpm[index]
        payload[f"fan{index + 1}"] = "" if rpm is None else str(rpm)

    for index in range(GPU_FAN_COUNT):
        rpm = hardware.gpu_fan_rpm[index]
        payload[f"gfan{index + 1}"] = "" if rpm is None else str(rpm)

    for index in range(STORAGE_TEMPERATURE_COUNT):
        if index < len(hardware.storage_temperatures):
            storage = hardware.storage_temperatures[index]
            payload[f"ssd{index + 1}name"] = storage.name
            payload[f"ssd{index + 1}"] = (
                "" if storage.temperature_c is None
                else f"{storage.temperature_c:.1f}"
            )
        else:
            payload[f"ssd{index + 1}name"] = f"SSD {index + 1}"
            payload[f"ssd{index + 1}"] = ""

    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    with urllib.request.urlopen(
        request, timeout=UPDATE_TIMEOUT_SECONDS
    ) as response:
        if response.status != 200:
            raise RuntimeError(f"ESP32 returned HTTP {response.status}")


def send_update_with_retry(
    url: str,
    hardware: HardwareState,
    media: MediaState,
) -> None:
    """Retry one failed display update before reporting a failed cycle."""
    try:
        send_update(url, hardware, media)
        return
    except Exception as first_error:
        LOGGER.warning("Update attempt failed; retrying once: %s", first_error)

    time.sleep(UPDATE_RETRY_DELAY_SECONDS)
    send_update(url, hardware, media)
    LOGGER.info("Update retry succeeded.")


def temperature_text(value: float | None) -> str:
    return "--" if value is None else f"{value:.0f} C"


async def main() -> int:
    configure_logging()
    LOGGER.info("ESP32 Room + PC Display companion")
    LOGGER.info("Press Ctrl+C to stop.")
    LOGGER.info("Log file: %s", LOG_FILE)
    LOGGER.info(
        "Log limit: %d entries; oldest entries are pruned automatically.",
        LOG_MAX_ENTRIES,
    )

    if MediaManager is None:
        LOGGER.warning("Windows media package is missing.")
        LOGGER.warning(
            "Run 1_INSTALL_PC_SENDER.bat, then start this program again."
        )
        media_manager = None
    else:
        try:
            media_manager = await MediaManager.request_async()
        except Exception as exc:
            LOGGER.warning("Windows media service could not start: %s", exc)
            media_manager = None

    display_url = configured_display_url()
    consecutive_failures = 0
    last_cpu_warning = 0.0

    while True:
        cycle_started = time.monotonic()

        if display_url is None:
            LOGGER.info("Looking for the ESP32 display...")
            display_url = discover_display()
            if display_url is None:
                LOGGER.warning(
                    "Display not found. Retrying; its IP can also be set "
                    "at the top of pc_sender.py."
                )
                await asyncio.sleep(3.0)
                continue
            LOGGER.info(
                "Found display at %s", display_url.rsplit("/update", 1)[0]
            )

        hardware = await asyncio.to_thread(read_lhm_hardware_state)
        hardware.gpu_temperature_c = await asyncio.to_thread(
            read_nvidia_gpu_temperature
        )
        media = await read_media_state(media_manager)

        if (
            hardware.cpu_temperature_c is None
            and time.monotonic() - last_cpu_warning >= 60.0
        ):
            LOGGER.warning(
                "CPU Package unavailable: start LibreHardwareMonitor, enable "
                "its Remote Web Server, and confirm CPU Package is visible."
            )
            last_cpu_warning = time.monotonic()

        try:
            await asyncio.to_thread(
                send_update_with_retry,
                display_url,
                hardware,
                media,
            )
            consecutive_failures = 0
            state = "playing" if media.playing else "paused/idle"
            LOGGER.debug(
                f"CPU {temperature_text(hardware.cpu_temperature_c):>5} | "
                f"GPU {temperature_text(hardware.gpu_temperature_c):>5} | "
                f"{state:<11} | {media.title[:55]}"
            )
        except Exception as exc:
            consecutive_failures += 1
            LOGGER.warning(
                "Send failed after retry (%d/%d, %s): %s",
                consecutive_failures,
                REDISCOVERY_FAILURE_THRESHOLD,
                type(exc).__name__,
                exc,
            )
            if (
                consecutive_failures >= REDISCOVERY_FAILURE_THRESHOLD
                and not DISPLAY_IP.strip()
            ):
                LOGGER.warning(
                    "Re-running display discovery after %d consecutive "
                    "failed update cycles.",
                    REDISCOVERY_FAILURE_THRESHOLD,
                )
                display_url = None
                consecutive_failures = 0

        elapsed = time.monotonic() - cycle_started
        await asyncio.sleep(max(0.1, SEND_INTERVAL_SECONDS - elapsed))


def run_sender_with_recovery() -> int:
    """Restart the async sender after an unexpected Python-level failure."""
    while True:
        try:
            return asyncio.run(main())
        except KeyboardInterrupt:
            LOGGER.info("Stopped.")
            return 0
        except Exception:
            configure_logging()
            LOGGER.exception(
                "Unexpected sender failure; restarting automatically in "
                "%.0f seconds.",
                UNEXPECTED_RESTART_DELAY_SECONDS,
            )
            time.sleep(UNEXPECTED_RESTART_DELAY_SECONDS)


if __name__ == "__main__":
    if not acquire_sender_instance():
        configure_logging()
        LOGGER.warning(
            "Another copy of the ESP32 display sender is already running."
        )
        raise SystemExit(0)

    try:
        raise SystemExit(run_sender_with_recovery())
    finally:
        release_sender_instance()
