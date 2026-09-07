"""Generating dbc/ebike.dbc from dbc/signals.toml.

`signals.toml` is the human-editable record of what has been worked out, with
the evidence and a confidence level against every entry. `ebike.dbc` is
generated from it so that cantools, SavvyCAN, Vector tools and anything else
that speaks DBC can decode a capture automatically.

Editing the generated DBC by hand would lose the evidence, which is the part
that matters when someone asks in six months why a signal is scaled the way it
is. Edit the TOML; regenerate:

    uv run python -m bikecan.dbc

BIT NUMBERING

signals.toml counts bits MSB-first from byte 0, the same way `discover.bit_detail`
does and the same way the payload is printed: bit 0 is the top bit of byte 0,
bit 8 is the top bit of byte 1. DBC counts bits within each byte from the LSB,
so this module converts. Getting this wrong is the classic way to produce a DBC
that decodes plausible-looking nonsense, so there is a test for it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

DBC_DIR = Path(__file__).resolve().parent.parent / "dbc"
SIGNALS_TOML = DBC_DIR / "signals.toml"
OUTPUT_DBC = DBC_DIR / "ebike.dbc"

CONFIDENCE_LEVELS = ("hypothesis", "probable", "confirmed")


def dbc_start_bit(msb_first_index: int) -> int:
    """Convert an MSB-first bit index into DBC bit numbering.

    MSB-first index 0 is byte 0's top bit, which DBC calls bit 7.
    MSB-first index 22 is byte 2 bit 1, which DBC calls 17.
    """
    byte, bit_in_byte = divmod(msb_first_index, 8)
    return byte * 8 + (7 - bit_in_byte)


def load_signals(path: Path | None = None) -> dict:
    path = path or SIGNALS_TOML
    with path.open("rb") as handle:
        return tomllib.load(handle)


def build_database(spec: dict):
    """Turn the TOML spec into a cantools Database."""
    from cantools.database.can import Database, Message, Signal
    from cantools.database.conversion import BaseConversion

    messages = []
    for entry in spec.get("message", []):
        signals = []
        for signal in entry.get("signal", []):
            confidence = signal.get("confidence", "hypothesis")
            if confidence not in CONFIDENCE_LEVELS:
                raise ValueError(
                    f"{entry['name']}.{signal['name']}: confidence "
                    f"{confidence!r} is not one of {CONFIDENCE_LEVELS}"
                )
            byte_order = signal.get("byte_order", "big_endian")
            start = (
                dbc_start_bit(signal["start_bit"])
                if byte_order == "big_endian"
                else signal["start_bit"]
            )
            # The confidence and the evidence travel with the signal, so a DBC
            # handed to someone else still says which parts are guesses.
            comment = f"[{confidence}] {signal.get('evidence', '').strip()}".strip()
            # cantools >= 40 carries scale and offset in a conversion object
            # rather than as Signal keyword arguments.
            conversion = BaseConversion.factory(
                scale=signal.get("scale", 1),
                offset=signal.get("offset", 0),
                choices=signal.get("choices"),
                is_float=signal.get("is_float", False),
            )
            signals.append(
                Signal(
                    name=signal["name"],
                    start=start,
                    length=signal["length"],
                    byte_order=byte_order,
                    is_signed=signal.get("signed", False),
                    conversion=conversion,
                    unit=signal.get("unit") or None,
                    comment=comment,
                )
            )

        cycle = entry.get("cycle_time_ms")
        messages.append(
            Message(
                frame_id=int(entry["id"]),
                name=entry["name"],
                length=entry.get("length", 8),
                signals=signals,
                is_extended_frame=entry.get("extended", True),
                cycle_time=int(cycle) if cycle else None,
                comment=(
                    f"[{entry.get('confidence', 'hypothesis')}] "
                    f"{entry.get('comment', '').strip()}"
                ).strip(),
            )
        )

    database = Database(messages=messages)
    database.refresh()
    return database


def generate(spec_path: Path | None = None, out_path: Path | None = None) -> Path:
    spec = load_signals(spec_path)
    database = build_database(spec)
    out_path = out_path or OUTPUT_DBC
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Written as bytes: cantools emits CRLF, which is the DBC convention, and
    # text-mode reads would silently translate it and make the file look stale.
    out_path.write_bytes(database.as_dbc_string().encode("utf-8"))
    return out_path


def decode_session(can, database=None):
    """Decode a session's frames with the current DBC.

    Unknown identifiers are left alone rather than dropped: the point of the
    project is the frames that are still unknown.
    """
    if database is None:
        database = load_database()

    decoded = []
    for _, frame in can.iterrows():
        try:
            message = database.get_message_by_frame_id(int(frame["can_id"]))
            values = message.decode(bytes(frame["data"]), allow_truncated=True)
        except (KeyError, ValueError):
            values = None
        decoded.append(values)
    result = can.copy()
    result["decoded"] = decoded
    result["known"] = [values is not None for values in decoded]
    return result


def load_database(path: Path | None = None):
    import cantools

    return cantools.database.load_file(path or OUTPUT_DBC)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Regenerate dbc/ebike.dbc.")
    parser.add_argument("--signals", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    out = generate(args.signals, args.out)
    database = load_database(out)
    print(f"wrote {out}")
    for message in database.messages:
        print(f"  {message.frame_id:08X}  {message.name:<24} {len(message.signals)} signals")
        for signal in message.signals:
            print(
                f"      {signal.name:<20} start {signal.start:>2} "
                f"len {signal.length:>2} {signal.byte_order}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
