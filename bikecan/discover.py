"""Narrowing down where the information is.

Nothing here decides what a signal means. Its job is to shrink the search
space, in the order the notebooks work through it:

  inventory()        what is on the bus, how often, and where it varies
  byte_detail()      which bytes of one message carry information
  bit_detail()       the same at bit resolution, for flags and packed fields
  request_response() who asks and who answers
  candidate_fields() contiguous runs of active bits, worth fitting as a value

The first useful result is negative: a byte that never changes across every
session is a constant, not a signal, and most bytes on this bus are constant.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MAX_PAYLOAD = 8
# A message whose inter-arrival spread is small relative to its period is being
# sent on a timer; anything looser is event-driven.
PERIODIC_JITTER_RATIO = 0.25


def _payload_matrix(frames: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Payloads as an (n, 8) uint8 array, plus a mask of which bytes are real.

    Short payloads are padded, so the mask is what stops a pad byte being read
    as a constant zero -- the 7-byte reply 05124609 would otherwise look as if
    it had a zero eighth byte.
    """
    count = len(frames)
    values = np.zeros((count, MAX_PAYLOAD), dtype=np.uint8)
    valid = np.zeros((count, MAX_PAYLOAD), dtype=bool)
    for row, payload in enumerate(frames["data"].to_numpy()):
        length = min(len(payload), MAX_PAYLOAD)
        if length:
            values[row, :length] = np.frombuffer(payload[:length], dtype=np.uint8)
        valid[row, :length] = True
    return values, valid


def _entropy(column: np.ndarray) -> float:
    """Shannon entropy of one byte position, in bits (0 to 8)."""
    if column.size == 0:
        return 0.0
    _, counts = np.unique(column, return_counts=True)
    probabilities = counts / counts.sum()
    return float(-(probabilities * np.log2(probabilities)).sum())


def _periods(timestamps: np.ndarray) -> tuple[float, float, bool]:
    """Median inter-arrival gap in ms, its IQR, and whether it looks periodic."""
    if timestamps.size < 3:
        return (float("nan"), float("nan"), False)
    deltas = np.diff(np.sort(timestamps)) * 1000.0
    median = float(np.median(deltas))
    iqr = float(np.percentile(deltas, 75) - np.percentile(deltas, 25))
    periodic = median > 0 and (iqr / median) < PERIODIC_JITTER_RATIO
    return (median, iqr, bool(periodic))


def inventory(can: pd.DataFrame) -> pd.DataFrame:
    """One row per identifier: rate, periodicity, and where it varies.

    `rate_hz` is count over the full span, so it under-reports badly when the
    input spans several recordings with gaps between them. `period_ms`, the
    median inter-arrival gap, is the figure to trust in that case.
    """
    if not len(can):
        return pd.DataFrame()

    rows = []
    for id_hex, group in can.groupby("id_hex", sort=False):
        timestamps = group["timestamp"].to_numpy(dtype=float)
        span = float(timestamps.max() - timestamps.min())
        values, valid = _payload_matrix(group)

        changing_bytes = []
        entropies = []
        for index in range(MAX_PAYLOAD):
            column = values[valid[:, index], index]
            if column.size == 0:
                entropies.append(float("nan"))
                continue
            entropies.append(_entropy(column))
            if np.unique(column).size > 1:
                changing_bytes.append(index)

        # Bits that are not identical across every frame of this id.
        bits = np.unpackbits(values, axis=1)
        bit_valid = np.repeat(valid, 8, axis=1)
        changing_bits = [
            index
            for index in range(bits.shape[1])
            if bit_valid[:, index].any()
            and np.unique(bits[bit_valid[:, index], index]).size > 1
        ]

        median_ms, iqr_ms, periodic = _periods(timestamps)
        rows.append(
            {
                "id_hex": id_hex,
                "count": len(group),
                "span_s": round(span, 3),
                "rate_hz": round(len(group) / span, 3) if span > 0 else float("nan"),
                "period_ms": round(median_ms, 2) if median_ms == median_ms else None,
                "jitter_ms": round(iqr_ms, 2) if iqr_ms == iqr_ms else None,
                "periodic": periodic,
                "dlc": ",".join(str(d) for d in sorted(group["dlc"].unique())),
                "distinct_payloads": group["data_hex"].nunique(),
                "changing_bytes": ",".join(str(b) for b in changing_bytes) or "-",
                "changing_bits": len(changing_bits),
                # abs() keeps a single-valued byte at 0.0 rather than -0.0.
                "max_byte_entropy": round(abs(max(
                    (e for e in entropies if e == e), default=0.0)), 3),
                "priority": int(group["priority"].iloc[0]),
                "msg_type": f"{int(group['msg_type'].iloc[0]):02X}",
                "node_pair": group["node_pair"].iloc[0],
                "is_error": bool(group["is_error"].any()),
            }
        )

    result = pd.DataFrame(rows).sort_values("count", ascending=False)
    return result.reset_index(drop=True)


