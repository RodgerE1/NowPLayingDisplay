#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import os
import serial
import json
import threading
from datetime import datetime, timedelta
import sys

import pystray
from PIL import Image, ImageDraw

# For single instance application check on Windows
import win32event
import win32api
import winerror

from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager
)

# --- CONFIGURATION ---
ENABLE_SERIAL_OUTPUT        = True
ESP32_PORT                  = 'COM4'      # ← adjust as needed, Rodger!
SERIAL_BAUD_RATE            = 115200

ENABLE_JSON_OUTPUT          = True
JSON_FILENAME               = r"e:\now_playing_sender.json"

CHECK_INTERVAL_SECONDS      = 2          # how often to poll Windows (sec)
SERIAL_RETRY_INTERVAL       = 60         # retry COM port every N seconds
# ----------------------

# State-tracking globals
last_track_id       = None
last_status         = None
last_serial_try     = datetime.min
ser                 = None

# Global for the tray icon, so we can update its tooltip
tray_icon           = None
_tray_tooltip_message = "Now Playing: Initializing..." # Initial message
_tray_tooltip_lock  = threading.Lock() # Lock for thread-safe access

# Dedicated asyncio loop so the tray callback can stop it
loop = asyncio.new_event_loop()

# Mutex handle for single instance check
_mutex_handle = None

def create_tray_icon_image(size=64):
    """Generate a simple blank icon (white square) for the tray."""
    img = Image.new("RGB", (size, size), "white")
    draw = ImageDraw.Draw(img)
    # Optional: draw a small music note or dot
    draw.ellipse((size*0.3, size*0.3, size*0.7, size*0.7), fill="black")
    return img

def update_tray_tooltip(message: str):
    """
    Updates the system tray icon's tooltip message.
    This function is thread-safe.
    """
    global tray_icon, _tray_tooltip_message
    with _tray_tooltip_lock:
        _tray_tooltip_message = message
        if tray_icon:
            # Updating the tooltip directly usually works with pystray
            # as it internally triggers a UI update.
            tray_icon.tooltip = message
            # For immediate visual update, sometimes explicit update is needed
            # but often just setting the property is enough if pystray is running.
            # tray_icon.update_menu() # Not strictly necessary for tooltip only

def on_quit(icon, item):
    """Called when the user selects 'Quit' from the tray menu."""
    icon.stop()
    loop.call_soon_threadsafe(loop.stop)

def setup_tray():
    """Initialize and run the system tray icon."""
    global tray_icon
    icon_image = create_tray_icon_image()
    menu = pystray.Menu(pystray.MenuItem("Quit", on_quit))
    
    with _tray_tooltip_lock:
        tray_icon = pystray.Icon("NowPlaying", icon_image, _tray_tooltip_message, menu)
    
    tray_icon.run()

def start_tray_thread():
    """Kick off the tray icon in a background thread."""
    t = threading.Thread(target=setup_tray, daemon=True)
    t.start()

def send_to_esp32(ser_instance, title, artist, status):
    """Send TITLE|ARTIST|STATUS over serial to the ESP32."""
    msg = f"{title}|{artist}|{status}\n".encode('utf-8')
    try:
        ser_instance.write(msg)
        ser_instance.flush()
        print(f"SERIAL → Sent: {msg.decode().strip()}")
        update_tray_tooltip("Now Playing: Display connected, sending data.")
    except Exception as e:
        print(f"!! SERIAL ERROR writing: {e}")
        update_tray_tooltip(f"Now Playing: Display communication error: {e}")


def send_clear_to_esp32(ser_instance):
    """Send the special CLEAR command so the ESP32 can blank its display."""
    try:
        ser_instance.write(b"CLEAR\n")
        ser_instance.flush()
        print("SERIAL → Sent CLEAR")
        update_tray_tooltip("Now Playing: Display connected, cleared data.")
    except Exception:
        pass # Error already handled by send_to_esp32 if ser fails
        update_tray_tooltip("Now Playing: Display communication error.")


