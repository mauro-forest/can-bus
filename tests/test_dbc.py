"""The bit-numbering conversion, and that the DBC decodes real frames."""

from bikecan import dbc


def test_msb_first_to_dbc_bit_numbering():
    """MSB-first index 0 is byte 0's top bit, which DBC numbers 7."""
    assert dbc.dbc_start_bit(0) == 7
    assert dbc.dbc_start_bit(7) == 0
    assert dbc.dbc_start_bit(8) == 15
    # Byte 2 bit 1: the start of the uptime field when read as 10 bits.
    assert dbc.dbc_start_bit(22) == 17
    # Byte 2 bit 7: the start of the 16-bit reading.
    assert dbc.dbc_start_bit(16) == 23


def test_generated_dbc_decodes_the_uptime_field(tmp_path):
    """0x0902 in bytes 2-3, scaled by 10, is 23,060 s.

    Worked by hand: the field is 16 bits big-endian starting at byte 2, so
    0x09 0x02 -> 2306, and the scale of 10 s per count gives 23,060.
    """
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)

    message = database.get_message_by_frame_id(0x04FF3400)
    decoded = message.decode(bytes.fromhex("0000090200000000"))
    assert decoded["Uptime"] == 23060


def test_little_endian_bms_cell_voltages_decode(tmp_path):
    """Bytes 14 10 are 0x1014 = 4116 mV, not 0x1410 = 5136.

    Cell voltages are the one place both bit-numbering conventions are in use
    in the same DBC, so this guards the little-endian half.
    """
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)

    decoded = database.get_message_by_frame_id(0x05FF4602).decode(
        bytes.fromhex("1410191019101910")
    )
    assert decoded["Cell1"] == 4116
    assert [decoded[f"Cell{n}"] for n in (2, 3, 4)] == [4121, 4121, 4121]


def test_thirteen_cells_sum_to_the_reported_pack_voltage(tmp_path):
    """The frames relayed verbatim in +RESP:GTBMI, against GTFRI's pack voltage.

    GTFRI reported 53.696 V at the same moment; 0.14 % apart.
    """
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)

    payloads = {
        0x05FF4602: "1410191019101910",
        0x05FF4603: "1A101E1020102010",
        0x05FF4604: "1F101F1021102210",
        0x05FF4605: "1E10000000000000",
    }
    cells = []
    for frame_id, payload in payloads.items():
        decoded = database.get_message_by_frame_id(frame_id).decode(
            bytes.fromhex(payload)
        )
        cells += [value for value in decoded.values() if value > 1000]

    assert len(cells) == 13
    assert sum(cells) == 53622
    assert abs(sum(cells) - 53696) / 53696 < 0.005


def test_light_states_decode(tmp_path):
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)

    head = database.get_message_by_frame_id(0x02203606)
    assert head.decode(bytes.fromhex("0201000000000000"))["HeadLightOn"] == 1
    assert head.decode(bytes.fromhex("0200000000000000"))["HeadLightOn"] == 0

    rear = database.get_message_by_frame_id(0x02181606)
    assert rear.decode(bytes.fromhex("0000000001030200"))["RearLightOn"] == 1
    assert rear.decode(bytes.fromhex("0000000000030200"))["RearLightOn"] == 0


def test_pack_voltage_and_signed_pack_current_decode(tmp_path):
    """05FF4610 against the bytes the serial console relays verbatim.

    The current is two's complement: FFFFFFA9 is -87 mA. Read unsigned it
    would be 4,294,967,209 -- a 4.3 million amp charge -- which is the mistake
    this test exists to catch.
    """
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)
    message = database.get_message_by_frame_id(0x05FF4610)

    decoded = message.decode(bytes.fromhex("0000D176FFFFFFA3"))
    assert decoded["PackVoltage"] == 53622
    assert decoded["PackCurrent"] == -93

    # The pack voltage here must equal the sum of the 13 cells, which the
    # little-endian test above decodes independently.
    assert decoded["PackVoltage"] == 53622

    assert message.decode(bytes.fromhex("0000D176FFFFFFA9"))["PackCurrent"] == -87


def test_state_of_charge_and_health_decode(tmp_path):
    """05FF4609/05124609 at the four SOC values the whole corpus contains.

    Scaled by 0.1: 0x0370 = 880 is 88.0 %, which is what GTFRI reported for
    <Scooter Battery Percentage> on 4 September.
    """
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)

    live = database.get_message_by_frame_id(0x05FF4609)
    assert live.decode(bytes.fromhex("030003DE03CA00"))["StateOfCharge"] == 99.0
    assert live.decode(bytes.fromhex("030003D403CA00"))["StateOfCharge"] == 98.0
    assert live.decode(bytes.fromhex("030003DE03CA00"))["StateOfHealth"] == 97.0

    polled = database.get_message_by_frame_id(0x05124609)
    decoded = polled.decode(bytes.fromhex("0100037003E800"))
    assert decoded["StateOfCharge"] == 88.0
    assert decoded["StateOfHealth"] == 100.0


