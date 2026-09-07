"""Reading marks.log -- the operator's annotations.

Format is "<epoch.micros> <label>", written by bikelog while recording.
"""

from __future__ import annotations

import re
from pathlib import Path

LINE_RE = re.compile(r"^(?P<ts>\d+\.\d+) (?P<label>.*)$")


def load(path: Path):
    import pandas as pd

    rows = []
    if path.is_file():
        for line_no, raw in enumerate(path.open(encoding="utf-8"), start=1):
            match = LINE_RE.match(raw.rstrip("\n"))
            if match is not None:
                rows.append(
                    {
                        "timestamp": float(match.group("ts")),
                        "label": match.group("label"),
                        "line_no": line_no,
                    }
                )
    frame = pd.DataFrame(rows, columns=["timestamp", "label", "line_no"])
    if frame.empty:
        return frame
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    frame["dt"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
    return frame
