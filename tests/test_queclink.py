"""Queclink @Track parser tests.

Every fixture is a real line from
data/sessions/2026-09-07T11-00-37_first-capture/serial.log, checked against
reference/ZK Series @Track Air Interface Protocol_V80.51.pdf.
"""

import pandas as pd
import pytest

from bikecan import queclink

GTFRI = (
    "+RESP:GTFRI,EF8051,865969076362128,HumanForest,0K550924005695,37088,,,0,"
    "0000000000000000,1,0.0,0,12.4,-0.099579,51.499718,20260907110351,,0234,0015,"
    "301F,001C730A,31&99,2,41,0,53871,4070,91,0,0,1,,0.0&0.00&0.0&0.1&0&0&0&"
    "0000004A0000000401030000&000000C3000000EB05000000&0&0&00000000000000000000&"
    "0&2&00000000000000,100,20260907110352,A637$"
)
GTRTO_HLON = (
    "+ACK:GTRTO,EF8051,865969076362128,HumanForest,,HLON,A651,20260907124654,A651$"
)


def test_gtfri_field_positions():
    report = queclink.parse(GTFRI)
    assert report.kind == "RESP" and report.report == "GTFRI"
    assert report["unique_id"] == "865969076362128"
    assert report["device_name"] == "HumanForest"
    assert report["vin"] == "0K550924005695"
    assert report["speed"] == 0.0
    assert report["longitude"] == -0.099579
    assert report["latitude"] == 51.499718
    assert report["gps_utc_time"] == "20260907110351"


def test_altitude_is_not_a_voltage():
    """12.4 is metres above sea level. The pack voltage is field 26.

    These sit four fields apart and 12.4 looks exactly like a lead-acid
    reading, which is how it gets misread.
    """
    report = queclink.parse(GTFRI)
    assert report["altitude"] == 12.4
    assert report["main_power_voltage"] == 53871  # mV
    assert report["backup_battery_voltage"] == 4070


def test_ecu_info_compound_is_split():
    report = queclink.parse(GTFRI)
    assert report["ecu_speed"] == 0.0
    assert report["ecu_total_mileage"] == 0.1
    assert report["ecu_head_light_status"] == 0
    assert report["ecu_rear_light_status"] == 0
    assert report["ecu_battery_lock_door"] == 2


def test_no_faults_when_the_error_code_is_zero():
    assert queclink.parse(GTFRI)["faults"] == []


def test_error_code_bits_name_the_components():
    """The vendor's own component list, which cross-checks dbc/nodes.md."""
    assert queclink.decode_error_code("0000000000000001") == ["head lamp fault"]
    faults = queclink.decode_error_code("0000000000000600")
    assert faults == ["BMS heartbeat error", "meter heartbeat error"]
    assert queclink.decode_error_code("") == []


def test_pack_current_is_signed():
    """A real capture produced 2**32 - 87, i.e. a discharge of 87 mA.

    Read unsigned this is a 4.3 million amp charge, which is the kind of
    mistake that survives a long time because nothing crashes.
    """
    # 20 characters: six two-character groups, then the 8-character current.
    status = queclink.parse_battery_status("000000000000" + "FFFFFFA9")
    assert status["pack_current_ma"] == -87

    charging = queclink.parse_battery_status("0364201A1B1C" + "00000064")
    assert charging["pack_current_ma"] == 100
    assert charging["charging_mos_on"] and charging["discharging_mos_on"]


def test_battery_status_groups():
    # 0x01 sets bit 0 only: charging MOS on, discharging MOS off.
    status = queclink.parse_battery_status("01005A20181C" + "00000000")
    assert status["charging_mos_on"] is True
    assert status["discharging_mos_on"] is False
    assert status["battery_health_pct"] == 0x00
    assert status["cell_temp_max_c"] == 0x5A
    assert status["cell_temp_min_c"] == 0x20
    assert status["mos_temp_c"] == 0x18
    assert status["pack_current_ma"] == 0


