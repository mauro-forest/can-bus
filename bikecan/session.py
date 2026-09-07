"""Loading a session: CAN frames, serial lines and marks on one time base.

    from bikecan import session
    s = session.load("data/sessions/2026-09-07T14-03-11_headlight-test")
    s.can, s.serial, s.marks     # three DataFrames, all keyed on epoch seconds

The single shared time base is the whole point. Everything downstream -- window
diffs, serial correlation, request/response pairing -- assumes it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from bikecan import logreader, marksreader, serialreader


@dataclass
class Session:
    path: Path
    meta: dict
    can: object  # pandas DataFrame
    serial: object
    marks: object
    has_meta: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def session_id(self) -> str:
        return str(self.meta.get("session_id", self.path.name))

    @property
    def label(self) -> str:
        return str(self.meta.get("label", ""))

    @property
    def clean(self) -> bool:
        return bool(self.meta.get("clean_shutdown", False))

    @property
    def span(self) -> tuple[float, float]:
        if len(self.can):
            return float(self.can["timestamp"].min()), float(self.can["timestamp"].max())
        return (0.0, 0.0)

    def summary(self) -> str:
        start, end = self.span
        lines = [
            f"{self.session_id}  {self.label}",
            f"  {len(self.can):,} frames, {self.can['id_hex'].nunique() if len(self.can) else 0} ids"
            f" over {end - start:,.1f}s",
            f"  {len(self.serial):,} serial lines, {len(self.marks)} marks",
        ]
        if self.has_meta and not self.clean:
            lines.append("  UNCLEAN: no end time recorded; the Pi stopped abruptly")
        if self.meta.get("clock", {}).get("ntp_synchronized") is False:
            lines.append("  clock was not NTP-synced: do not compare with other sessions")
        lines.extend(f"  {w}" for w in self.warnings)
        return "\n".join(lines)


def _deduplicate(paths: list[Path]) -> tuple[list[Path], list[str]]:
    """Drop byte-identical copies of the same log.

    data/sessions/legacy holds one recording twice: the same bytes saved once
    under a descriptive name and once under the plain candump name. Loading
    both double-counts every frame in it -- which showed up as a periodic
    counter appearing to stand still. Both files stay on disk; only one is read.
    """
    seen: dict[str, Path] = {}
    keep: list[Path] = []
    notes: list[str] = []
    for candidate in paths:
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if digest in seen:
            notes.append(
                f"skipped {candidate.name}: byte-identical to {seen[digest].name}"
            )
            continue
        seen[digest] = candidate
        keep.append(candidate)
    return keep, notes


def load(path: str | Path) -> Session:
    """Load a session directory.

    Also accepts a directory of loose candump logs with no meta.json -- which
    is what data/sessions/legacy/ is, and those recordings still matter.
    """
    path = Path(path)
    if not path.is_dir():
        raise NotADirectoryError(f"{path} is not a session directory")

    warnings: list[str] = []
    meta_path = path / "meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text())
        can_logs = [path / "can0.log"]
        serial_log = path / "serial.log"
        marks_log = path / "marks.log"
    else:
        # Recorded before this tooling existed: logs only, no metadata.
        meta = {"session_id": path.name, "label": "(legacy: no metadata)"}
        can_logs = [
            p for p in sorted(path.glob("*.log"))
            if p.name not in {"serial.log", "marks.log"}
        ]
        serial_log = path / "serial.log"
        marks_log = path / "marks.log"
        can_logs, duplicate_notes = _deduplicate(can_logs)
        warnings.append(
            f"no meta.json: {len(can_logs)} loose log(s), no marks and no clock record"
        )
        warnings.extend(duplicate_notes)

    can = logreader.load([p for p in can_logs if p.is_file()])
    serial = serialreader.load(serial_log)
    marks = marksreader.load(marks_log)

    if len(can) and can["is_error"].any():
        count = int(can["is_error"].sum())
        warnings.append(f"{count} error frames: the interface bitrate may be wrong")

    return Session(
        path=path,
        meta=meta,
        can=can,
        serial=serial,
        marks=marks,
        has_meta=meta_path.is_file(),
        warnings=warnings,
    )


def load_all(root: str | Path = "data/sessions") -> list[Session]:
    """Every session under `root`, oldest first by directory name."""
    root = Path(root)
    if not root.is_dir():
        return []
    return [load(entry) for entry in sorted(root.iterdir()) if entry.is_dir()]


def combine(sessions: list[Session]):
    """One CAN DataFrame across sessions, with a session_id column.

    A byte that never changes in one session might change in another; questions
    about what is constant have to be asked across everything at once.
    """
    import pandas as pd

    frames = []
    for item in sessions:
        if not len(item.can):
            continue
        block = item.can.copy()
        block["session_id"] = item.session_id
        frames.append(block)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("timestamp")
