"""Bringing the SocketCAN interface up, and reading back what it actually is.

Read-only as far as the bus is concerned: configuring the local interface does
not put a frame on the wire.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class IfaceState:
    name: str
    exists: bool
    up: bool
    bitrate: int | None
    can_state: str | None  # SocketCAN's own view: ERROR-ACTIVE, BUS-OFF, ...

    def describe(self) -> str:
        if not self.exists:
            return f"{self.name}: not present"
        bits = f"{self.bitrate} bit/s" if self.bitrate else "bitrate unknown"
        state = self.can_state or "state unknown"
        return f"{self.name}: {'up' if self.up else 'down'}, {bits}, {state}"


def _ip(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ip", *args], capture_output=True, text=True, timeout=10, check=False
    )


def state(name: str) -> IfaceState:
    """Query the interface without changing anything."""
    if shutil.which("ip") is None:
        return IfaceState(name, False, False, None, None)

    out = _ip("-details", "-json", "link", "show", name)
    if out.returncode != 0:
        return IfaceState(name, False, False, None, None)

    try:
        links = json.loads(out.stdout)
    except json.JSONDecodeError:
        return IfaceState(name, True, False, None, None)
    if not links:
        return IfaceState(name, False, False, None, None)

    link = links[0]
    info = link.get("linkinfo", {}).get("info_data", {}) or {}
    bittiming = info.get("bittiming", {}) or {}
    bitrate = bittiming.get("bitrate")
    return IfaceState(
        name=name,
        exists=True,
        up="UP" in (link.get("flags") or []),
        bitrate=int(bitrate) if bitrate else None,
        can_state=info.get("state"),
    )


def ensure_up(name: str, bitrate: int) -> tuple[IfaceState, list[str]]:
    """Bring `name` up at `bitrate` if it is down. Idempotent.

    Returns the resulting state and a list of notes worth showing the operator
    -- most importantly a bitrate mismatch, which produces a log full of error
    frames rather than an obvious failure.
    """
    notes: list[str] = []
    current = state(name)

    if not current.exists:
        return current, [f"{name} does not exist -- is the CAN HAT overlay loaded?"]

    if not current.up:
        notes.append(f"{name} was down; bringing it up at {bitrate} bit/s")
        setup = _ip("link", "set", name, "up", "type", "can", "bitrate", str(bitrate))
        if setup.returncode != 0:
            stderr = setup.stderr.strip()
            # The bitrate cannot be set on an already-configured interface, and
            # a non-root user cannot set it at all. Both are worth saying plainly.
            notes.append(f"could not configure {name}: {stderr or 'ip link failed'}")
            notes.append(f"try: sudo ip link set {name} up type can bitrate {bitrate}")
            return state(name), notes
        current = state(name)

    if current.bitrate and current.bitrate != bitrate:
        notes.append(
            f"{name} is running at {current.bitrate} bit/s but the config says "
            f"{bitrate} bit/s -- captures will be full of error frames"
        )
    if current.can_state and current.can_state.upper() not in {"ERROR-ACTIVE", "STOPPED"}:
        notes.append(f"{name} reports CAN state {current.can_state}")

    return current, notes
