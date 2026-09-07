"""Parser tests, built on the awkward lines the real recordings contain."""

import pytest

from bikecan import logreader

# Every one of these is a real line from data/sessions/legacy, except where noted.
NORMAL = "(1788538180.786114) can0 03FF1000#0840112200000001"
ZERO_DLC = "(1788538877.589377) can0 02294609#"
SEVEN_BYTE = "(1788538877.590926) can0 05124609#0100037003E800"


def test_normal_frame():
    frame = logreader.parse_line(NORMAL)
    assert frame.timestamp == 1788538180.786114
    assert frame.channel == "can0"
    assert frame.can_id == 0x03FF1000
    assert frame.is_extended
    assert not frame.is_error
    assert frame.dlc == 8
    assert frame.data == bytes.fromhex("0840112200000001")


def test_zero_length_frame_is_not_a_parse_failure():
    """The request half of the 46 09 exchange carries no payload at all."""
    frame = logreader.parse_line(ZERO_DLC)
    assert frame.can_id == 0x02294609
    assert frame.dlc == 0
    assert frame.data == b""
    assert not frame.is_remote


def test_seven_byte_payload():
    """A DLC of 7 must not be padded out to 8."""
    frame = logreader.parse_line(SEVEN_BYTE)
    assert frame.dlc == 7
    assert frame.data == bytes.fromhex("0100037003E800")


def test_standard_id_is_distinguished_by_width():
    """candump prints 3 hex digits for an 11-bit id and 8 for a 29-bit one."""
    standard = logreader.parse_line("(1.0) can0 123#DEADBEEF")
    assert standard.can_id == 0x123
    assert not standard.is_extended
    assert logreader.parse_line(NORMAL).is_extended


def test_error_frame_detected():
    frame = logreader.parse_line("(1.0) can0 20000004#0000000000000000")
    assert frame.is_error


def test_remote_frame():
    frame = logreader.parse_line("(1.0) can0 03FF1000#R8")
    assert frame.is_remote
    assert frame.dlc == 8
    assert frame.data == b""


def test_fd_frame():
    frame = logreader.parse_line("(1.0) can0 03FF1000##10011223344556677")
    assert frame.is_fd
    assert frame.data == bytes.fromhex("0011223344556677")


def test_blank_line_is_skipped_not_an_error():
    assert logreader.parse_line("   \n") is None


def test_malformed_line_raises():
    with pytest.raises(logreader.ParseError):
        logreader.parse_line("this is not a candump line", source="x", line_no=7)


def test_odd_length_payload_raises():
    with pytest.raises(logreader.ParseError):
        logreader.parse_line("(1.0) can0 03FF1000#08401")


def test_truncated_final_line_does_not_lose_the_file(tmp_path):
    """A session cut short by a power cut ends mid-line. Keep the rest."""
    log = tmp_path / "can0.log"
    log.write_text(f"{NORMAL}\n{ZERO_DLC}\n(17885381")
    frames = list(logreader.read_frames(log))
    assert len(frames) == 2

    with pytest.raises(logreader.ParseError):
        list(logreader.read_frames(log, strict=True))


def test_legacy_corpus_parses_completely(legacy_dir):
    """Every line of every real recording must parse in strict mode."""
    logs = sorted(legacy_dir.glob("*.log"))
    frames = list(logreader.read_many(logs, strict=True))
    assert len(frames) == 8732  # includes the duplicated file; see test_session
    assert all(frame.is_extended for frame in frames)
    assert not any(frame.is_error for frame in frames)
