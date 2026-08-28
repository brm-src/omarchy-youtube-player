#!/usr/bin/env python3
"""Small, URL-first mpv controller for the Omarchy YouTube Player plugin."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

PLUGIN_NAME = "omarchy-youtube-player"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
SOCKET_PATH = RUNTIME_DIR / f"{PLUGIN_NAME}.sock"
STATE_PATH = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / PLUGIN_NAME / "state.json"
VIDEO_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}


def _t(es: str, en: str, lang: str) -> str:
    return es if lang == "es" else en


def valid_video_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return parsed.scheme in {"http", "https"} and host in {h.removeprefix('www.') for h in VIDEO_HOSTS}


def format_duration(seconds: object) -> str:
    try:
        value = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    if value < 0:
        return ""
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def write_state(data: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(STATE_PATH)


def read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def socket_is_alive() -> bool:
    if not SOCKET_PATH.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.4)
            client.connect(str(SOCKET_PATH))
        return True
    except OSError:
        return False


def active_window_address() -> str:
    try:
        active = json.loads(subprocess.check_output(["hyprctl", "activewindow", "-j"], text=True))
        return str(active.get("address") or "")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return ""


def normalize_window(restore_address: str = "") -> None:
    """Keep the player as PiP without leaving focus on it."""
    try:
        clients = json.loads(subprocess.check_output(["hyprctl", "clients", "-j"], text=True))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return
    player = next((item for item in clients if item.get("title") == "Omarchy YouTube"), None)
    if not player:
        return
    address = player.get("address", "")
    active_address = active_window_address()
    size = player.get("size") or [480, 270]

    def dispatch(expression: str) -> None:
        subprocess.run(["hyprctl", "dispatch", expression], capture_output=True, check=False)

    if address and address != active_address:
        dispatch(f'''hl.dsp.focus({{ window = "address:{address}" }})''')
    width_delta = 480 - int(size[0])
    height_delta = 270 - int(size[1])
    if width_delta or height_delta:
        dispatch(f"hl.dsp.window.resize({{ x = {width_delta}, y = {height_delta}, relative = true }})")
    dispatch("hl.dsp.window.move({ x = 24, y = 64, relative = false })")
    if restore_address and restore_address != address:
        dispatch(f'''hl.dsp.focus({{ window = "address:{restore_address}" }})''')


def ensure_player() -> None:
    restore_address = active_window_address()
    if socket_is_alive():
        normalize_window(restore_address)
        return
    try:
        SOCKET_PATH.unlink()
    except FileNotFoundError:
        pass
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "mpv",
        "--no-terminal",
        "--force-window=immediate",
        "--focus-on=never",
        "--force-window-position",
        "--ontop-level=system",
        "--player-operation-mode=pseudo-gui",
        "--title=Omarchy YouTube",
        "--input-ipc-server=" + str(SOCKET_PATH),
        "--geometry=480x270+24+64",
        "--autofit=480x270",
        "--ytdl-format=bestvideo[height<=720]+bestaudio/best[height<=720]",
        "--idle=yes",
        "--keep-open=yes",
    ]
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if socket_is_alive():
            normalize_window(restore_address)
            return
        time.sleep(0.05)
    raise RuntimeError("mpv did not open its IPC socket")


def ipc(command: list[object], request_id: int = 1) -> object:
    ensure_player()
    payload = json.dumps({"command": command, "request_id": request_id}) + "\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(4)
        client.connect(str(SOCKET_PATH))
        client.sendall(payload.encode())
        chunks = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    for line in b"".join(chunks).splitlines():
        try:
            response = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if response.get("request_id") == request_id:
            if response.get("error") != "success":
                raise RuntimeError(response.get("error", "mpv command failed"))
            return response.get("data")
    return None


def get_property(name: str) -> object:
    try:
        return ipc(["get_property", name], request_id=2)
    except (OSError, RuntimeError):
        return None


def metadata_for(url: str) -> dict:
    command = [
        "yt-dlp",
        "--dump-single-json",
        "--flat-playlist",
        "--skip-download",
        "--no-warnings",
        "--playlist-end",
        "1",
        url,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=25, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "yt-dlp could not read this video")
    data = json.loads(result.stdout)
    if data.get("entries"):
        data = next((entry for entry in data["entries"] if entry), {})
    return data


def search(query: str, lang: str) -> dict:
    query = query.strip()[:160]
    if not query:
        return {"ok": False, "message": _t("Escribe algo para buscar.", "Type something to search.", lang)}
    command = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-end",
        "8",
        "--no-warnings",
        "--quiet",
        "ytsearch8:" + query,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=35, check=False)
    if result.returncode != 0:
        return {"ok": False, "message": _t("No se pudo buscar en YouTube.", "YouTube search failed.", lang)}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "message": _t("La respuesta de YouTube no fue válida.", "YouTube returned an invalid response.", lang)}
    entries = []
    for item in data.get("entries", [])[:8]:
        if not item or not item.get("id"):
            continue
        entries.append({
            "id": item["id"],
            "title": str(item.get("title") or "Untitled")[:180],
            "channel": str(item.get("channel") or item.get("uploader") or "")[:100],
            "duration": format_duration(item.get("duration")),
            "url": f"https://www.youtube.com/watch?v={item['id']}",
            "thumbnail": item.get("thumbnail") or f"https://i.ytimg.com/vi/{item['id']}/hqdefault.jpg",
        })
    return {"ok": True, "results": entries}


def play(url: str, lang: str) -> dict:
    if not valid_video_url(url):
        return {"ok": False, "message": _t("La URL no parece ser de YouTube.", "That URL does not look like YouTube.", lang)}
    restore_address = active_window_address()
    try:
        data = metadata_for(url)
        title = str(data.get("title") or "YouTube video")[:180]
        channel = str(data.get("channel") or data.get("uploader") or "")[:100]
        thumbnail = data.get("thumbnail") or ""
        ipc(["loadfile", url, "replace"])
        ipc(["set_property", "pause", False])
        normalize_window(restore_address)
        write_state({"url": url, "title": title, "channel": channel, "thumbnail": thumbnail})
        return {"ok": True, "title": title, "channel": channel, "thumbnail": thumbnail, "url": url}
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        return {"ok": False, "message": _t("No se pudo reproducir este video.", "This video could not be played.", lang), "detail": str(error)[:180]}


def action(name: str, lang: str) -> dict:
    try:
        if name == "pause":
            ipc(["cycle", "pause"])
        elif name == "stop":
            ipc(["stop"])
            write_state({})
        elif name == "back":
            ipc(["seek", -10, "relative"])
        elif name == "forward":
            ipc(["seek", 10, "relative"])
        elif name == "volume-up":
            ipc(["add", "volume", 5])
        elif name == "volume-down":
            ipc(["add", "volume", -5])
        elif name == "fullscreen":
            ipc(["cycle", "fullscreen"])
        elif name == "show":
            try:
                ipc(["set_property", "window-minimized", False])
            except RuntimeError:
                pass
        elif name == "quit":
            ipc(["quit"])
            write_state({})
        else:
            return {"ok": False, "message": _t("Acción no reconocida.", "Unknown action.", lang)}
        return {"ok": True}
    except (OSError, RuntimeError) as error:
        return {"ok": False, "message": _t("El reproductor no está disponible.", "The player is not available.", lang), "detail": str(error)[:180]}


def status() -> dict:
    state = read_state()
    if not socket_is_alive():
        return {"ok": True, "active": False, **state}
    idle = get_property("idle-active")
    title = get_property("media-title") or state.get("title", "")
    pause = get_property("pause")
    return {
        "ok": True,
        "active": bool(title) and not bool(idle),
        "playing": bool(title) and not bool(idle) and pause is False,
        "title": title,
        "channel": state.get("channel", ""),
        "thumbnail": state.get("thumbnail", ""),
        "url": state.get("url", ""),
        "position": get_property("time-pos") or 0,
        "durationSeconds": get_property("duration") or 0,
        "fullscreen": bool(get_property("fullscreen")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["search", "play", "action", "status"])
    parser.add_argument("value", nargs="?")
    parser.add_argument("--lang", choices=["es", "en"], default="en")
    args = parser.parse_args()
    try:
        if args.command == "search":
            output = search(args.value or "", args.lang)
        elif args.command == "play":
            output = play(args.value or "", args.lang)
        elif args.command == "action":
            output = action(args.value or "", args.lang)
        else:
            output = status()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        output = {"ok": False, "message": str(error)[:200]}
    print(json.dumps(output, ensure_ascii=False))
    return 0 if output.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
