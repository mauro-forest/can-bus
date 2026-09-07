"""Session directories and their meta.json.

A session is a directory, not a file:

    2026-09-07T14-03-11_headlight-test/
        can0.log     candump log format, absolute epoch timestamps
        serial.log   "<epoch.micros> <line>"
        marks.log    "<epoch.micros> <label>"
        meta.json    everything needed to interpret the three above

meta.json records the clock state on purpose. A session recorded before the Pi
reached an NTP server has timestamps that cannot be compared with any other
session, and the only way to know that later is to have written it down at the
time.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CAN_LOG = "can0.log"
SERIAL_LOG = "serial.log"
MARKS_LOG = "marks.log"
MARKS_FIFO = "marks.fifo"
META = "meta.json"

_REPO_ROOT = Path(__file__).resolve().parent.parent


def slugify(label: str) -> str:
    """Turn a free-text label into something safe for a directory name."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    return slug[:48] or "session"


def git_rev() -> str:
    """The deployed revision, suffixed `-dirty` when it does not match HEAD.

    Recorded in every session so a recording made with an old build of the
    capture code stays identifiable. Returns "unknown" off a checkout.
    """
    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    sha = git("rev-parse", "--short", "HEAD")
    if sha is None:
        # Deployed by rsync rather than checked out; deploy.sh leaves the rev
        # it shipped in this file.
        stamped = _REPO_ROOT / ".deployed-rev"
        if stamped.is_file():
            return stamped.read_text().strip() or "unknown"
        return "unknown"

    dirty = git("status", "--porcelain")
    return f"{sha}-dirty" if dirty else sha


def clock_state() -> dict[str, object]:
    """Whether this machine's clock can be trusted, and what says so."""
    state: dict[str, object] = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "epoch": time.time(),
        "tz": time.strftime("%Z%z"),
    }
    try:
        out = subprocess.run(
            ["timedatectl", "show"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        state["ntp_synchronized"] = None
        return state

    if out.returncode != 0:
        state["ntp_synchronized"] = None
        return state

    fields = dict(
        line.split("=", 1) for line in out.stdout.splitlines() if "=" in line
    )
    state["ntp_synchronized"] = fields.get("NTPSynchronized") == "yes"
    state["timedatectl"] = fields
    return state


@dataclass
class Session:
    """A session directory on disk, plus the paths inside it."""

    path: Path
    session_id: str
    label: str
    started: float

    @property
    def can_log(self) -> Path:
        return self.path / CAN_LOG

    @property
    def serial_log(self) -> Path:
        return self.path / SERIAL_LOG

    @property
    def marks_log(self) -> Path:
        return self.path / MARKS_LOG

    @property
    def marks_fifo(self) -> Path:
        return self.path / MARKS_FIFO

    @property
    def meta_path(self) -> Path:
        return self.path / META

    def write_meta(self, extra: dict[str, object]) -> None:
        meta = {
            "session_id": self.session_id,
            "label": self.label,
            "started_epoch": self.started,
            "started_utc": datetime.fromtimestamp(
                self.started, timezone.utc
            ).isoformat(),
            "git_rev": git_rev(),
            "host": platform.node(),
            "uname": " ".join(platform.uname()),
            "clock": clock_state(),
            **extra,
        }
        self._merge(meta)

    def finalise(self, extra: dict[str, object]) -> None:
        """Mark the session complete. Its absence is how an unclean stop shows."""
        ended = time.time()
        self._merge(
            {
                "ended_epoch": ended,
                "ended_utc": datetime.fromtimestamp(ended, timezone.utc).isoformat(),
                "duration_s": round(ended - self.started, 3),
                "clean_shutdown": True,
                **extra,
            }
        )

    def _merge(self, updates: dict[str, object]) -> None:
        current: dict[str, object] = {}
        if self.meta_path.is_file():
            try:
                current = json.loads(self.meta_path.read_text())
            except json.JSONDecodeError:
                current = {}
        current.update(updates)
        tmp = self.meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.meta_path)


def create(root: Path, label: str) -> Session:
    """Make a new session directory under `root`."""
    started = time.time()
    stamp = datetime.fromtimestamp(started, timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    session_id = f"{stamp}_{slugify(label)}"
    path = root / session_id
    path.mkdir(parents=True, exist_ok=False)
    return Session(path=path, session_id=session_id, label=label, started=started)


def find_all(root: Path) -> list[Session]:
    """Every session under `root`, oldest first."""
    if not root.is_dir():
        return []
    sessions = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or not (entry / META).is_file():
            continue
        try:
            meta = json.loads((entry / META).read_text())
        except json.JSONDecodeError:
            continue
        sessions.append(
            Session(
                path=entry,
                session_id=str(meta.get("session_id", entry.name)),
                label=str(meta.get("label", "")),
                started=float(meta.get("started_epoch", 0.0)),
            )
        )
    return sessions


def latest(root: Path) -> Session | None:
    sessions = find_all(root)
    return sessions[-1] if sessions else None
