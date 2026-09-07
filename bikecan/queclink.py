"""Parsing the Queclink @Track reports on the IoT serial console.

The console is not a free-form debug log: it is the tracker's ASCII @Track
protocol, specified in `reference/ZK Series @Track Air Interface Protocol_V80.51.pdf`.
The device identifies itself as a `ZK115MGC` in its `+RESP:GTVER` report, so the
ZK series document is the right one.

WHY THIS MATTERS MORE THAN THE CAN STRUCTURE WORK

`+RESP:GTFRI` carries, on the same clock as the CAN log, values the bike
measures about itself: pack voltage, speed, total mileage, head and rear light
state, pack current, cell temperatures, lock state. That is **ground truth**,
and signal fitting had none before. A candidate CAN field is confirmed by
matching one of these, not by looking plausible.

The catch is cadence. Reports arrive every 240 s by default, so ground truth is
coarse: enough to pin a slow value like pack voltage or total mileage, not
enough to fit speed during a ride. For fast signals it gives you the value at
each report and you interpolate between marks.

Field numbering and names below follow the specification exactly (GTFRI in
section 3.8.2, ECU Info in 3.16.2.1) so the two can be checked against each
other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# +RESP:GTFRI, section 3.8.2. Order matters: these are positional.
GTFRI_FIELDS = [
    "protocol_version", "unique_id", "device_name", "vin", "qr_code",
    "reserved_6", "reserved_7", "report_type", "ecu_error_code", "gps_accuracy",
    "speed", "azimuth", "altitude", "longitude", "latitude", "gps_utc_time",
    "reserved_17", "mcc", "mnc", "lac", "cell_id", "csq", "network_type",
    "state", "power_supply", "main_power_voltage", "backup_battery_voltage",
    "backup_battery_percentage", "ecu_error_type", "alive", "ecu_lock_state",
    "task_id", "ecu_info", "scooter_battery_percentage", "generated_time",
    "count_number",
]

# The '&'-separated <ECU Info> compound, section 3.16.2.1.
ECU_INFO_FIELDS = [
    "speed", "current_mileage", "remaining_mileage", "total_mileage",
    "head_light_status", "rear_light_status", "ride_time", "firmware_version",
    "hardware_version", "bell_button_status", "charging", "battery_status",
    "battery_lock_state", "battery_lock_door", "battery_heating_plate",
]

# <ECU Error Code> bits, section 3.16.2.1. Names the components on the bus,
# which is why this is worth keeping: it is the vendor's own component list.
ECU_ERROR_BITS = {
    0: "head lamp fault",
    1: "meter firmware lost",
    2: "turning handle fault",
    3: "turning handle not returned",
    4: "left brake crank fault",
    5: "right brake crank fault",
    6: "left brake crank not returned",
    7: "right brake crank not returned",
    8: "ECU heartbeat error",
    9: "BMS heartbeat error",
    10: "meter heartbeat error",
    11: "abnormal communication with Hub-Lock",
    16: "over legal charging temperature",
    17: "discharging over heating",
    18: "charging under temperature",
    19: "discharging under temperature",
    20: "MOS over heating",
    21: "other over heating",
    22: "pre-discharge error",
    23: "pre-charge error",
    24: "under voltage first layer protection",
    25: "under voltage second layer protection",
    26: "over voltage first layer protection",
    27: "over voltage second layer protection",
    28: "over current first layer protection",
    29: "over current second layer protection",
    30: "over current third layer protection",
    31: "over current fourth layer protection",
    32: "cell temperature sensor damage",
    33: "large charging/discharging temperature difference",
    34: "charging fuse broken",
    35: "discharging fuse broken",
}

HEADER_RE = re.compile(r"^\+(?P<kind>RESP|ACK|BUFF):(?P<report>GT[A-Z]{3})$")


@dataclass
class Report:
    """One parsed @Track report."""

    kind: str  # RESP, ACK or BUFF
    report: str  # GTFRI, GTVER, GTRTO, ...
    fields: dict[str, Any] = field(default_factory=dict)
    raw: str = ""

    def __getitem__(self, key: str) -> Any:
        return self.fields[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.fields.get(key, default)


def _number(text: str) -> float | int | None:
    """Coerce a field to a number, or None when the device left it empty.

    Empty is meaningful in this protocol -- it means "not reported" -- so it
    must not become a zero.
    """
    if text is None or text == "":
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return None


def _hex_groups(text: str, count: int) -> list[int | None]:
    """Split a fixed-width hex string into `count` two-character groups."""
    if not text or len(text) < count * 2:
        return [None] * count
    groups = []
    for index in range(count):
        chunk = text[index * 2 : index * 2 + 2]
        try:
            groups.append(int(chunk, 16))
        except ValueError:
            groups.append(None)
    return groups


def parse_battery_status(text: str) -> dict[str, Any]:
    """The 20-character <Battery Status> field: 10 two-character groups.

    Groups 7-10 together are the pack current in mA, so they are recombined
    into one 32-bit value rather than reported separately.

    That value is SIGNED. The specification says only "Unit: mA", but a real
    capture produced 4294967209, which is 2**32 - 87: a discharge of 87 mA
    written as two's complement. Read unsigned it would look like a 4.3 million
    amp charge.
    """
    groups = _hex_groups(text, 10)
    mos = groups[0]
    current: int | None = None
    if all(group is not None for group in groups[6:10]):
        current = int(text[12:20], 16)
        if current >= 0x80000000:
            current -= 0x100000000

    return {
        "charging_mos_on": None if mos is None else bool(mos & 0x01),
        "discharging_mos_on": None if mos is None else bool(mos & 0x02),
        "battery_health_pct": groups[1],
        "cell_temp_max_c": groups[2],
        "cell_temp_min_c": groups[3],
        "mos_temp_c": groups[4],
        "other_temp_c": groups[5],
        "pack_current_ma": current,
    }


def parse_battery_heating(text: str) -> dict[str, Any]:
    """The 14-character <Battery Heating Plate> field: 7 two-character groups."""
    groups = _hex_groups(text, 7)
    return {
        "heating_temp_1_c": groups[0],
        "heating_temp_2_c": groups[1],
        "heating_temp_3_c": groups[2],
        "heating_temp_4_c": groups[3],
        "heating_temp_5_c": groups[4],
        "heating_plate_1_on": None if groups[5] is None else bool(groups[5]),
        "heating_plate_2_on": None if groups[6] is None else bool(groups[6]),
    }


def parse_ecu_info(text: str) -> dict[str, Any]:
    """The '&'-separated <ECU Info> compound: the bike's view of itself."""
    if not text:
        return {}

    parts = text.split("&")
    values: dict[str, Any] = {}
    for name, raw in zip(ECU_INFO_FIELDS, parts):
        values[f"ecu_{name}"] = raw

    numeric = [
        "speed", "current_mileage", "remaining_mileage", "total_mileage",
        "head_light_status", "rear_light_status", "ride_time",
        "bell_button_status", "charging", "battery_lock_state",
        "battery_lock_door",
    ]
    for name in numeric:
        key = f"ecu_{name}"
        if key in values:
            values[key] = _number(values[key])

    values.update(parse_battery_status(values.get("ecu_battery_status") or ""))
    values.update(parse_battery_heating(values.get("ecu_battery_heating_plate") or ""))
    return values


