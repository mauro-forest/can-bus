"""End-to-end recorder test with a stubbed candump.

This is the riskiest code in the project -- two child processes, a FIFO, stdin
and a shutdown path -- and it is the part that cannot be checked by reading a
recording afterwards. The stub stands in for hardware this laptop does not have.
"""

import json
import sys
import threading
import time

import pytest

from bikelog import config, marks, meta, recorder

STUB_FRAMES = 30


@pytest.fixture
def stub_candump(monkeypatch):
    """Replace candump with a Python process emitting real log-format lines."""
    script = (
        "import sys, time\n"
        f"for i in range({STUB_FRAMES}):\n"
        "    now = time.time()\n"
        "    sys.stdout.write(f'({now:.6f}) can0 03FF1000#0840112200000001\\n')\n"
        "    sys.stdout.write(f'({now:.6f}) can0 04FF3400#0000090{i % 10}00000000\\n')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.01)\n"
        "time.sleep(30)\n"  # stay alive so shutdown exercises the SIGTERM path
    )
    monkeypatch.setattr(
        recorder, "_candump_argv", lambda interface: [sys.executable, "-c", script]
    )


def _config(tmp_path) -> config.Config:
    return config.Config(
        can=config.CanConfig(interface="can0", bitrate=500000),
        serial=config.SerialConfig(port=str(tmp_path / "no-such-port"),
                                   baudrate=115200, timeout=0.1),
        session_dir=tmp_path / "sessions",
        source=tmp_path / "capture.toml",
    )


def test_records_frames_marks_and_finalises(tmp_path, stub_candump):
    cfg = _config(tmp_path)
    cfg.session_dir.mkdir(parents=True)
    session = meta.create(cfg.session_dir, "recorder test")
    session.write_meta({"clean_shutdown": False})

    instance = recorder.Recorder(cfg, session)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()

    # Wait for the stub's frames to arrive.
    deadline = time.time() + 10
    while instance.counts.can_frames < STUB_FRAMES * 2 and time.time() < deadline:
        time.sleep(0.05)

    # A mark from "another pane", through the FIFO the capture is watching.
    deadline = time.time() + 5
    while not session.marks_fifo.exists() and time.time() < deadline:
        time.sleep(0.05)
    marks.send(session.marks_fifo, "headlight on")

    deadline = time.time() + 5
    while instance.counts.marks == 0 and time.time() < deadline:
        time.sleep(0.05)

    instance._stop = True
    thread.join(timeout=20)
    assert not thread.is_alive(), "the recorder did not shut down"

    session.finalise(instance.counts.as_meta())

    assert instance.counts.can_frames == STUB_FRAMES * 2
    assert instance.counts.can_ids == {"03FF1000", "04FF3400"}
    assert instance.counts.marks == 1

    written = session.can_log.read_text().splitlines()
    assert len(written) == STUB_FRAMES * 2
    assert written[0].endswith("03FF1000#0840112200000001")

    assert session.marks_log.read_text().endswith("headlight on\n")
    # The FIFO is a runtime artefact; it must not be left in the recording.
    assert not session.marks_fifo.exists()

    final = json.loads(session.meta_path.read_text())
    assert final["clean_shutdown"] is True
    assert final["can_frames"] == STUB_FRAMES * 2
    assert final["can_id_count"] == 2


def test_error_frames_are_counted_separately(tmp_path, monkeypatch):
    """A bitrate mismatch shows up as error frames, and must be visible."""
    script = (
        "import sys\n"
        "sys.stdout.write('(1.0) can0 20000004#0000000000000000\\n')\n"
        "sys.stdout.write('(1.1) can0 03FF1000#0840112200000001\\n')\n"
        "sys.stdout.flush()\n"
    )
    monkeypatch.setattr(
        recorder, "_candump_argv", lambda interface: [sys.executable, "-c", script]
    )

    cfg = _config(tmp_path)
    cfg.session_dir.mkdir(parents=True)
    session = meta.create(cfg.session_dir, "errors")
    instance = recorder.Recorder(cfg, session)

    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while instance.counts.can_frames < 2 and time.time() < deadline:
        time.sleep(0.05)
    instance._stop = True
    thread.join(timeout=20)

    assert instance.counts.can_frames == 2
    assert instance.counts.can_error_frames == 1


def test_missing_candump_is_reported_not_fatal(tmp_path, monkeypatch):
    """No can-utils installed must not take the serial capture down with it."""
    monkeypatch.setattr(
        recorder, "_candump_argv", lambda interface: ["definitely-not-a-binary"]
    )
    cfg = _config(tmp_path)
    cfg.session_dir.mkdir(parents=True)
    session = meta.create(cfg.session_dir, "no candump")
    instance = recorder.Recorder(cfg, session)

    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    time.sleep(0.5)
    instance._stop = True
    thread.join(timeout=20)

    assert any("not found" in note for note in instance.notices)
    assert instance.counts.can_frames == 0