def test_short_battery_status_gives_none_not_a_wrong_number():
    """The all-zero 20-char field a sleeping BMS sends, and a truncated one."""
    asleep = queclink.parse_battery_status("0" * 20)
    assert asleep["pack_current_ma"] == 0
    truncated = queclink.parse_battery_status("0364")
    assert truncated["pack_current_ma"] is None
    assert truncated["cell_temp_max_c"] is None


def test_battery_heating_plate_groups():
    plate = queclink.parse_battery_heating("1C1D1E1F200100")
    assert plate["heating_temp_1_c"] == 28
    assert plate["heating_plate_1_on"] is True
    assert plate["heating_plate_2_on"] is False


def test_empty_field_is_none_not_zero():
    """The protocol uses an empty field to mean "not reported"."""
    assert queclink._number("") is None
    assert queclink._number("0") == 0


def test_ack_report_keeps_the_command_name():
    """+ACK:GTRTO is what makes a remote command a timestamped stimulus."""
    report = queclink.parse(GTRTO_HLON)
    assert report.kind == "ACK" and report.report == "GTRTO"
    assert "HLON" in report.raw
    assert report["f5"] == "HLON"


def test_non_track_lines_are_ignored():
    assert queclink.parse("") is None
    assert queclink.parse("random serial noise") is None
    assert queclink.parse("+GARBAGE:XX,1,2") is None


def test_ground_truth_table():
    serial = pd.DataFrame(
        [
            {"timestamp": 1788779032.664, "text": GTFRI},
            {"timestamp": 1788785214.368, "text": GTRTO_HLON},
            {"timestamp": 1788785214.500, "text": "not a report"},
        ]
    )
    truth = queclink.ground_truth(serial)
    assert len(truth) == 2  # the noise line is dropped

    fri = truth[truth["report"] == "GTFRI"].iloc[0]
    assert fri["main_power_v"] == pytest.approx(53.871)
    assert fri["backup_battery_v"] == pytest.approx(4.07)
    assert fri["ecu_total_mileage"] == 0.1
    assert fri["altitude"] == 12.4


def test_describe_names_the_device():
    serial = pd.DataFrame([{"timestamp": 1.0, "text": GTFRI}])
    summary = queclink.describe(serial)
    assert "GTFRI" in summary and "HumanForest" in summary
    assert "faults reported  none" in summary


def test_describe_with_nothing_to_describe():
    assert "no @Track reports" in queclink.describe(pd.DataFrame())


def test_commands_become_marks():
    """Remote commands are marks nobody had to type."""
    serial = pd.DataFrame(
        [
            {"timestamp": 1788785234.208, "text": GTRTO_HLON.replace("HLON", "HLOFF")},
            {"timestamp": 1788785214.368, "text": GTRTO_HLON},
            {"timestamp": 1788779032.664, "text": GTFRI},  # not a command
        ]
    )
    marks = queclink.as_marks(serial)
    assert list(marks.columns) == ["timestamp", "label"]
    assert list(marks["label"]) == ["HLON", "HLOFF"]  # sorted by time
    assert marks.iloc[0]["timestamp"] == 1788785214.368


def test_marks_feed_the_experiment_machinery():
    """The point of as_marks: experiment.windows accepts it unchanged."""
    from bikecan import experiment

    serial = pd.DataFrame([{"timestamp": 1788785214.368, "text": GTRTO_HLON}])
    windows = experiment.windows(queclink.as_marks(serial), default_duration=20.0)
    assert len(windows) == 1
    assert windows[0].label == "HLON"
    assert windows[0].duration == 20.0


def test_as_marks_with_no_commands():
    assert queclink.as_marks(pd.DataFrame()).empty


def test_undocumented_error_bits_are_surfaced_not_dropped():
    """Every session so far carries 0210000000000000: bits 52 and 57.

    Both are outside the 0-35 range this protocol version documents. Returning
    an empty list would report a bike with two active faults as healthy.
    """
    faults = queclink.decode_error_code("0210000000000000")
    assert faults == ["undocumented bit 52", "undocumented bit 57"]


def test_documented_and_undocumented_bits_together():
    faults = queclink.decode_error_code("0010000000000801")
    assert "head lamp fault" in faults
    assert "abnormal communication with Hub-Lock" in faults
    assert "undocumented bit 52" in faults