def decode_error_code(text: str) -> list[str]:
    """<ECU Error Code> as a list of active faults.

    Bits the specification does not define are reported as "undocumented bit N"
    rather than dropped. A real capture carries 0210000000000000 -- bits 52 and
    57, both outside the documented 0-35 range -- and silently returning an
    empty list there would report a bike with two active faults as healthy.
    """
    if not text:
        return []
    try:
        mask = int(text, 16)
    except ValueError:
        return []

    faults = [name for bit, name in sorted(ECU_ERROR_BITS.items()) if mask & (1 << bit)]
    unknown = [
        bit
        for bit in range(mask.bit_length())
        if mask & (1 << bit) and bit not in ECU_ERROR_BITS
    ]
    faults += [f"undocumented bit {bit}" for bit in unknown]
    return faults


def parse(text: str) -> Report | None:
    """Parse one @Track line. Returns None if it is not one.

    Unknown report types still come back, with their tokens numbered, because
    an unrecognised report is information too.
    """
    text = text.strip().rstrip("$")
    if not text.startswith("+"):
        return None

    tokens = text.split(",")
    header = HEADER_RE.match(tokens[0])
    if header is None:
        return None

    report = Report(
        kind=header.group("kind"), report=header.group("report"), raw=text
    )
    body = tokens[1:]

    if report.report == "GTFRI":
        for name, raw in zip(GTFRI_FIELDS, body):
            report.fields[name] = raw
        for name in [
            "gps_accuracy", "speed", "azimuth", "altitude", "longitude",
            "latitude", "report_type", "network_type", "power_supply",
            "main_power_voltage", "backup_battery_voltage",
            "backup_battery_percentage", "ecu_error_type", "alive",
            "ecu_lock_state", "scooter_battery_percentage",
        ]:
            if name in report.fields:
                report.fields[name] = _number(report.fields[name])
        report.fields["faults"] = decode_error_code(
            report.fields.get("ecu_error_code") or ""
        )
        report.fields.update(parse_ecu_info(report.fields.get("ecu_info") or ""))
    else:
        # Every report shares the first few fields; beyond that the layout is
        # per-report, so keep the tokens rather than guess at names.
        for name, raw in zip(
            ["protocol_version", "unique_id", "device_name"], body
        ):
            report.fields[name] = raw
        for index, raw in enumerate(body):
            report.fields[f"f{index + 1}"] = raw

    return report


