"""Capture-side tests. These must pass on a laptop with no CAN hardware."""

import json
import os
import time
from pathlib import Path

import pytest

from bikelog import can_iface, config, lines, marks, meta, serial_reader


# -- config --------------------------------------------------------------

def test_config_loads_the_committed_file():
    loaded = config.load()
    assert loaded.can.interface
    assert loaded.serial.baudrate > 0
    assert loaded.session_dir.is_absolute()  # ~ expanded


def test_missing_config_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        config.load(tmp_path / "nope.toml")


# -- session metadata ----------------------------------------------------

def test_slugify_makes_a_safe_directory_name():
    assert meta.slugify("Headlight on and off!") == "headlight-on-and-off"
    assert meta.slugify("  ") == "session"
    assert "/" not in meta.slugify("a/b")


def test_session_lifecycle(tmp_path):
    session = meta.create(tmp_path, "brake test")
    assert session.path.is_dir()
    assert session.session_id.endswith("_brake-test")

    session.write_meta({"can_interface": "can0", "clean_shutdown": False})
    assert json.loads(session.meta_path.read_text())["clean_shutdown"] is False

    session.finalise({"can_frames": 42})
    written = json.loads(session.meta_path.read_text())
    assert written["clean_shutdown"] is True
    assert written["can_frames"] == 42
    assert written["duration_s"] >= 0
    assert "utc" in written["clock"]  # the clock record is not optional


def test_finalise_preserves_earlier_fields(tmp_path):
    session = meta.create(tmp_path, "x")
    session.write_meta({"serial_port": "/dev/ttyUSB0"})
    session.finalise({})
    assert json.loads(session.meta_path.read_text())["serial_port"] == "/dev/ttyUSB0"


def test_find_all_and_latest(tmp_path):
    first = meta.create(tmp_path, "one")
    first.write_meta({})
    time.sleep(0.01)
    second = meta.create(tmp_path, "two")
    second.write_meta({})
    assert [s.label for s in meta.find_all(tmp_path)] == ["one", "two"]
    assert meta.latest(tmp_path).label == "two"


def test_find_all_on_a_missing_directory_is_empty(tmp_path):
    assert meta.find_all(tmp_path / "nothing") == []


# -- marks ---------------------------------------------------------------

def test_bare_mark_is_recorded_unlabelled(tmp_path):
    """Both hands may be on the bike; a bare Enter still has to work."""
    log = tmp_path / "marks.log"
    stamp, label = marks.append(log, "   ")
    assert label == marks.UNLABELLED
    assert log.read_text() == f"{stamp:.6f} {marks.UNLABELLED}\n"


def test_mark_round_trips_through_the_fifo(tmp_path):
    from bikecan import marksreader

    fifo = tmp_path / "marks.fifo"
    marks.make_fifo(fifo)
    reader = marks.open_fifo_read(fifo)
    try:
        marks.send(fifo, "from another pane")
        time.sleep(0.05)
        buffer = lines.LineBuffer(reader)
        assert buffer.read() == [b"from another pane"]
    finally:
        os.close(reader)

    log = tmp_path / "marks.log"
    marks.append(log, "from another pane", 1788539377.6)
    parsed = marksreader.load(log)
    assert parsed.iloc[0]["timestamp"] == 1788539377.6
    assert parsed.iloc[0]["label"] == "from another pane"


def test_mark_without_a_running_capture_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        marks.send(tmp_path / "marks.fifo", "x")


def test_mark_with_no_reader_says_the_capture_is_not_running(tmp_path):
    fifo = tmp_path / "marks.fifo"
    marks.make_fifo(fifo)
    with pytest.raises(RuntimeError, match="not running"):
        marks.send(fifo, "x")


# -- line buffering ------------------------------------------------------

def test_line_buffer_holds_a_partial_line_until_it_completes():
    read_fd, write_fd = os.pipe()
    buffer = lines.LineBuffer(read_fd)
    os.write(write_fd, b"(1.0) can0 03FF1000#08\n(2.0) can0 03FF")
    assert buffer.read() == [b"(1.0) can0 03FF1000#08"]
    os.write(write_fd, b"1000#08\n")
    assert buffer.read() == [b"(2.0) can0 03FF1000#08"]
    os.close(write_fd)
    assert buffer.read() == []
    assert buffer.at_eof
    os.close(read_fd)


def test_line_buffer_drains_an_unterminated_tail():
    """A tail with no newline comes out on the read that sees EOF.

    A short read is not itself EOF, so the unterminated tail is held back until
    a later read returns nothing. The recorder covers the same ground from the
    other side by calling drain() explicitly during shutdown.
    """
    read_fd, write_fd = os.pipe()
    buffer = lines.LineBuffer(read_fd)
    os.write(write_fd, b"no newline")
    os.close(write_fd)
    assert buffer.read() == []  # held: no newline yet, and EOF not yet seen
    assert buffer.read() == [b"no newline"]
    assert buffer.at_eof
    os.close(read_fd)


def test_line_buffer_drain_releases_the_tail_immediately():
    read_fd, write_fd = os.pipe()
    buffer = lines.LineBuffer(read_fd)
    os.write(write_fd, b"partial")
    assert buffer.read() == []
    assert buffer.drain() == [b"partial"]
    assert buffer.drain() == []
    os.close(write_fd)
    os.close(read_fd)


# -- serial stamping -----------------------------------------------------

def test_serial_lines_are_stamped_and_stripped():
    import io

    out = io.StringIO()
    serial_reader._emit(out, 1788538180.786114, b"AT+CSQ: 21,99\r")
    assert out.getvalue() == "1788538180.786114 AT+CSQ: 21,99\n"


def test_non_utf8_is_escaped_not_dropped():
    """A stray byte is telling us something; a lost line tells us nothing."""
    import io

    out = io.StringIO()
    serial_reader._emit(out, 1.0, b"bad\xffbyte")
    assert out.getvalue() == "1.000000 bad\\xffbyte\n"


# -- CAN interface -------------------------------------------------------

def test_missing_interface_degrades_gracefully():
    """No CAN hardware here, so this exercises the failure path."""
    state = can_iface.state("definitely-not-an-interface")
    assert not state.exists
    assert "not present" in state.describe()

    _, notes = can_iface.ensure_up("definitely-not-an-interface", 500000)
    assert any("does not exist" in note for note in notes)
