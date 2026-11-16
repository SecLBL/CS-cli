import os
import re
import shutil
import signal
import subprocess
from argparse import Namespace
from pathlib import Path

from caelestia.utils import capture, hypr
from caelestia.utils.notify import close_notification, notify
from caelestia.utils.paths import c_state_dir, recording_notif_path, recording_path

RECORDER = capture.RECORDER
# First line: recorder pid, second line: the file it writes to
pid_path: Path = c_state_dir / "record/pid"


class Command:
    args: Namespace

    def __init__(self, args: Namespace) -> None:
        self.args = args

    def run(self) -> None:
        if self.args.pause:
            if pid := capture.read_pid(pid_path):
                os.kill(pid, signal.SIGUSR2)
        elif self.proc_running():
            self.stop()
        else:
            self.start()

    def proc_running(self) -> bool:
        return capture.read_pid(pid_path) is not None

    def intersects(self, a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
        return a[0] < b[0] + b[2] and a[0] + a[2] > b[0] and a[1] < b[1] + b[3] and a[1] + a[3] > b[1]

    def start(self) -> None:
        settings = capture.get_settings()
        profile = settings["record"]
        args = ["-w"]

        monitors = hypr.message("monitors")
        if self.args.region:
            if self.args.region == "slurp":
                region = subprocess.check_output(
                    ["slurp", "-f", "%wx%h+%x+%y"],
                    text=True,
                    stdin=subprocess.DEVNULL,
                )
            else:
                region = self.args.region.strip()
            args += ["region", "-region", region]

            m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", region)
            if not m:
                raise ValueError(f"Invalid region: {region}")

            w, h, x, y = map(int, m.groups())
            r = x, y, w, h
            max_rr = 0
            for monitor in monitors:
                if self.intersects((monitor["x"], monitor["y"], monitor["width"], monitor["height"]), r):
                    rr = round(monitor["refreshRate"])
                    max_rr = max(max_rr, rr)
            args += ["-f", str(capture.fps_for(profile, max_rr))]
            args += capture.video_args(settings, profile, None)
        else:
            focused_monitor = next((monitor for monitor in monitors if monitor["focused"]), None)
            if focused_monitor:
                args += [
                    focused_monitor["name"],
                    "-f",
                    str(capture.fps_for(profile, focused_monitor["refreshRate"])),
                ]
            args += capture.video_args(settings, profile, focused_monitor)

        if self.args.sound:
            args += capture.audio_args(settings, capture.audio_sources(settings)) or ["-a", "default_output"]

        args += capture.extra_args()

        output = recording_path.with_suffix(f".{settings['container']}")
        output.parent.mkdir(parents=True, exist_ok=True)
        # The recorder outlives this command, so it must not inherit our stdio
        proc = subprocess.Popen(
            [RECORDER, *args, "-c", settings["container"], "-o", str(output)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        pid_path.write_text(f"{proc.pid}\n{output}\n")

        notif = notify("-p", "Recording started", "Recording...")
        recording_notif_path.write_text(notif)

        try:
            if proc.wait(1) != 0:
                close_notification(notif)
                notify(
                    "Recording failed",
                    "An error occurred attempting to start recorder. "
                    f"Command `{' '.join(proc.args)}` failed with exit code {proc.returncode}",
                )
        except subprocess.TimeoutExpired:
            pass

    def stop(self) -> None:
        settings = capture.get_settings()
        pid = capture.read_pid(pid_path)
        output = Path(pid_path.read_text().splitlines()[1])

        # Stop and wait for the recording to finish to avoid a corrupted video file
        capture.stop_pid(pid)
        pid_path.unlink(missing_ok=True)

        # Move to recordings folder
        directory = capture.recordings_path(settings)
        new_path = directory / f"recording_{capture.stamp()}{output.suffix}"
        directory.mkdir(exist_ok=True, parents=True)
        shutil.move(output, new_path)

        # Close start notification
        try:
            close_notification(recording_notif_path.read_text())
        except IOError:
            pass

        if self.args.clipboard:
            file_uri = Path(new_path).resolve().as_uri() + "\n"
            subprocess.run(["wl-copy", "--type", "text/uri-list"], input=file_uri.encode())

        capture.notify_saved("Recording stopped", new_path)
