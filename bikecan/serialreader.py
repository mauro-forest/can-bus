"""Reading the timestamped serial log into a DataFrame.

Lines are written by bikelog.serial_reader as "<epoch.micros> <text>". The
epoch comes from the Pi's clock -- the same clock candump reads -- so serial
lines and CAN frames share one time base and can be put on one axis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

LINE_RE = re.compile(r"^(?P<ts>\d+\.\d+) (?P<text>.*)$")


@dataclass(frozen=True)
class SerialLine:
    timestamp: float
    text: str
    source: str
    line_no: int


def read_lines(path: Path) -> Iterator[SerialLine]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, raw in enumerate(handle, start=1):
            match = LINE_RE.match(raw.rstrip("\n"))
            if match is None:
                # An untimestamped line means the log was written by something
                # other than bikelog. Keep it, with no time -- dropping data is
                # worse than carrying a NaN.
                text = raw.rstrip("\n")
                if text:
                    yield SerialLine(float("nan"), text, path.name, line_no)
                continue
            yield SerialLine(
                float(match.group("ts")), match.group("text"), path.name, line_no
            )


def to_dataframe(lines):
    import pandas as pd

    rows = [
        {
            "timestamp": line.timestamp,
            "text": line.text,
            "source": line.source,
            "line_no": line.line_no,
        }
        for line in lines
    ]
    frame = pd.DataFrame(rows, columns=["timestamp", "text", "source", "line_no"])
    if frame.empty:
        return frame
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    frame["dt"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
    return frame


def load(path: Path):
    return to_dataframe(read_lines(path))


def grep(frame, pattern: str, case: bool = False):
    """Serial lines matching a regex -- the usual way into a session.

    A cellular command arriving here (unlock, immobilise) is the anchor for
    finding the CAN frames it produces, which no bench test can trigger.
    """
    if frame.empty:
        return frame
    return frame[frame["text"].str.contains(pattern, case=case, regex=True, na=False)]
