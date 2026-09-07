"""The Lua dissector: that it stays in step with signals.toml, and that
Wireshark decodes real payloads with it.

The end-to-end tests run tshark over a candump log, which is the same thing
the dissector does in anger -- Wireshark reads candump logs natively, so a
session's can0.log needs no conversion. They skip where Wireshark is not
installed, so the Pi and CI are unaffected.
"""

import os
import shutil
import subprocess

import pytest

from bikecan import wireshark

# macOS installs Wireshark as an app bundle and leaves the CLI off PATH.
TSHARK = shutil.which("tshark") or shutil.which(
    "tshark", path="/Applications/Wireshark.app/Contents/MacOS"
)

needs_tshark = pytest.mark.skipif(TSHARK is None, reason="Wireshark is not installed")


def decode(tmp_path, lines, display_filter):
    """Write a candump log, dissect it, return the packet detail as text."""
    lua = wireshark.generate(out_path=tmp_path / "ebike.lua")
    log = tmp_path / "can0.log"
    log.write_text("".join(f"{line}\n" for line in lines))

    result = subprocess.run(
        [
            TSHARK,
            "-X", f"lua_script:{lua}",
            "-r", str(log),
            "-Y", display_filter,
            "-V",
        ],
        capture_output=True,
        text=True,
        check=True,
        # Wireshark loads personal Lua plugins from a path under $HOME. If
        # dbc/ebike.lua is installed there -- which is how you use it in the
        # GUI -- loading it again with -X aborts with "there cannot be two
        # protocols with the same description", and the test would then be
        # exercising the installed copy rather than the one just generated.
        env={**os.environ, "HOME": str(tmp_path)},
    )
    # A Lua failure is reported on stderr and the frames simply go undissected,
    # so an assertion on stdout alone can pass against a broken dissector.
    assert "Lua" not in result.stderr, result.stderr
    return result.stdout


def test_byte_span_matches_both_bit_conventions():
    """The two conventions reach the same bytes by different routes.

    big_endian counts bits MSB-first from byte 0, so bit 16 is byte 2.
    little_endian names the position of the LSB, so 16 is also byte 2 -- but
    the length is then counted in whole bytes upwards from there.
    """
    big = {"start_bit": 16, "length": 16, "byte_order": "big_endian"}
    assert wireshark.byte_span(big) == (2, 2)

    little = {"start_bit": 16, "length": 16, "byte_order": "little_endian"}
    assert wireshark.byte_span(little) == (2, 2)

    # A sub-byte big_endian field still reads the one byte that holds it.
    assert wireshark.byte_span(
        {"start_bit": 8, "length": 2, "byte_order": "big_endian"}
    ) == (1, 1)

    # A big_endian field spanning a byte boundary reads both bytes.
    assert wireshark.byte_span(
        {"start_bit": 6, "length": 4, "byte_order": "big_endian"}
    ) == (0, 2)


def test_unaligned_little_endian_is_refused():
    """Rather than emit Lua that decodes plausible-looking nonsense."""
    message = {"name": "Test"}
    signal = {
        "name": "Unaligned",
        "start_bit": 4,
        "length": 16,
        "byte_order": "little_endian",
        "confidence": "hypothesis",
    }
    with pytest.raises(ValueError, match="byte-aligned"):
        wireshark.validate(message, signal)


def test_confidence_level_is_validated():
    with pytest.raises(ValueError, match="confidence"):
        wireshark.validate(
            {"name": "Test"},
            {"name": "Bad", "start_bit": 0, "length": 8, "confidence": "certain"},
        )


def test_only_unconfirmed_signals_are_marked():
    """A confirmed value carries its unit and nothing else; anything weaker
    says so every time it is displayed."""
    assert wireshark.suffix({"confidence": "confirmed", "unit": "mV"}) == " mV"
    assert wireshark.suffix({"confidence": "confirmed"}) == ""
    assert wireshark.suffix({"confidence": "probable", "unit": "degC"}) == " degC [probable]"
    assert wireshark.suffix({"confidence": "hypothesis"}) == " [hypothesis]"


def test_committed_lua_is_current(tmp_path):
    """Regenerate and compare, the same guard dbc/ebike.dbc has.

    Editing signals.toml without regenerating leaves a dissector that quietly
    reports the old reading of the bus.
    """
    regenerated = wireshark.generate(out_path=tmp_path / "ebike.lua")
    assert regenerated.read_text() == wireshark.OUTPUT_LUA.read_text(), (
        "dbc/ebike.lua is stale -- run: uv run python -m bikecan.wireshark"
    )


@needs_tshark
def test_lights_and_lock_decode(tmp_path):
    detail = decode(
        tmp_path,
        [
            "(1788785215.138000) can0 02203606#0201000000000000",
            "(1788785215.140000) can0 13B76400#0100000000000000",
        ],
        "ebike",
    )
    assert "HeadLightOn: 1" in detail
    assert "Unlocked: 1" in detail


@needs_tshark
def test_little_endian_cell_voltage_decodes(tmp_path):
    """Bytes 14 10 are 0x1014 = 4116 mV, not 0x1410."""
    detail = decode(
        tmp_path,
        ["(1788785215.138000) can0 05FF4602#1410191019101910"],
        "ebike.bmscellvoltages_1_4.cell1",
    )
    assert "Cell1: 4116 mV" in detail


@needs_tshark
def test_scaled_uptime_decodes(tmp_path):
    """0x0902 in bytes 2-3, scaled by 10, is 23,060 s."""
    detail = decode(
        tmp_path,
        ["(1788785215.138000) can0 04FF3400#0000090200000000"],
        "ebike.uptime_3400.uptime",
    )
    assert "Uptime: 23060 s" in detail


@needs_tshark
def test_signed_pack_current_decodes(tmp_path):
    """0xFFFFFFA9 is -87 mA. Unsigned it would read 4294967209, which is the
    mistake a real capture has already provoked once."""
    detail = decode(
        tmp_path,
        ["(1788785215.138000) can0 05FF4610#0000D176FFFFFFA9"],
        "ebike.bmspackvoltagecurrent.packcurrent",
    )
    assert "PackVoltage: 53622 mV" in detail
    assert "PackCurrent: -87 mA" in detail


@needs_tshark
def test_confidence_reaches_the_packet_detail(tmp_path):
    """The reason this generator exists rather than dbc2shark: a guess must
    not read like a measurement in the one place people actually look."""
    detail = decode(
        tmp_path,
        ["(1788785215.138000) can0 05FF4607#1D1B1C"],
        "ebike.bmstemperatures_b.temp5",
    )
    assert "Temp5: 27 degC [probable]" in detail


@needs_tshark
def test_unknown_identifiers_are_left_alone(tmp_path):
    """The frames that are still unknown are the point of the project, so the
    dissector must decline an identifier that signals.toml does not describe
    rather than claim it. 04FF9999 is not on the bus and not in the TOML."""
    detail = decode(
        tmp_path,
        ["(1788785215.138000) can0 04FF9999#0102030405060708"],
        "can",
    )
    assert "Forest ebike" not in detail


@needs_tshark
def test_every_message_is_filterable_by_name(tmp_path):
    """Heartbeat_1000 has no signals -- its payload never varies -- so without
    a name field it would be reachable only by raw identifier."""
    detail = decode(
        tmp_path,
        ["(1788785215.138000) can0 03FF1000#0840112200000001"],
        'ebike.message == "Heartbeat_1000"',
    )
    # Generated, so Wireshark brackets it: the name is ours, not the bus's.
    assert "[Message: Heartbeat_1000]" in detail
