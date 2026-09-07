"""Operator annotations.

Knowing that the headlight was switched on at 1788539377.6 is what turns a wall
of frames into an experiment. Two ways in, both landing in the same marks.log:

  * type a label into the running `bikelog start` and press Enter
  * `bikelog mark "..."` from another tmux pane, which writes to a FIFO in the
    session directory that the running capture is watching
"""

from __future__ import annotations

import errno
import os
import time
from pathlib import Path

UNLABELLED = "(mark)"


def append(marks_log: Path, label: str, stamp: float | None = None) -> tuple[float, str]:
    """Append one mark. A bare Enter is a valid mark -- both hands may be busy."""
    stamp = time.time() if stamp is None else stamp
    label = label.strip() or UNLABELLED
    with marks_log.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp:.6f} {label}\n")
        handle.flush()
    return stamp, label


def make_fifo(path: Path) -> None:
    if path.exists():
        path.unlink()
    os.mkfifo(path, 0o600)


def open_fifo_read(path: Path) -> int:
    """Open the FIFO for reading without blocking on a writer appearing."""
    return os.open(path, os.O_RDONLY | os.O_NONBLOCK)


def send(fifo: Path, label: str) -> None:
    """Used by `bikelog mark`. Raises if no capture is listening."""
    if not fifo.exists():
        raise FileNotFoundError(f"no mark FIFO at {fifo}; is a capture running?")
    try:
        fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ENXIO:
            raise RuntimeError(
                f"nothing is reading {fifo}; the capture is not running"
            ) from exc
        raise
    try:
        os.write(fd, f"{label.strip()}\n".encode())
    finally:
        os.close(fd)