def byte_detail(can: pd.DataFrame, id_hex: str) -> pd.DataFrame:
    """Per-byte breakdown of one identifier: where the information actually is."""
    group = can[can["id_hex"] == id_hex]
    if not len(group):
        raise KeyError(f"no frames with id {id_hex}")

    values, valid = _payload_matrix(group)
    rows = []
    for index in range(MAX_PAYLOAD):
        column = values[valid[:, index], index]
        if column.size == 0:
            continue
        transitions = int((np.diff(column.astype(int)) != 0).sum())
        rows.append(
            {
                "byte": index,
                "distinct": int(np.unique(column).size),
                "min": int(column.min()),
                "max": int(column.max()),
                "transitions": transitions,
                "entropy_bits": round(abs(_entropy(column)), 3),
                "constant": bool(np.unique(column).size == 1),
                "first_value": f"{int(column[0]):02X}",
            }
        )
    return pd.DataFrame(rows)


def bit_detail(can: pd.DataFrame, id_hex: str) -> pd.DataFrame:
    """Per-bit breakdown, for flags and fields that do not sit on byte edges.

    Bit numbering is MSB-first within each byte, so bit 0 is the top bit of
    byte 0 -- the same order the payload is printed in.
    """
    group = can[can["id_hex"] == id_hex]
    if not len(group):
        raise KeyError(f"no frames with id {id_hex}")

    values, valid = _payload_matrix(group)
    bits = np.unpackbits(values, axis=1)
    bit_valid = np.repeat(valid, 8, axis=1)

    rows = []
    for index in range(bits.shape[1]):
        column = bits[bit_valid[:, index], index]
        if column.size == 0:
            continue
        transitions = int((np.diff(column.astype(int)) != 0).sum())
        rows.append(
            {
                "bit": index,
                "byte": index // 8,
                "bit_in_byte": index % 8,
                "ones": int(column.sum()),
                "zeros": int(column.size - column.sum()),
                "transitions": transitions,
                "active": bool(transitions > 0),
            }
        )
    return pd.DataFrame(rows)


def candidate_fields(can: pd.DataFrame, id_hex: str) -> pd.DataFrame:
    """Contiguous runs of active bits -- the things worth fitting as a value.

    A run of adjacent changing bits is far more likely to be one multi-bit
    field than several unrelated flags, so these are where signal fitting
    should start.
    """
    bits = bit_detail(can, id_hex)
    runs = []
    start = None
    for _, row in bits.iterrows():
        if row["active"] and start is None:
            start = int(row["bit"])
        elif not row["active"] and start is not None:
            runs.append((start, int(row["bit"]) - 1))
            start = None
    if start is not None:
        runs.append((start, int(bits["bit"].iloc[-1])))

    return pd.DataFrame(
        [
            {
                "start_bit": lo,
                "length": hi - lo + 1,
                "bytes": f"{lo // 8}-{hi // 8}",
                "byte_aligned": lo % 8 == 0 and (hi - lo + 1) % 8 == 0,
            }
            for lo, hi in runs
        ]
    )


def request_response(can: pd.DataFrame, window_ms: float = 10.0) -> pd.DataFrame:
    """Zero-length frames and whatever answered them.

    A zero-DLC frame carries no data, so it is asking rather than telling. In
    the existing recordings 02294609 is answered 1.5 ms later by 05124609:
    same node pair, different message type. Pairing these is the quickest way
    to work out which node polls which.
    """
    if not len(can):
        return pd.DataFrame()

    ordered = can.sort_values("timestamp").reset_index(drop=True)
    timestamps = ordered["timestamp"].to_numpy(dtype=float)
    requests = ordered[(ordered["dlc"] == 0) | (ordered["is_remote"])]

    rows = []
    for index, request in requests.iterrows():
        after = np.searchsorted(timestamps, request["timestamp"], side="right")
        limit = request["timestamp"] + window_ms / 1000.0
        for candidate in range(after, len(ordered)):
            if timestamps[candidate] > limit:
                break
            reply = ordered.iloc[candidate]
            if reply["id_hex"] == request["id_hex"]:
                continue
            rows.append(
                {
                    "request_id": request["id_hex"],
                    "reply_id": reply["id_hex"],
                    "gap_ms": round(
                        (reply["timestamp"] - request["timestamp"]) * 1000, 3
                    ),
                    "same_node_pair": request["node_pair"] == reply["node_pair"],
                    "request_type": f"{int(request['msg_type']):02X}",
                    "reply_type": f"{int(reply['msg_type']):02X}",
                    "reply_data": reply["data_hex"],
                    "timestamp": request["timestamp"],
                }
            )
            break  # only the first reply is a reply

    return pd.DataFrame(rows)


def node_summary(can: pd.DataFrame) -> pd.DataFrame:
    """Traffic grouped by the hypothesised node pair.

    If AA and BB really are node addresses, these groups correspond to physical
    components talking to each other, and each row is a subsystem to identify.
    """
    if not len(can):
        return pd.DataFrame()
    grouped = can.groupby("node_pair").agg(
        frames=("id_hex", "size"),
        ids=("id_hex", "nunique"),
        id_list=("id_hex", lambda values: ",".join(sorted(set(values)))),
        msg_types=("msg_type", "nunique"),
        priorities=("priority", lambda values: ",".join(
            f"{v:02X}" for v in sorted(set(values)))),
    )
    return grouped.sort_values("frames", ascending=False).reset_index()
