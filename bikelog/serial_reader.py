"""Reads the IoT module's debug console and stamps every line with the time.

Run as a child of `bikelog start`, which redirects stdout into serial.log.
Notices go to stderr so they never end up in the log.

The console prints untimestamped free-form text, so the timestamp this module
adds is the only thing that ties a serial line to a CAN frame. It has to come
from the Pi's clock -- the same clock `candump` reads -- which is why the
stamping happens here at the moment of the read, and not later.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from typing import IO

# A debug console does not always terminate its last line promptly. After this
# long with no further bytes, emit what we have rather than hold it back.
PARTIAL_LINE_IDLE_S = 2.0
REOPEN_BACKOFF_S = 2.0


def _emit(out: IO[str], stamp: float, payload: bytes) -> None:
    # Escape rather than drop: a console that emits a stray non-UTF8 byte is
    # telling us something, and a lost line is worse than an ugly one.
    text = payload.decode("utf-8", errors="backslashreplace").rstrip("\r\n")
    out.write(f"{stamp:.6f} {text}\n")
    out.flush()  # per line, so an abrupt stop loses at most one


def _notify(message: str) -> None:
    print(f"serial: {message}", file=sys.stderr, flush=True)


def run(
    port: str,
    baudrate: int,
    timeout: float,
    out: IO[str] = sys.stdout,
) -> int:
    try:
        import serial  # pyserial
    except ImportError:
        _notify("pyserial is not installed (uv sync --group capture)")
        return 2

    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # the parent owns Ctrl-C

    handle = None
    pending = bytearray()
    pending_at = 0.0

    while not stopping:
        if handle is None:
            try:
                handle = serial.Serial(port, baudrate, timeout=timeout)
            except Exception as exc:  # pyserial raises several unrelated types
                _notify(f"cannot open {port}: {exc}")
                # Sleep in slices so SIGTERM is not delayed by the whole backoff.
                for _ in range(int(REOPEN_BACKOFF_S * 10)):
                    if stopping:
                        return 0
                    time.sleep(0.1)
                continue
            _notify(f"reading {port} at {baudrate} baud")

        try:
            chunk = handle.read(handle.in_waiting or 1)
        except Exception as exc:
            _notify(f"read failed, will reopen: {exc}")
            handle.close()
            handle = None
            continue

        now = time.time()
        if chunk:
            if not pending:
                # Stamp the line at the arrival of its first byte, which is
                # closer to when the device said it than the newline is.
                pending_at = now
            pending.extend(chunk)
            while b"\n" in pending:
                line, _, rest = bytes(pending).partition(b"\n")
                _emit(out, pending_at, line)
                pending = bytearray(rest)
                pending_at = now
        elif pending and (now - pending_at) > PARTIAL_LINE_IDLE_S:
            _emit(out, pending_at, bytes(pending))
            pending.clear()

    if pending:
        _emit(out, pending_at, bytes(pending))
    if handle is not None:
        handle.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--baudrate", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=1.0)
    args = parser.parse_args(argv)
    return run(args.port, args.baudrate, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