def test_cell_extremes_agree_with_the_cell_voltages(tmp_path):
    """05124614's max and min must be the max and min of 05FF4602-4605.

    Both sides are decoded here from real frames captured at the same moment,
    so this checks the two independent readings against each other rather than
    against a number written down by hand. Note the extremes are big-endian
    while the cell voltages are little-endian.
    """
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)

    cells = []
    for frame_id, payload in {
        0x05FF4602: "1410191019101910",
        0x05FF4603: "1A101E1020102010",
        0x05FF4604: "1F101F1021102210",
        0x05FF4605: "1E10000000000000",
    }.items():
        decoded = database.get_message_by_frame_id(frame_id).decode(
            bytes.fromhex(payload)
        )
        cells += [value for value in decoded.values() if value > 1000]

    extremes = database.get_message_by_frame_id(0x05124614).decode(
        bytes.fromhex("10220B101400000E")
    )
    assert extremes["MaxCellVoltage"] == max(cells) == 4130
    assert extremes["MinCellVoltage"] == min(cells) == 4116
    assert extremes["MaxCellIndex"] == cells.index(max(cells)) == 11
    assert extremes["MinCellIndex"] == cells.index(min(cells)) == 0
    assert extremes["CellVoltageSpread"] == max(cells) - min(cells) == 14


def test_remaining_over_full_capacity_is_the_reported_soc(tmp_path):
    """05124611 must reproduce the state of charge 05FF4609 reports.

    This is the arithmetic that confirmed the capacity fields: 13771/13964 is
    98.6 %, and the bus reported 99 % at that moment.
    """
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)

    capacity = database.get_message_by_frame_id(0x05124611).decode(
        bytes.fromhex("0061368C000035CB")
    )
    assert capacity["StateOfHealth"] == 97
    assert capacity["FullChargeCapacity"] == 13964
    assert capacity["RemainingCapacity"] == 13771

    soc = database.get_message_by_frame_id(0x05FF4609).decode(
        bytes.fromhex("030003DE03CA00")
    )["StateOfCharge"]
    ratio = 100 * capacity["RemainingCapacity"] / capacity["FullChargeCapacity"]
    assert round(ratio) == round(soc) == 99


def test_version_replies_match_the_serial_ecu_info_groups(tmp_path):
    """The six version replies must reassemble <ECU Info>'s version strings.

    This is the evidence for BB=00 being hardware and BB=01 firmware, and for
    the AA channel scheme in nodes.md, so it is worth a test: if a start bit
    or a width drifts, the strings stop matching.
    """
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)

    def value(frame_id, payload, signal):
        message = database.get_message_by_frame_id(frame_id)
        return int(message.decode(bytes.fromhex(payload))[signal])

    firmware = (
        f"{value(0x03FF1501, '0000004A00000000', 'FirmwareVersion'):08X}"
        f"{value(0x04F93501, '0000000400000000', 'FirmwareVersion'):08X}"
        # The BMS reply is two bytes; the report zero-extends it to four.
        f"{value(0x05124501, '0103', 'FirmwareVersion'):04X}0000"
    )
    hardware = (
        f"{value(0x03FF1500, '000000C300000000', 'HardwareVersion'):08X}"
        f"{value(0x04F93500, '000000EB00000000', 'HardwareVersion'):08X}"
        f"{value(0x05124500, '0500', 'HardwareVersion'):04X}0000"
    )
    assert firmware == "0000004A0000000401030000"
    assert hardware == "000000C3000000EB05000000"


def test_the_three_ten_second_counters_scale_alike(tmp_path):
    """04FF3400, 02F82400 and 03FF1400 all count 10 s per tick.

    They differ in what they count and in endianness, not in scale. 02F82400
    is the little-endian one: C503 is 0x03C5 = 965 ticks = 9,650 s, and
    reading it big-endian would give 0xC503 = 50,435.

    There are THREE of these, not four. 05FF4400 was counted as a fourth until
    the battery-removal session showed it stepping by 1 across 2.7 s and again
    across 68.3 s; see test_bms_sequence_is_not_a_clock.
    """
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)

    def tick(frame_id, payload, signal="AwakeTime"):
        message = database.get_message_by_frame_id(frame_id)
        return message.decode(bytes.fromhex(payload))[signal]

    assert tick(0x02F82400, "C503000000000000") == 9650
    assert tick(0x03FF1400, "0000000A00000000") == 100
    assert tick(0x04FF3400, "0000090200000000", "Uptime") == 23060


