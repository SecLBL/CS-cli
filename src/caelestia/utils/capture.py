"""Shared settings and process helpers for `caelestia record` and `caelestia replay`.

Settings live in the shell config (shell.json, section `capture`) so the shell's
settings page and keybinds share them; defaults here mirror the shell's C++ defaults.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from caelestia.utils import hypr
from caelestia.utils.paths import c_config_dir, get_config, recordings_dir, videos_dir

RECORDER = "gpu-screen-recorder"
shell_config_path: Path = c_config_dir / "shell.json"
default_clips_dir: Path = Path(os.getenv("CAELESTIA_CLIPS_DIR", videos_dir / "Clips"))

DEFAULTS: dict[str, Any] = {
    "codec": "auto",
    "vulkan": False,
    "container": "mkv",
    "cursor": True,
    "framerateMode": "vfr",
    "recordingsDir": "",
    "clipsDir": "",
    "appInFileName": True,
    "clipSound": True,
    "record": {"resolution": 0, "fps": 0, "bitrateMode": "qp", "quality": "very_high", "bitrate": 40000},
    "replay": {
        "resolution": 0,
        "fps": 60,
        "bitrateMode": "cbr",
        "quality": "very_high",
        "bitrate": 15000,
        "length": 60,
        "storage": "ram",
        "restartOnSave": False,
        "monitor": "",
    },
    "audio": {
        "codec": "opus",
        "bitrate": 128,
        "mixTrack": True,
        "system": True,
        "systemPostFilter": False,
        "chat": True,
        "chatPostFilter": False,
        "mic": True,
        "micPostFilter": True,
    },
    "cleanup": {"maxAgeDays": 0, "maxSizeGb": 0},
}

# ChromaShell audio pipeline nodes per track: (before filter, after filter)
_PIPELINE_SOURCES = {
    "system": ("MixBus.input.monitor", "general_chain_out"),
    "chat": ("MixBusChat.input.monitor", "chat_chain_out"),
    "mic": ("mic_chain_in.monitor", "mic_chain_out"),
}
# Generic fallbacks without the pipeline; chat has none
_FALLBACK_SOURCES = {"system": "default_output", "mic": "default_input"}

_RT_SIGNALS = {10: 1, 30: 2, 60: 3, 300: 4, 600: 5, 1800: 6}


def _merge(base: dict[str, Any], over: Any) -> dict[str, Any]:
    out = dict(base)
    if isinstance(over, dict):
        for k, v in over.items():
            out[k] = _merge(base[k], v) if isinstance(base.get(k), dict) else v
    return out


def get_settings() -> dict[str, Any]:
    try:
        conf = json.loads(shell_config_path.read_text()).get("capture", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        conf = {}
    return _merge(DEFAULTS, conf)


def _dir(value: str, fallback: Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))) if value else fallback


def recordings_path(settings: dict[str, Any]) -> Path:
    return _dir(settings["recordingsDir"], recordings_dir)


def clips_path(settings: dict[str, Any]) -> Path:
    return _dir(settings["clipsDir"], default_clips_dir)


def audio_devices() -> set[str]:
    try:
        out = subprocess.check_output([RECORDER, "--list-audio-devices"], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return set()
    return {line.split("|", 1)[0] for line in out.splitlines() if line}


def audio_sources(settings: dict[str, Any]) -> list[str]:
    """One source per enabled track; falls back to plain devices without the ChromaShell pipeline."""
    conf = settings["audio"]
    devices = audio_devices()
    sources = []
    for track, (pre, post) in _PIPELINE_SOURCES.items():
        if not conf[track]:
            continue
        node = post if conf[f"{track}PostFilter"] else pre
        if node in devices:
            sources.append(f"device:{node}")
        elif track in _FALLBACK_SOURCES:
            sources.append(_FALLBACK_SOURCES[track])
    return list(dict.fromkeys(sources))


def audio_args(settings: dict[str, Any], sources: list[str]) -> list[str]:
    if not sources:
        return []
    args = []
    # Players and most sites only play the first track, so lead with a mix of all of them
    if settings["audio"]["mixTrack"] and len(sources) > 1:
        args += ["-a", "|".join(sources)]
    for s in sources:
        args += ["-a", s]
    return args + ["-ac", settings["audio"]["codec"], "-ab", str(settings["audio"]["bitrate"])]


def focused_monitor() -> dict[str, Any] | None:
    return next((m for m in hypr.message("monitors") if m["focused"]), None)


def scaled_size(monitor: dict[str, Any], cap: int) -> str | None:
    """Output size with the shorter side capped at `cap`, keeping the monitor's aspect ratio."""
    w, h = monitor["width"], monitor["height"]
    if monitor.get("transform", 0) % 2:
        w, h = h, w
    short = min(w, h)
    if not cap or cap >= short:
        return None
    scale = cap / short
    return f"{round(w * scale / 2) * 2}x{round(h * scale / 2) * 2}"


