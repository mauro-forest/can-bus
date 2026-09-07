"""Supervises the two capture children and shows what they are doing.

`candump` and the serial reader both write their output to stdout; this module
pipes both, writes them into the session directory, counts as it goes, and
prints a live status line. Counting here rather than in the children means one
place knows the whole picture, which is what the status line needs.

The status line is the reason the CLI runs in the foreground. A dead serial
cable or a wrong bitrate is obvious within a second of starting, instead of
after an hour of experiments.
"""

from __future__ import annotations

import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from bikelog import marks as marks_mod
from bikelog.config import Config
from bikelog.lines import LineBuffer
from bikelog.meta import Session

STATUS_INTERVAL_S = 0.5
QUIET_AFTER_S = 3.0
SHUTDOWN_GRACE_S = 5.0
CAN_ERR_FLAG = 0x20000000


@dataclass
class Counts:
    can_frames: int = 0
    can_error_frames: int = 0
    can_ids: set[str] = field(default_factory=set)
    serial_lines: int = 0
    marks: int = 0
    last_can: float = 0.0
    last_serial: float = 0.0

    def as_meta(self) -> dict[str, object]:
        return {
            "can_frames": self.can_frames,
            "can_error_frames": self.can_error_frames,
            "can_ids": sorted(self.can_ids),
            "can_id_count": len(self.can_ids),
            "serial_lines": self.serial_lines,
            "marks": self.marks,
        }


def _candump_argv(interface: str) -> list[str]:
    """`candump -L` in log format, line-buffered.

    Without stdbuf, candump's stdio switches to block buffering when its stdout
    is a pipe, so the status line would lag by whole 4 KB blocks and a hard
    power cut would lose the tail of the log.
    """
    argv = ["candump", "-L", interface]
    if shutil.which("stdbuf"):
        return ["stdbuf", "-oL", *argv]
    return argv