def test_bms_sequence_is_not_a_clock(tmp_path):
    """05FF4400 carries a raw frame index, with no seconds scaling.

    It was recorded as a fourth 10-second counter, which meant 0x08 decoded as
    80 s. It is the eighth frame of the message, not eighty seconds, and the
    scale must stay 1 so that nothing downstream reads it as a duration.
    """
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)

    message = database.get_message_by_frame_id(0x05FF4400)
    assert message.decode(bytes.fromhex("00000008"))["Sequence"] == 8
    assert "AwakeTime" not in {signal.name for signal in message.signals}


def test_heartbeat_system_ready_bit(tmp_path):
    """03FF1000 byte 0 bit 0x08 is clear for the first seconds after power-on.

    The whole point of the signal is that the two payloads differ, so decode
    both: every frame recorded before the first pack removal reads 0x08, and
    every cold boot starts at 0x00 and switches over within about two seconds.
    """
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)

    message = database.get_message_by_frame_id(0x03FF1000)
    booting = message.decode(bytes.fromhex("0040112200000001"))
    ready = message.decode(bytes.fromhex("0840112200000001"))
    assert booting["SystemReady"] == 0
    assert ready["SystemReady"] == 1


def test_bus_awake_flag_decodes_on_both_light_messages(tmp_path):
    """Byte 0 of each light message is the wake state, not the light.

    02203606 uses 1 awake / 2 asleep and 02181606 uses 1 awake / 0 asleep, so
    the two are not interchangeable. Both frames here have their lamp on, and
    only the wake state differs -- which is the case that disproved the old
    "HeadLightSource" reading.
    """
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)

    head = database.get_message_by_frame_id(0x02203606)
    assert head.decode(bytes.fromhex("0101000100010000"))["BusAwake"] == 1
    assert head.decode(bytes.fromhex("0201000000000000"))["BusAwake"] == 2

    rear = database.get_message_by_frame_id(0x02181606)
    awake = rear.decode(bytes.fromhex("0100190001030200"))
    asleep = rear.decode(bytes.fromhex("0000000001030200"))
    assert awake["RearLightOn"] == asleep["RearLightOn"] == 1
    assert (awake["BusAwake"], asleep["BusAwake"]) == (1, 0)
    # Byte 2 is the speed limit, not a lamp level: 25 here is 25 km/h, and the
    # 0 when asleep is the ECU not publishing rather than a measured zero.
    assert (awake["MaxSpeed"], asleep["MaxSpeed"]) == (25, 0)


def test_max_speed_decodes_the_same_on_both_carriers(tmp_path):
    """03FF1600 byte 7 and 02181606 byte 2 carry the same value.

    Frames verbatim from the set-speed-while-unlocked session, one pair per
    commanded value. A search over every identifier and byte position for the
    commanded sequence returned exactly these two positions, so if they ever
    disagree the reading is wrong.
    """
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)
    limit = database.get_message_by_frame_id(0x03FF1600)
    rear = database.get_message_by_frame_id(0x02181606)

    pairs = [
        ("142B000104001313", "0100130001030200", 19),
        ("142B000104001319", "0100190001030200", 25),
        ("142B00010400130F", "01000F0001030200", 15),
        ("142B00010400130A", "01000A0001030200", 10),
        ("142B000104001305", "0100050001030200", 5),
    ]
    for limit_frame, rear_frame, expected in pairs:
        assert limit.decode(bytes.fromhex(limit_frame))["MaxSpeed"] == expected
        assert rear.decode(bytes.fromhex(rear_frame))["MaxSpeed"] == expected


def test_confidence_level_is_validated(tmp_path):
    import pytest

    spec = {
        "message": [
            {
                "id": 0x123,
                "name": "M",
                "signal": [{"name": "S", "start_bit": 0, "length": 8,
                            "confidence": "definitely"}],
            }
        ]
    }
    with pytest.raises(ValueError, match="confidence"):
        dbc.build_database(spec)


def test_committed_dbc_is_current():
    """dbc/ebike.dbc must match what signals.toml generates.

    A stale generated file is worse than none: someone decodes a capture with
    it and trusts the result.
    """
    generated = dbc.build_database(dbc.load_signals()).as_dbc_string()
    assert dbc.OUTPUT_DBC.read_bytes() == generated.encode("utf-8"), (
        "dbc/ebike.dbc is stale -- run: uv run python -m bikecan.dbc"
    )


def test_lock_state_decodes(tmp_path):
    """13B76400 is a single-byte message: 1 unlocked, 0 locked.

    Note the sense: this is an "unlocked" flag, the inverse of GTFRI's
    ECU Lock State, and is named for the raw value rather than inverted.
    """
    out = dbc.generate(out_path=tmp_path / "ebike.dbc")
    database = dbc.load_database(out)

    lock = database.get_message_by_frame_id(0x13B76400)
    assert lock.length == 1
    assert lock.decode(bytes.fromhex("01"))["Unlocked"] == 1
    assert lock.decode(bytes.fromhex("00"))["Unlocked"] == 0
