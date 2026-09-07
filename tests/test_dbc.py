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