class Recorder:
    def __init__(self, cfg: Config, session: Session) -> None:
        self.cfg = cfg
        self.session = session
        self.counts = Counts()
        self.notices: list[str] = []
        self._selector = selectors.DefaultSelector()
        self._children: list[tuple[str, subprocess.Popen[bytes]]] = []
        self._files: dict[str, object] = {}
        self._fifo_fd: int | None = None
        self._stop = False
        self._window_start = 0.0
        self._window_can = 0
        self._window_serial = 0
        self._can_rate = 0.0
        self._serial_rate = 0.0
        self._status_len = 0

    # -- console -----------------------------------------------------------

    def notice(self, message: str) -> None:
        """Print above the status line without leaving fragments behind."""
        sys.stderr.write("\r" + " " * self._status_len + "\r")
        sys.stderr.write(f"  {message}\n")
        self._status_len = 0
        sys.stderr.flush()
        self.notices.append(message)

    def _status(self, now: float) -> None:
        elapsed = int(now - self.session.started)
        clock = f"{elapsed // 3600:d}:{elapsed // 60 % 60:02d}:{elapsed % 60:02d}"

        can_quiet = now - self.counts.last_can if self.counts.last_can else None
        if can_quiet is None or can_quiet > QUIET_AFTER_S:
            since = "no frames yet" if can_quiet is None else f"quiet {can_quiet:.0f}s"
            can = f"can {since}"
        else:
            can = (
                f"can {self._can_rate:.0f}/s {self.counts.can_frames:,} frames "
                f"{len(self.counts.can_ids)} ids"
            )
        if self.counts.can_error_frames:
            can += f" {self.counts.can_error_frames} ERR"

        ser_quiet = now - self.counts.last_serial if self.counts.last_serial else None
        if ser_quiet is None or ser_quiet > QUIET_AFTER_S:
            since = "no lines yet" if ser_quiet is None else f"quiet {ser_quiet:.0f}s"
            ser = f"serial {since}"
        else:
            ser = f"serial {self._serial_rate:.0f}/s {self.counts.serial_lines:,} lines"

        line = f"{clock} | {can} | {ser} | marks {self.counts.marks}"
        padding = max(0, self._status_len - len(line))
        sys.stderr.write("\r" + line + " " * padding)
        sys.stderr.flush()
        self._status_len = len(line)

    # -- setup -------------------------------------------------------------

    def _spawn(self, name: str, argv: list[str], sink: Path) -> None:
        try:
            child = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError:
            self.notice(f"{name}: {argv[0]} not found; that stream is not recording")
            return

        self._children.append((name, child))
        assert child.stdout is not None and child.stderr is not None
        os.set_blocking(child.stdout.fileno(), False)
        os.set_blocking(child.stderr.fileno(), False)
        self._files[name] = sink.open("ab")
        self._selector.register(
            child.stdout.fileno(), selectors.EVENT_READ, (f"{name}:out", LineBuffer(child.stdout.fileno()))
        )
        self._selector.register(
            child.stderr.fileno(), selectors.EVENT_READ, (f"{name}:err", LineBuffer(child.stderr.fileno()))
        )

    def _setup(self) -> None:
        self._spawn("can", _candump_argv(self.cfg.can.interface), self.session.can_log)
        self._spawn(
            "serial",
            [
                sys.executable,
                "-m",
                "bikelog.serial_reader",
                "--port",
                self.cfg.serial.port,
                "--baudrate",
                str(self.cfg.serial.baudrate),
                "--timeout",
                str(self.cfg.serial.timeout),
            ],
            self.session.serial_log,
        )

        self.session.marks_log.touch()
        marks_mod.make_fifo(self.session.marks_fifo)
        self._fifo_fd = marks_mod.open_fifo_read(self.session.marks_fifo)
        self._selector.register(
            self._fifo_fd, selectors.EVENT_READ, ("fifo", LineBuffer(self._fifo_fd))
        )

        if sys.stdin is not None and sys.stdin.isatty():
            os.set_blocking(sys.stdin.fileno(), False)
            self._selector.register(
                sys.stdin.fileno(),
                selectors.EVENT_READ,
                ("stdin", LineBuffer(sys.stdin.fileno())),
            )
        else:
            self.notice("stdin is not a tty; use `bikelog mark` to annotate")

    # -- the loop ----------------------------------------------------------

    def _handle_can(self, line: bytes, now: float) -> None:
        self._files["can"].write(line + b"\n")  # type: ignore[union-attr]
        self.counts.can_frames += 1
        self._window_can += 1
        self.counts.last_can = now
        # "(1788538180.786114) can0 03FF1000#0840112200000001"
        parts = line.split(b" ")
        if len(parts) < 3:
            return
        can_id = parts[2].split(b"#", 1)[0].decode("ascii", "replace")
        self.counts.can_ids.add(can_id)
        try:
            if int(can_id, 16) & CAN_ERR_FLAG:
                self.counts.can_error_frames += 1
        except ValueError:
            pass

    def _handle_serial(self, line: bytes, now: float) -> None:
        self._files["serial"].write(line + b"\n")  # type: ignore[union-attr]
        self.counts.serial_lines += 1
        self._window_serial += 1
        self.counts.last_serial = now

    def _handle_mark(self, label: str) -> None:
        stamp, resolved = marks_mod.append(self.session.marks_log, label)
        self.counts.marks += 1
        self.notice(f"mark {self.counts.marks}: {resolved} @ {stamp:.3f}")

    def _drain_ready(self, timeout: float, now: float) -> None:
        for key, _events in self._selector.select(timeout=timeout):
            name, buffer = key.data
            for line in buffer.read():
                if name == "can:out":
                    self._handle_can(line, now)
                elif name == "serial:out":
                    self._handle_serial(line, now)
                elif name in {"stdin", "fifo"}:
                    self._handle_mark(line.decode("utf-8", "replace"))
                else:  # a child's stderr
                    text = line.decode("utf-8", "replace").strip()
                    if text:
                        self.notice(text)
            if buffer.at_eof:
                self._selector.unregister(key.fd)
                if name == "fifo":
                    # A writer closing the FIFO looks like EOF. Reopen so the
                    # next `bikelog mark` still gets through.
                    self._reopen_fifo()
                elif name.endswith(":out"):
                    self.notice(f"{name.split(':')[0]} stream ended")

    def _reopen_fifo(self) -> None:
        if self._fifo_fd is not None:
            os.close(self._fifo_fd)
        self._fifo_fd = marks_mod.open_fifo_read(self.session.marks_fifo)
        self._selector.register(
            self._fifo_fd, selectors.EVENT_READ, ("fifo", LineBuffer(self._fifo_fd))
        )

    def _install_signal_handlers(self) -> dict[int, object]:
        """Stop cleanly on SIGTERM as well as Ctrl-C.

        `tmux kill-session` sends SIGTERM, and a session killed that way must
        still finalise its meta.json rather than be left looking unclean.
        """
        def stop(_signum: int, _frame: object) -> None:
            self._stop = True

        previous = {}
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                previous[signum] = signal.getsignal(signum)
                signal.signal(signum, stop)
            except ValueError:
                # Only the main thread may install handlers. Off it, the caller
                # stops the loop by setting `_stop` directly.
                return {}
        return previous

    def run(self) -> Counts:
        previous = self._install_signal_handlers()
        self._setup()
        self._window_start = time.time()
        next_status = self._window_start

        try:
            while not self._stop:
                now = time.time()
                self._drain_ready(timeout=0.2, now=now)

                now = time.time()
                if now >= next_status:
                    window = now - self._window_start
                    if window > 0:
                        self._can_rate = self._window_can / window
                        self._serial_rate = self._window_serial / window
                    self._window_start = now
                    self._window_can = 0
                    self._window_serial = 0
                    self._status(now)
                    next_status = now + STATUS_INTERVAL_S
        except KeyboardInterrupt:  # stdin-less contexts can still raise it
            pass
        finally:
            sys.stderr.write("\n")
            for signum, handler in previous.items():
                signal.signal(signum, handler)  # type: ignore[arg-type]
            self._shutdown()
        return self.counts

    # -- teardown ----------------------------------------------------------

    def _shutdown(self) -> None:
        for name, child in self._children:
            if child.poll() is None:
                child.send_signal(signal.SIGTERM)

        # Let the children flush what they were holding before closing the files.
        deadline = time.time() + SHUTDOWN_GRACE_S
        while time.time() < deadline and any(
            child.poll() is None for _name, child in self._children
        ):
            self._drain_ready(timeout=0.1, now=time.time())

        for name, child in self._children:
            if child.poll() is None:
                self.notice(f"{name} did not stop; killing it")
                child.kill()
            child.wait(timeout=2)

        self._drain_ready(timeout=0.0, now=time.time())
        for key in list(self._selector.get_map().values()):
            _name, buffer = key.data
            for line in buffer.drain():
                if _name == "can:out":
                    self._handle_can(line, time.time())
                elif _name == "serial:out":
                    self._handle_serial(line, time.time())

        self._selector.close()
        for handle in self._files.values():
            handle.flush()  # type: ignore[union-attr]
            handle.close()  # type: ignore[union-attr]
        if self._fifo_fd is not None:
            os.close(self._fifo_fd)
        self.session.marks_fifo.unlink(missing_ok=True)
