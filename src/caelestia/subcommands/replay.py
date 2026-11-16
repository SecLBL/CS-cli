import json
import os
import shutil
import signal
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path
from typing import Any

from caelestia.utils import capture, hypr
from caelestia.utils.notify import notify
from caelestia.utils.paths import atomic_dump, c_state_dir

state_dir: Path = c_state_dir / "replay"
state_path: Path = state_dir / "state.json"
hook_path: Path = state_dir / "on-save.sh"
log_path: Path = state_dir / "recorder.log"


def read_state() -> dict[str, Any]:
    try:
        return json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def running_pid() -> int | None:
    return capture.read_pid(state_dir / "pid")


def estimate_mb(settings: dict[str, Any], tracks: int) -> float | None:
    """RAM/disk the buffer needs; only predictable with a constant bitrate."""
    profile = settings["replay"]
    if profile["bitrateMode"] != "cbr":
        return None
    kbps = profile["bitrate"] + tracks * settings["audio"]["bitrate"]
    return round(kbps * profile["length"] / 8 / 1024, 1)


class Command:
    args: Namespace

    def __init__(self, args: Namespace) -> None:
        self.args = args

    def run(self) -> None:
        action = self.args.action
        if action in (None, "toggle"):
            self.stop() if running_pid() else self.start()
        elif action == "start":
            if not running_pid():
                self.start()
        elif action == "stop":
            self.stop()
        elif action == "restart":
            self.stop()
            self.start()
        elif action == "save":
            self.save()
        elif action == "status":
            print(json.dumps({**read_state(), "running": running_pid() is not None}))
        elif action == "codecs":
            self.codecs()
        elif action == "saved":
            self.saved()

    def start(self) -> None:
        settings = capture.get_settings()
        profile = settings["replay"]

        monitors = hypr.message("monitors")
        wanted = profile.get("monitor", "")
        monitor = next((m for m in monitors if m["name"] == wanted), None) or next(
            (m for m in monitors if m["focused"]), None
        )
        if not monitor:
            raise RuntimeError("No monitor to capture")

        sources = capture.audio_sources(settings)
        tracks = len(capture.audio_args(settings, sources)[:-4]) // 2
        clips_dir = capture.clips_path(settings)
        clips_dir.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)

        # gsr runs a script path with (file, type) after each save; route it back to us
        caelestia = shutil.which("caelestia") or sys.argv[0]
        hook_path.write_text(f'#!/bin/sh\nexec "{caelestia}" replay saved "$1" "$2"\n')
        hook_path.chmod(0o755)

        args = [
            capture.RECORDER,
            "-v",
            "no",
            "-w",
            monitor["name"],
            "-f",
            str(capture.fps_for(profile, monitor["refreshRate"])),
            "-r",
            str(profile["length"]),
            "-replay-storage",
            profile["storage"],
            "-restart-replay-on-save",
            "yes" if profile["restartOnSave"] else "no",
            *capture.video_args(settings, profile, monitor),
            *capture.audio_args(settings, sources),
            "-c",
            settings["container"],
            "-sc",
            str(hook_path),
            "-o",
            str(clips_dir),
        ]
        # The recorder outlives this command; its output goes to a log for error reports
        with log_path.open("w") as log:
            proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        (state_dir / "pid").write_text(f"{proc.pid}\n")

        try:
            if proc.wait(1) != 0:
                err = log_path.read_text(errors="ignore").strip().splitlines()
                (state_dir / "pid").unlink(missing_ok=True)
                notify("Replay buffer failed", err[-1] if err else f"exit code {proc.returncode}")
                return
        except subprocess.TimeoutExpired:
            pass

        atomic_dump(
            state_path,
            {
                "pid": proc.pid,
                "started": time.time(),
                "monitor": monitor["name"],
                "length": profile["length"],
                "storage": profile["storage"],
                "tracks": tracks,
                "estimateMb": estimate_mb(settings, tracks),
                "clipsDir": str(clips_dir),
            },
        )

    def stop(self) -> None:
        if pid := running_pid():
            # SIGINT ends replay mode without saving
            capture.stop_pid(pid)
        (state_dir / "pid").unlink(missing_ok=True)
        if state_path.exists():
            last = {k: v for k, v in read_state().items() if k in ("lastSaved", "lastSavedAt")}
            atomic_dump(state_path, last)

    def save(self) -> None:
        pid = running_pid()
        if not pid:
            notify("Replay buffer is off", "Start it first to save clips")
            return
        seconds = self.args.seconds or 0
        # Remember the requested length; the save hook trims clips the recorder makes too long
        wanted = seconds if capture.save_signal(seconds) != signal.SIGUSR1 else read_state().get("length", 0)
        atomic_dump(state_path, {**read_state(), "pendingSeconds": wanted})
        os.kill(pid, capture.save_signal(seconds))

    def codecs(self) -> None:
        try:
            out = subprocess.run([capture.RECORDER, "--info"], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.TimeoutExpired):
            return
        section = ""
        for line in out.splitlines():
            if line.startswith("section="):
                section = line[8:]
            elif section == "video_codecs":
                print(line)

    def saved(self) -> None:
        src = Path(self.args.path)
        if self.args.type != "replay" or not src.exists():
            return
        settings = capture.get_settings()
        app = capture.active_app() if settings["appInFileName"] else ""
        dest = src.with_name(f"clip_{capture.stamp()}{'_' + app if app else ''}{src.suffix}")
        src.rename(dest)

        if settings["clipSound"]:
            capture.play_sound("camera-shutter")

        state = read_state()
        capture.trim_to(dest, state.get("pendingSeconds", 0))
        atomic_dump(state_path, {**state, "lastSaved": str(dest), "lastSavedAt": time.time()})
        capture.notify_saved("Clip saved", dest)
        capture.cleanup(dest.parent, settings, "clip_*")