# -- ground truth --------------------------------------------------------

# What a CAN field can be fitted against. Voltages arrive in mV.
GROUND_TRUTH_COLUMNS = [
    "timestamp", "report",
    "speed", "ecu_speed", "ecu_total_mileage", "ecu_current_mileage",
    "ecu_remaining_mileage", "ecu_ride_time",
    "main_power_v", "backup_battery_v", "backup_battery_percentage",
    "scooter_battery_percentage", "pack_current_ma", "battery_health_pct",
    "cell_temp_max_c", "cell_temp_min_c", "mos_temp_c",
    "ecu_head_light_status", "ecu_rear_light_status", "ecu_bell_button_status",
    "ecu_charging", "ecu_battery_lock_state", "ecu_lock_state", "alive",
    "longitude", "latitude", "altitude", "faults",
]


def ground_truth(serial):
    """Every @Track report in a session as one table of measured values.

    This is the table to join a candidate CAN signal against. `main_power_v` is
    the pack voltage in volts (the protocol reports millivolts); note that
    `altitude` is metres above sea level and has nothing to do with any battery,
    which is an easy field to misread.
    """
    import pandas as pd

    rows = []
    for _, line in serial.iterrows():
        report = parse(str(line.get("text", "")))
        if report is None:
            continue
        values: dict[str, Any] = {
            "timestamp": line.get("timestamp"),
            "report": report.report,
        }
        for column in GROUND_TRUTH_COLUMNS[2:]:
            values[column] = report.get(column)

        main_mv = report.get("main_power_voltage")
        backup_mv = report.get("backup_battery_voltage")
        values["main_power_v"] = None if main_mv is None else main_mv / 1000.0
        values["backup_battery_v"] = None if backup_mv is None else backup_mv / 1000.0
        rows.append(values)

    frame = pd.DataFrame(rows, columns=GROUND_TRUTH_COLUMNS)
    if frame.empty:
        return frame
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    frame["dt"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
    return frame


def describe(serial) -> str:
    """A readable summary of what the console said during a session."""
    reports = [parse(str(row.get("text", ""))) for _, row in serial.iterrows()]
    reports = [r for r in reports if r is not None]
    if not reports:
        return "no @Track reports in this session"

    from collections import Counter

    counts = Counter(f"+{r.kind}:{r.report}" for r in reports)
    lines = [f"{len(reports)} @Track reports:"]
    lines += [f"  {name:16} {count}" for name, count in counts.most_common()]

    identity = next((r for r in reports if r.get("device_name")), None)
    if identity is not None:
        lines.append(f"  device name      {identity.get('device_name')}")
        lines.append(f"  vin              {identity.get('vin')}")

    version = next((r for r in reports if r.report == "GTVER"), None)
    if version is not None:
        lines.append(f"  model/modem      {version.get('f7')} / {version.get('f10')}")

    faults = {fault for r in reports for fault in (r.get("faults") or [])}
    lines.append(f"  faults reported  {', '.join(sorted(faults)) if faults else 'none'}")
    return "\n".join(lines)

# Commands sent *to* the module, e.g. `AT+GTECC=*,,18,,,,,,,,A73A$`. These are
# not @Track reports -- they do not start with `+`, so parse() returns None for
# them -- but they carry the argument the matching +ACK does not echo back.
SENT_COMMAND_RE = re.compile(r"^AT\+(?P<report>GT[A-Z0-9]{3})=(?P<body>.*)$")


def sent_commands(serial) -> dict[str, tuple[str, str | None]]:
    """Map each sent command's serial number to (report, first argument).

    The module answers `AT+GTECC=*,,18,,,,,,,,A73A$` with
    `+ACK:GTECC,...,A73A,20260907170657,A73A$`. The `18` -- the part that says
    what was actually asked for -- appears only in the outgoing line, and the
    only thing tying the two together is the trailing serial number. So to
    label a GTECC mark usefully we have to pair them up.
    """
    found: dict[str, tuple[str, str | None]] = {}
    if serial is None or len(serial) == 0:
        return found
    for _, line in serial.iterrows():
        match = SENT_COMMAND_RE.match(str(line.get("text", "")).strip().rstrip("$"))
        if match is None:
            continue
        fields = match.group("body").split(",")
        # Last field is the serial number; field 0 is the password. Anything
        # non-empty in between is the argument, and in every command seen so
        # far there is exactly one.
        serial_number = fields[-1].strip() if len(fields) > 1 else ""
        argument = next((f.strip() for f in fields[1:-1] if f.strip()), None)
        if serial_number:
            found[serial_number] = (match.group("report"), argument)
    return found


def as_marks(serial):
    """Remote commands as a marks table, usable anywhere marks are.

    `+ACK:GTRTO` acknowledges each remote command by name -- HLON, HLOFF,
    RLONEN, RLOFF, UNLOCK, LOCK, MEULK -- with a timestamp on the same clock as
    the CAN log. That is exactly what a hand-typed mark is, except the operator
    did not have to type it and the timing is the device's own.

    `+ACK:GTECC` is the same kind of event but does not name itself: field 5 is
    the serial number, not a command, so the label comes from pairing the ack
    with the `AT+GTECC=` line that provoked it. A GTECC mark is labelled
    `GTECC:<argument>` -- `GTECC:18` -- because the argument is the whole point
    of the command and two GTECCs with different arguments are different
    stimuli. Where no sent line was recorded the label falls back to `GTECC`.

    What the argument *means* is not known. Do not read it as a speed.

    The output has the same `timestamp` and `label` columns that
    bikecan.experiment expects, so:

        marks = queclink.as_marks(session.serial)
        print(experiment.report(session.can, marks))

    identifies components from a session nobody annotated. This is how the
    headlight and rear light were confirmed.
    """
    import pandas as pd

    sent = sent_commands(serial)

    rows = []
    for _, line in serial.iterrows():
        report = parse(str(line.get("text", "")))
        if report is None or report.report not in ("GTRTO", "GTECC"):
            continue
        if report.report == "GTRTO":
            # Field 5 of GTRTO is the command being acknowledged.
            command = report.get("f5") or report.get("f4") or "GTRTO"
        else:
            # Field 5 of GTECC is the serial number, which is the only handle
            # on the argument. Field 7 repeats it if field 5 is missing.
            serial_number = str(report.get("f5") or report.get("f7") or "").strip()
            _, argument = sent.get(serial_number, (None, None))
            command = f"GTECC:{argument}" if argument else "GTECC"
        rows.append({"timestamp": line.get("timestamp"), "label": str(command)})

    frame = pd.DataFrame(rows, columns=["timestamp", "label"])
    if frame.empty:
        return frame
    return frame.sort_values("timestamp").reset_index(drop=True)
