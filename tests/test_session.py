from bikecan import session


def test_legacy_directory_loads_without_metadata(legacy_session):
    assert len(legacy_session.can) == 7588
    assert legacy_session.can["id_hex"].nunique() == 13
    assert not legacy_session.has_meta
    assert legacy_session.serial.empty
    assert legacy_session.marks.empty


def test_duplicate_log_is_skipped_not_counted_twice(legacy_session):
    """One recording is present twice under different names.

    Reading both double-counts every frame in it, which made the uptime counter
    appear to stand still. Both files stay on disk; only one is read.
    """
    skipped = [w for w in legacy_session.warnings if "byte-identical" in w]
    assert len(skipped) == 1
    assert "172937" in skipped[0]


def test_session_with_metadata_reports_clean_shutdown(tmp_path):
    directory = tmp_path / "2026-09-07T10-00-00_test"
    directory.mkdir()
    (directory / "meta.json").write_text(
        '{"session_id": "s1", "label": "test", "clean_shutdown": true}'
    )
    (directory / "can0.log").write_text("(1.0) can0 03FF1000#0840112200000001\n")
    (directory / "serial.log").write_text("1.5 modem ready\n")
    (directory / "marks.log").write_text("1.2 headlight on\n")

    loaded = session.load(directory)
    assert loaded.clean and loaded.has_meta
    assert len(loaded.can) == 1
    assert loaded.serial.iloc[0]["text"] == "modem ready"
    assert loaded.marks.iloc[0]["label"] == "headlight on"


def test_unclean_session_is_flagged(tmp_path):
    directory = tmp_path / "s"
    directory.mkdir()
    (directory / "meta.json").write_text('{"clean_shutdown": false}')
    (directory / "can0.log").write_text("")
    assert "UNCLEAN" in session.load(directory).summary()


def test_error_frames_produce_a_bitrate_warning(tmp_path):
    directory = tmp_path / "s"
    directory.mkdir()
    (directory / "meta.json").write_text('{"clean_shutdown": true}')
    (directory / "can0.log").write_text("(1.0) can0 20000004#0000000000000000\n")
    assert any("bitrate" in w for w in session.load(directory).warnings)
