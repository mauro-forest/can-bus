"""Turning marks into windows, and windows into findings.

A bench experiment is: record, mark the instant you do something, then ask what
was different inside that window. `bikelog` writes the marks; this module turns
them into comparisons.

Two marks with the same label bracket a window ("headlight on" ... "headlight
on"); a lone mark opens a window of `default_duration` seconds. Baselines are
taken from the quiet stretch before the first mark, because "different from
normal" is only meaningful against a definition of normal.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

DEFAULT_WINDOW_S = 5.0
BASELINE_GUARD_S = 1.0


@dataclass(frozen=True)
class Window:
    label: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def slice(self, frames: pd.DataFrame) -> pd.DataFrame:
        return frames[
            (frames["timestamp"] >= self.start) & (frames["timestamp"] <= self.end)
        ]

    def __str__(self) -> str:
        return f"{self.label} [{self.start:.3f}, {self.end:.3f}] {self.duration:.1f}s"


def windows(marks: pd.DataFrame, default_duration: float = DEFAULT_WINDOW_S) -> list[Window]:
    """Marks in, windows out. Repeated labels pair up into brackets."""
    if not len(marks):
        return []

    result: list[Window] = []
    open_marks: dict[str, float] = {}
    for _, mark in marks.sort_values("timestamp").iterrows():
        label = str(mark["label"])
        stamp = float(mark["timestamp"])
        if label in open_marks:
            result.append(Window(label, open_marks.pop(label), stamp))
        else:
            open_marks[label] = stamp

    # A label marked once is a moment, not a bracket: give it a fixed window.
    for label, stamp in open_marks.items():
        result.append(Window(label, stamp, stamp + default_duration))

    return sorted(result, key=lambda window: window.start)


def baseline(can: pd.DataFrame, before: float, guard: float = BASELINE_GUARD_S) -> pd.DataFrame:
    """Everything up to `guard` seconds before a window opens.

    The guard exists because a mark is typed by a person: the thing being marked
    often happens a moment before the Enter key does.
    """
    return can[can["timestamp"] < (before - guard)]


def diff_window(
    can: pd.DataFrame, window: Window, reference: pd.DataFrame | None = None
) -> pd.DataFrame:
    """What is different inside `window` compared with `reference`.

    Answers the question a stimulus experiment actually asks: which identifiers
    appeared only during the stimulus, and which ones carried payloads they had
    never carried before.
    """
    inside = window.slice(can)
    if reference is None:
        reference = baseline(can, window.start)

    reference_ids = set(reference["id_hex"])
    reference_payloads = set(zip(reference["id_hex"], reference["data_hex"]))

    rows = []
    for id_hex, group in inside.groupby("id_hex", sort=False):
        payloads = set(group["data_hex"])
        new_payloads = {
            payload for payload in payloads
            if (id_hex, payload) not in reference_payloads
        }
        rows.append(
            {
                "id_hex": id_hex,
                "frames_in_window": len(group),
                "new_id": id_hex not in reference_ids,
                "new_payloads": len(new_payloads),
                "example_new_payload": sorted(new_payloads)[0] if new_payloads else "",
                "interesting": id_hex not in reference_ids or bool(new_payloads),
            }
        )

    result = pd.DataFrame(rows)
    if not len(result):
        return result
    return result.sort_values(
        ["interesting", "new_id", "new_payloads"], ascending=False
    ).reset_index(drop=True)


def report(can: pd.DataFrame, marks: pd.DataFrame) -> str:
    """A readable pass over every window in a session."""
    found = windows(marks)
    if not found:
        return "no marks in this session: nothing to compare"

    lines = []
    for window in found:
        lines.append(str(window))
        diff = diff_window(can, window)
        interesting = diff[diff["interesting"]] if len(diff) else diff
        if not len(interesting):
            lines.append("  nothing new -- the stimulus produced no unseen traffic")
            continue
        for _, row in interesting.iterrows():
            note = "NEW ID" if row["new_id"] else f"{row['new_payloads']} new payloads"
            lines.append(
                f"  {row['id_hex']}  {row['frames_in_window']:>5} frames  {note}"
                + (f"  e.g. {row['example_new_payload']}" if row["example_new_payload"] else "")
            )
        lines.append("")
    return "\n".join(lines)
