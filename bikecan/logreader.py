"""Reading candump log files into a DataFrame.

Log format, one frame per line, as written by `candump -l` / `candump -L`:

    (1788538180.786114) can0 03FF1000#0840112200000001
    (1788538877.589377) can0 02294609#                  <- zero-length request
    (1788543439.745037) can0 02407600#0100000000000000

The timestamp is absolute epoch seconds, which is what makes a CAN log and a
serial log comparable at all. The format is also what `canplayer` replays, so
it stays the on-disk format throughout this project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from bikecan import ids

# "(<epoch>) <iface> <id>#<data>", where data may be empty, "R"/"R<len>" for a
# remote frame, or preceded by a second '#' for CAN FD.
LINE_RE = re.compile(
    r"^\((?P<ts>\d+\.\d+)\)\s+(?P<channel>\S+)\s+(?P<id>[0-9A-Fa-f]+)"
    r"(?P<sep>#\#?)(?P<rest>\S*)\s*$"
)


@dataclass(frozen=True)
class Frame:
    timestamp: float
    channel: str
    can_id: int
    is_extended: bool
    is_error: bool
    is_remote: bool
    is_fd: bool
    dlc: int
    data: bytes
    source: str
    line_no: int


class ParseError(ValueError):
    pass


def parse_line(line: str, source: str = "", line_no: int = 0) -> Frame | None:
    """Parse one line. Returns None for a blank line; raises on a malformed one."""
    if not line.strip():
        return None

    match = LINE_RE.match(line)
    if match is None:
        raise ParseError(f"{source}:{line_no}: cannot parse {line.strip()!r}")

    id_text = match.group("id")
    raw_id = int(id_text, 16)
    rest = match.group("rest")
    is_fd = match.group("sep") == "##"

    is_remote = False
    payload = b""
    dlc = 0

    if is_fd:
        # FD frames prefix the payload with a flags nibble.
        payload = bytes.fromhex(rest[1:]) if len(rest) > 1 else b""
        dlc = len(payload)
    elif rest.startswith(("R", "r")):
        is_remote = True
        dlc = int(rest[1:]) if len(rest) > 1 else 0
    elif rest:
        if len(rest) % 2:
            raise ParseError(f"{source}:{line_no}: odd-length payload {rest!r}")
        payload = bytes.fromhex(rest)
        dlc = len(payload)

    return Frame(
        timestamp=float(match.group("ts")),
        channel=match.group("channel"),
        can_id=raw_id & ids.CAN_EFF_MASK,
        # candump prints 3 hex digits for a standard id and 8 for an extended
        # one; the width is the only thing that distinguishes them on disk.
        is_extended=len(id_text) > 3,
        is_error=ids.is_error_frame(raw_id),
        is_remote=is_remote,
        is_fd=is_fd,
        dlc=dlc,
        data=payload,
        source=source,
        line_no=line_no,
    )


def read_frames(path: Path, strict: bool = False) -> Iterator[Frame]:
    """Yield every frame in one log file.

    Non-strict by default: a truncated final line is exactly what a session cut
    short by a power cut looks like, and losing the whole file over it would be
    the wrong trade.
    """
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, start=1):
            try:
                frame = parse_line(line, source=path.name, line_no=line_no)
            except ParseError:
                if strict:
                    raise
                continue
            if frame is not None:
                yield frame


def read_many(paths: Iterable[Path], strict: bool = False) -> Iterator[Frame]:
    for path in paths:
        yield from read_frames(path, strict=strict)


def to_dataframe(frames: Iterable[Frame]):
    """Frames as a pandas DataFrame, indexed by timestamp and sorted.

    Adds the hypothesised identifier fields as columns so a notebook can group
    by node or message type without decoding by hand.
    """
    import pandas as pd

    rows = []
    for frame in frames:
        decoded = ids.decode(frame.can_id)
        rows.append(
            {
                "timestamp": frame.timestamp,
                "channel": frame.channel,
                "can_id": frame.can_id,
                "id_hex": f"{frame.can_id:08X}" if frame.is_extended else f"{frame.can_id:03X}",
                "priority": decoded.priority,
                "msg_type": decoded.msg_type,
                "node_a": decoded.node_a,
                "node_b": decoded.node_b,
                "node_pair": decoded.node_pair,
                "is_extended": frame.is_extended,
                "is_error": frame.is_error,
                "is_remote": frame.is_remote,
                "dlc": frame.dlc,
                "data": frame.data,
                "data_hex": frame.data.hex().upper(),
                "source": frame.source,
            }
        )

    columns = [
        "timestamp", "channel", "can_id", "id_hex", "priority", "msg_type",
        "node_a", "node_b", "node_pair", "is_extended", "is_error",
        "is_remote", "dlc", "data", "data_hex", "source",
    ]
    frame_df = pd.DataFrame(rows, columns=columns)
    if frame_df.empty:
        return frame_df
    frame_df = frame_df.sort_values("timestamp").reset_index(drop=True)
    frame_df["dt"] = pd.to_datetime(frame_df["timestamp"], unit="s", utc=True)
    return frame_df


def load(paths: Iterable[Path], strict: bool = False):
    """Read every given log file into one DataFrame."""
    return to_dataframe(read_many(list(paths), strict=strict))


def main(argv: list[str] | None = None) -> int:
    """`python -m bikecan.logreader <logs...>` -- the inventory, as a table."""
    import argparse

    parser = argparse.ArgumentParser(description="Summarise candump logs.")
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    frames = list(read_many(args.logs, strict=args.strict))
    if not frames:
        print("no frames")
        return 1

    from collections import Counter

    counts = Counter(
        (f"{frame.can_id:08X}" if frame.is_extended else f"{frame.can_id:03X}")
        for frame in frames
    )
    span = max(f.timestamp for f in frames) - min(f.timestamp for f in frames)
    print(f"{len(frames):,} frames, {len(counts)} ids, {len(args.logs)} files")
    print(f"span {span:,.1f}s")
    errors = sum(1 for frame in frames if frame.is_error)
    if errors:
        print(f"{errors} error frames")
    print()
    print(f"{'id':<10} {'count':>8}  {'dlc':>3}  payloads")
    for id_hex, count in counts.most_common():
        matching = [f for f in frames if
                    (f"{f.can_id:08X}" if f.is_extended else f"{f.can_id:03X}") == id_hex]
        dlcs = sorted({f.dlc for f in matching})
        distinct = len({f.data for f in matching})
        dlc_text = ",".join(str(d) for d in dlcs)
        print(f"{id_hex:<10} {count:>8}  {dlc_text:>3}  {distinct} distinct")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
