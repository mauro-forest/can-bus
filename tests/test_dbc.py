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
