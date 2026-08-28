#!/usr/bin/env python3
"""Small, URL-first mpv controller for the Omarchy YouTube Player plugin."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import selectors
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

PLUGIN_NAME = "omarchy-youtube-player"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
SOCKET_PATH = RUNTIME_DIR / f"{PLUGIN_NAME}.sock"
STATE_PATH = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / PLUGIN_NAME / "state.json"
VIDEO_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
MAX_STATE_BYTES = 64 * 1024
MAX_PROCESS_BYTES = 256 * 1024
MAX_SOCKET_BYTES = 64 * 1024
MAX_URL_CHARS = 2048
MAX_THUMBNAIL_CHARS = 512
MAX_TITLE_CHARS = 180
MAX_CHANNEL_CHARS = 100
MAX_WORKSPACE_CHARS = 64
MAX_RESULTS = 8


class ResponseLimitError(subprocess.SubprocessError):
    """A child process or socket exceeded its bounded response size."""


def _bounded_text(value: object, limit: int) -> str:
    return str(value or "")[:limit]


def _safe_http_url(value: object, limit: int = MAX_URL_CHARS) -> str:
    text = _bounded_text(value, limit)
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _bounded_number(value: object, maximum: float = 604800.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return min(max(number, 0.0), maximum) if math.isfinite(number) else 0.0


def _t(es: str, en: str, lang: str) -> str:
    return es if lang == "es" else en


def valid_video_url(value: str) -> bool:
    if not isinstance(value, str) or len(value) > MAX_URL_CHARS:
        return False
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


def _state_data(data: object) -> dict:
    if not isinstance(data, dict):
        return {}
    return {
        "url": _safe_http_url(data.get("url")),
        "title": _bounded_text(data.get("title"), MAX_TITLE_CHARS),
        "channel": _bounded_text(data.get("channel"), MAX_CHANNEL_CHARS),
        "thumbnail": _safe_http_url(data.get("thumbnail"), MAX_THUMBNAIL_CHARS),
        "audioOnly": bool(data.get("audioOnly")),
        "workspace": _bounded_text(data.get("workspace"), MAX_WORKSPACE_CHARS),
    }


def _ensure_state_dir() -> None:
    directory = STATE_PATH.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise OSError("state directory is not a private user-owned directory")
    if info.st_mode & 0o077:
        os.chmod(directory, 0o700)


def write_state(data: dict) -> None:
    _ensure_state_dir()
    payload = json.dumps(_state_data(data), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_STATE_BYTES:
        raise ResponseLimitError("state exceeds its size limit")
    descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=STATE_PATH.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, STATE_PATH)
        temporary = ""
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def read_state() -> dict:
    try:
        descriptor = os.open(
            STATE_PATH,
            os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            info = os.fstat(descriptor)
            if (
                info.st_uid != os.getuid()
                or not stat.S_ISREG(info.st_mode)
                or info.st_size > MAX_STATE_BYTES
            ):
                return {}
            payload = os.read(descriptor, MAX_STATE_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(payload) > MAX_STATE_BYTES:
            return {}
        return _state_data(json.loads(payload.decode("utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def socket_is_alive() -> bool:
    try:
        socket_info = SOCKET_PATH.stat(follow_symlinks=False)
        if not stat.S_ISSOCK(socket_info.st_mode) or socket_info.st_uid != os.getuid():
            return False
    except OSError:
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.4)
            client.connect(str(SOCKET_PATH))
        return True
    except OSError:
        return False


def _run_bounded(command: list[str], timeout: float = 4.0, max_bytes: int = MAX_PROCESS_BYTES) -> tuple[int, bytes, bytes]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    for stream in (process.stdout, process.stderr):
        assert stream is not None
        selector.register(stream, selectors.EVENT_READ)
    stdout = bytearray()
    stderr = bytearray()
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _ in selector.select(min(remaining, 0.25)):
                stream = key.fileobj
                chunk = os.read(stream.fileno(), min(65536, max_bytes - len(stdout) - len(stderr) + 1))
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                if len(stdout) + len(stderr) + len(chunk) > max_bytes:
                    raise ResponseLimitError("process response exceeds its size limit")
                (stdout if stream is process.stdout else stderr).extend(chunk)
        return process.wait(timeout=max(0, deadline - time.monotonic())), bytes(stdout), bytes(stderr)
    except (OSError, subprocess.TimeoutExpired, ResponseLimitError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        process.wait()
        raise
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def _run_bounded_text(command: list[str], timeout: float = 4.0) -> tuple[int, str, str]:
    code, stdout, stderr = _run_bounded(command, timeout=timeout)
    return code, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")


def _hyprctl_json(arguments: list[str]) -> dict | list:
    try:
        code, stdout, _ = _run_bounded_text(["hyprctl", *arguments])
        data = json.loads(stdout)
        return data if code == 0 and isinstance(data, (dict, list)) else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}


def active_window_address() -> str:
    active = _hyprctl_json(["activewindow", "-j"])
    return _bounded_text(active.get("address"), MAX_WORKSPACE_CHARS) if isinstance(active, dict) else ""


def normalize_window(restore_address: str = "") -> None:
    """Keep the player as PiP without leaving focus on it."""
    clients = _hyprctl_json(["clients", "-j"])
    if not isinstance(clients, list):
        return
    player = next((item for item in clients if isinstance(item, dict) and item.get("title") == "Omarchy YouTube"), None)
    if not player:
        return
    address = _bounded_text(player.get("address"), MAX_WORKSPACE_CHARS)
    active_address = active_window_address()
    size = player.get("size") or [480, 270]

    def dispatch(expression: str) -> None:
        subprocess.run(["hyprctl", "dispatch", expression], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4, check=False)

    if address and address != active_address:
        dispatch(f'''hl.dsp.focus({{ window = "address:{address}" }})''')
    width_delta = 480 - int(size[0])
    height_delta = 270 - int(size[1])
    if width_delta or height_delta:
        dispatch(f"hl.dsp.window.resize({{ x = {width_delta}, y = {height_delta}, relative = true }})")
    dispatch("hl.dsp.window.move({ x = 24, y = 64, relative = false })")
    if restore_address and restore_address != address:
        dispatch(f'''hl.dsp.focus({{ window = "address:{restore_address}" }})''')


def move_player_to_workspace(workspace: str) -> None:
    workspace = _bounded_text(workspace, MAX_WORKSPACE_CHARS)
    if not workspace:
        return
    clients = _hyprctl_json(["clients", "-j"])
    if not isinstance(clients, list):
        return
    active_address = active_window_address()
    player = next((item for item in clients if isinstance(item, dict) and item.get("title") == "Omarchy YouTube"), None)
    if not player or not player.get("address"):
        return
    address = _bounded_text(player["address"], MAX_WORKSPACE_CHARS)

    def dispatch(expression: str) -> None:
        subprocess.run(["hyprctl", "dispatch", expression], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4, check=False)

    if address != active_address:
        dispatch(f'''hl.dsp.focus({{ window = "address:{address}" }})''')
    dispatch(f'''hl.dsp.window.move({{ workspace = "{workspace}", follow = false }})''')
    if active_address and active_address != address:
        dispatch(f'''hl.dsp.focus({{ window = "address:{active_address}" }})''')


def player_workspace() -> str:
    clients = _hyprctl_json(["clients", "-j"])
    if not isinstance(clients, list):
        return ""
    player = next((item for item in clients if isinstance(item, dict) and item.get("title") == "Omarchy YouTube"), None)
    workspace = player.get("workspace") if player else None
    return _bounded_text(workspace.get("name"), MAX_WORKSPACE_CHARS) if isinstance(workspace, dict) else ""


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
    payload = (json.dumps({"command": command, "request_id": request_id}) + "\n").encode("utf-8")
    if len(payload) > MAX_SOCKET_BYTES:
        raise ResponseLimitError("IPC request exceeds its size limit")
    deadline = time.monotonic() + 4
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(0.4)
        client.connect(str(SOCKET_PATH))
        client.sendall(payload)
        response_bytes = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("mpv IPC response timed out")
            client.settimeout(min(0.4, remaining))
            try:
                chunk = client.recv(min(65536, MAX_SOCKET_BYTES - len(response_bytes) + 1))
            except socket.timeout:
                continue
            if not chunk:
                break
            if len(response_bytes) + len(chunk) > MAX_SOCKET_BYTES:
                raise ResponseLimitError("IPC response exceeds its size limit")
            response_bytes.extend(chunk)
            if b"\n" in chunk:
                break
    for line in bytes(response_bytes).splitlines():
        try:
            response = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if response.get("request_id") == request_id:
            if response.get("error") != "success":
                raise RuntimeError(_bounded_text(response.get("error"), MAX_TITLE_CHARS) or "mpv command failed")
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
    code, stdout, stderr = _run_bounded_text(command, timeout=25)
    if code != 0:
        raise RuntimeError(stderr.strip()[:MAX_TITLE_CHARS] or "yt-dlp could not read this video")
    data = json.loads(stdout)
    if not isinstance(data, dict):
        raise ValueError("yt-dlp returned an invalid object")
    entries = data.get("entries")
    if isinstance(entries, list) and entries:
        data = next((entry for entry in entries if isinstance(entry, dict)), {})
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
    code, stdout, _ = _run_bounded_text(command, timeout=35)
    if code != 0:
        return {"ok": False, "message": _t("No se pudo buscar en YouTube.", "YouTube search failed.", lang)}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": False, "message": _t("La respuesta de YouTube no fue válida.", "YouTube returned an invalid response.", lang)}
    if not isinstance(data, dict):
        return {"ok": False, "message": _t("La respuesta de YouTube no fue válida.", "YouTube returned an invalid response.", lang)}
    entries = []
    source_entries = data.get("entries") if isinstance(data.get("entries"), list) else []
    for item in source_entries[:MAX_RESULTS]:
        if not isinstance(item, dict):
            continue
        video_id = _bounded_text(item.get("id"), 64)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", video_id):
            continue
        entries.append({
            "id": video_id,
            "title": _bounded_text(item.get("title"), MAX_TITLE_CHARS) or "Untitled",
            "channel": _bounded_text(item.get("channel") or item.get("uploader"), MAX_CHANNEL_CHARS),
            "duration": format_duration(item.get("duration")),
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "thumbnail": _safe_http_url(item.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg", MAX_THUMBNAIL_CHARS),
        })
    return {"ok": True, "results": entries}


def play(url: str, lang: str) -> dict:
    if not valid_video_url(url):
        return {"ok": False, "message": _t("La URL no parece ser de YouTube.", "That URL does not look like YouTube.", lang)}
    restore_address = active_window_address()
    previous_state = read_state()
    try:
        data = metadata_for(url)
        title = _bounded_text(data.get("title"), MAX_TITLE_CHARS) or "YouTube video"
        channel = _bounded_text(data.get("channel") or data.get("uploader"), MAX_CHANNEL_CHARS)
        thumbnail = _safe_http_url(data.get("thumbnail"), MAX_THUMBNAIL_CHARS)
        ipc(["loadfile", url, "replace"])
        ipc(["set_property", "pause", False])
        if previous_state.get("audioOnly"):
            ipc(["set_property", "vid", "auto"])
            move_player_to_workspace(previous_state.get("workspace", ""))
        normalize_window(restore_address)
        write_state({"url": url, "title": title, "channel": channel, "thumbnail": thumbnail, "audioOnly": False})
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
            ipc(["set_property", "window-minimized", False])
            normalize_window(active_window_address())
        elif name == "audio-only":
            state = read_state()
            workspace = player_workspace()
            if workspace and not workspace.startswith("special:"):
                state["workspace"] = workspace
            ipc(["set_property", "vid", "no"])
            ipc(["set_property", "window-minimized", True])
            move_player_to_workspace("special:youtube-audio")
            state["audioOnly"] = True
            write_state(state)
        elif name == "show-video":
            state = read_state()
            ipc(["set_property", "vid", "auto"])
            ipc(["set_property", "window-minimized", False])
            move_player_to_workspace(state.get("workspace", ""))
            normalize_window(active_window_address())
            state["audioOnly"] = False
            write_state(state)
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
    title = _bounded_text(get_property("media-title") or state.get("title"), MAX_TITLE_CHARS)
    pause = get_property("pause")
    return {
        "ok": True,
        "active": bool(title) and not bool(idle),
        "playing": bool(title) and not bool(idle) and pause is False,
        "title": title,
        "channel": _bounded_text(state.get("channel"), MAX_CHANNEL_CHARS),
        "thumbnail": _safe_http_url(state.get("thumbnail"), MAX_THUMBNAIL_CHARS),
        "url": _safe_http_url(state.get("url")),
        "position": _bounded_number(get_property("time-pos")),
        "durationSeconds": _bounded_number(get_property("duration")),
        "fullscreen": bool(get_property("fullscreen")),
        "audioOnly": bool(state.get("audioOnly")) or get_property("vid") == "no",
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