def write_to_json_file(info: dict):
    """Dump the full media-info dict to a compact JSON file."""
    if not ENABLE_JSON_OUTPUT:
        return
    try:
        with open(JSON_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(info, f, separators=(',',':'))
            f.flush()
            os.fsync(f.fileno())
        print("FILE → Wrote full JSON info")
    except Exception as e:
        print(f"!! FILE ERROR writing JSON: {e}")


def clear_json_file():
    """Reset the JSON file to an empty object when there's no session."""
    if not ENABLE_JSON_OUTPUT:
        return
    try:
        with open(JSON_FILENAME, 'w', encoding='utf-8') as f:
            f.write('{}')
        print("FILE → Cleared JSON file")
    except Exception as e:
        print(f"!! FILE ERROR clearing JSON: {e}")


async def get_current_track_info():
    """Fetch status, metadata, timeline, and source-app ID."""
    try:
        mgr   = await MediaManager.request_async()
        sess  = mgr.get_current_session()
        if not sess:
            return None

        status = sess.get_playback_info().playback_status
        props  = await sess.try_get_media_properties_async()
        tl     = sess.get_timeline_properties()  # synchronous

        return {
            "status":   status,
            "title":    props.title or "",
            "artist":   props.artist or "",
            "album":    props.album_title or "",
            "genres":   props.genres or [],
            "subtitle": props.subtitle or "",
            "rating":   getattr(props, "rating", ""),
            "position": tl.position.total_seconds(),
            "duration": tl.end_time.total_seconds(),
            "app_id":   sess.source_app_user_model_id or ""
        }

    except Exception as e:
        print(f"!! MEDIA ERROR: {e}")
        return None


async def main_loop():
    """Poll Windows, detect changes of title/artist/status, and send updates."""
    global last_track_id, last_status, last_serial_try, ser

    print("--- Unified Now-Playing Sender (Full JSON + Tray) ---")
    update_tray_tooltip("Now Playing: Waiting for media...")


    while True:
        # Retry opening serial port if it's not open
        if ENABLE_SERIAL_OUTPUT:
            if ser is None:
                now = datetime.now()
                if (now - last_serial_try) > timedelta(seconds=SERIAL_RETRY_INTERVAL):
                    last_serial_try = now
                    try:
                        ser = serial.Serial(ESP32_PORT, SERIAL_BAUD_RATE, timeout=1)
                        print(f"SERIAL → Connected to {ESP32_PORT}")
                        update_tray_tooltip(f"Now Playing: Connected to {ESP32_PORT}")
                    except Exception as e:
                        print(f"!! SERIAL ERROR opening port: {e}")
                        update_tray_tooltip(f"Now Playing: COM port error: {e}")
                        ser = None
            else: # If serial is connected, keep tooltip updated
                update_tray_tooltip(f"Now Playing: Connected to {ESP32_PORT}")
        else: # If serial output is disabled
            update_tray_tooltip("Now Playing: Serial output disabled.")


        info = await get_current_track_info()
        if info:
            track_id = info['title'] + info['artist']
            status   = info['status']
        else:
            track_id = None
            status   = None

        # Trigger on any change in title, artist, or status
        if track_id != last_track_id or status != last_status:
            if info:
                if ENABLE_SERIAL_OUTPUT and ser:
                    send_to_esp32(ser,
                                  info['title'],
                                  info['artist'],
                                  info['status'])
                write_to_json_file(info)
            else:
                if ENABLE_SERIAL_OUTPUT and ser:
                    send_clear_to_esp32(ser)
                clear_json_file()
                # Update tooltip if no media session is active
                if ser:
                    update_tray_tooltip(f"Now Playing: Connected to {ESP32_PORT}, no media.")
                else:
                    update_tray_tooltip("Now Playing: Waiting for media...")


            last_track_id = track_id
            last_status   = status

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def main():
    """Start the tray icon and run the main loop, cleaning up on exit."""
    global _mutex_handle

    # --- Single instance check ---
    # Create a named mutex. If it already exists, another instance is running.
    mutex_name = "NowPlayingSenderMutex-12345" # Unique name for your app
    try:
        _mutex_handle = win32event.CreateMutex(None, False, mutex_name)
        last_error = win32api.GetLastError()
        if last_error == winerror.ERROR_ALREADY_EXISTS:
            print("Another instance of Now Playing Sender is already running. Exiting.")
            sys.exit(0) # Exit cleanly if another instance is detected
    except Exception as e:
        print(f"Error creating mutex: {e}. Cannot guarantee single instance.")
        # Continue running, but without single-instance guarantee.

    start_tray_thread()
    try:
        await main_loop()
    finally:
        print("\n--- Shutting down, clearing outputs ---")
        if ENABLE_SERIAL_OUTPUT and ser:
            send_clear_to_esp32(ser)
            ser.close()
        clear_json_file()
        update_tray_tooltip("Now Playing: Shutting down.")

        # Release the mutex handle on exit
        if _mutex_handle:
            win32api.CloseHandle(_mutex_handle)


if __name__ == "__main__":
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        # This is expected when the loop is stopped by the tray icon.
        if "Event loop stopped before Future completed" in str(e):
            pass
        else:
            raise