def video_args(settings: dict[str, Any], profile: dict[str, Any], monitor: dict[str, Any] | None) -> list[str]:
    args = []
    codec = settings["codec"]
    if codec != "auto":
        args += ["-k", f"{codec}_vulkan" if settings["vulkan"] else codec]
    if profile["bitrateMode"] == "cbr":
        args += ["-bm", "cbr", "-q", str(profile["bitrate"])]
    else:
        args += ["-bm", profile["bitrateMode"], "-q", profile["quality"]]
    args += ["-fm", settings["framerateMode"], "-cursor", "yes" if settings["cursor"] else "no"]
    if monitor and (size := scaled_size(monitor, profile["resolution"])):
        args += ["-s", size]
    return args


def fps_for(profile: dict[str, Any], refresh_rate: float) -> int:
    native = round(refresh_rate) or 60
    return min(profile["fps"], native) if profile["fps"] else native


def extra_args() -> list[str]:
    config = get_config()
    try:
        return list(config["record"]["extraArgs"]) if "extraArgs" in config.get("record", {}) else []
    except TypeError as e:
        raise ValueError(f"Config option 'record.extraArgs' should be an array: {e}")


def active_app() -> str:
    try:
        cls = hypr.message("activewindow").get("class", "")
    except Exception:
        return ""
    return re.sub(r"[^A-Za-z0-9]+", "", cls.split(".")[-1])[:32]


# Process tracking: pid files instead of pidof, so recorder and replay buffer can run side by side


def read_pid(pid_file: Path) -> int | None:
    try:
        pid = int(pid_file.read_text().split()[0])
        if RECORDER in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="ignore"):
            return pid
    except (OSError, ValueError, IndexError):
        pass
    return None


def stop_pid(pid: int, sig: int = signal.SIGINT) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return
    # Wait for the recorder to finalise the file
    while Path(f"/proc/{pid}").exists():
        time.sleep(0.1)


def save_signal(seconds: int) -> int:
    return signal.SIGRTMIN + _RT_SIGNALS[seconds] if seconds in _RT_SIGNALS else signal.SIGUSR1


def stamp() -> str:
    return time.strftime("%Y%m%d_%H-%M-%S")


def cleanup(directory: Path, settings: dict[str, Any], pattern: str) -> None:
    conf = settings["cleanup"]
    files = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if conf["maxAgeDays"] > 0:
        limit = time.time() - conf["maxAgeDays"] * 86400
        for f in [f for f in files if f.stat().st_mtime < limit]:
            f.unlink(missing_ok=True)
            files.remove(f)
    if conf["maxSizeGb"] > 0:
        total, budget = 0, conf["maxSizeGb"] * 1024**3
        for f in files:
            total += f.stat().st_size
            if total > budget:
                f.unlink(missing_ok=True)


def play_sound(name: str) -> None:
    for base in os.getenv("XDG_DATA_DIRS", "/run/current-system/sw/share:/usr/share").split(":"):
        path = Path(base) / "sounds/freedesktop/stereo" / f"{name}.oga"
        if path.exists() and shutil.which("pw-play"):
            subprocess.Popen(["pw-play", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return


# Handles the "saved" notification actions in a tiny detached shell process,
# so the command can exit as soon as the file is saved.
_SAVED_NOTIF_HANDLER = r"""
title=$1
path=$2
uri=$3
directory=$4

action=$(notify-send -a caelestia-cli \
    --action=watch=Watch \
    --action=open=Open \
    --action=delete=Delete \
    "$title" \
    "Saved in $path") || exit 0

case "$action" in
    watch)
        exec xdg-open "$path"
        ;;
    open)
        if ! dbus-send --session \
            --dest=org.freedesktop.FileManager1 \
            --type=method_call \
            /org/freedesktop/FileManager1 \
            org.freedesktop.FileManager1.ShowItems \
            "array:string:$uri" \
            "string:"; then
            exec xdg-open "$directory"
        fi
        ;;
    delete)
        rm -f -- "$path"
        ;;
esac
"""


def notify_saved(title: str, path: Path) -> None:
    path = path.resolve()
    subprocess.Popen(
        ["sh", "-c", _SAVED_NOTIF_HANDLER, "sh", title, str(path), path.as_uri(), str(path.parent)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        cwd="/",
    )
