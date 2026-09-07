import pandas as pd

from bikecan import experiment, logreader


def _marks(rows):
    return pd.DataFrame(rows, columns=["timestamp", "label"])


def test_repeated_label_brackets_a_window():
    windows = experiment.windows(
        _marks([(100.0, "headlight on"), (110.0, "headlight on")])
    )
    assert len(windows) == 1
    assert (windows[0].start, windows[0].end) == (100.0, 110.0)


def test_lone_mark_gets_a_fixed_window():
    windows = experiment.windows(_marks([(100.0, "brake")]), default_duration=5.0)
    assert (windows[0].start, windows[0].end) == (100.0, 105.0)


def test_windows_are_ordered_and_independent():
    windows = experiment.windows(
        _marks([(200.0, "b"), (100.0, "a"), (105.0, "a"), (210.0, "b")])
    )
    assert [w.label for w in windows] == ["a", "b"]


def test_diff_window_finds_a_new_id():
    can = logreader.to_dataframe(
        logreader.parse_line(line)
        for line in [
            "(100.0) can0 03FF1000#0840112200000001",
            "(101.0) can0 03FF1000#0840112200000001",
            # The stimulus window opens at 110.
            "(111.0) can0 02203606#0100000000000000",
            "(111.5) can0 03FF1000#0840112200000001",
        ]
    )
    window = experiment.Window("headlight on", 110.0, 112.0)
    diff = diff = experiment.diff_window(can, window).set_index("id_hex")
    assert bool(diff.loc["02203606", "new_id"])
    assert not bool(diff.loc["03FF1000", "interesting"])


def test_baseline_guard_excludes_the_moment_before_the_mark():
    """A person types Enter after the thing happens, so the guard matters."""
    can = logreader.to_dataframe(
        logreader.parse_line(line)
        for line in [
            "(100.0) can0 03FF1000#0840112200000001",
            "(109.5) can0 02203606#0100000000000000",  # inside the guard
        ]
    )
    baseline = experiment.baseline(can, before=110.0, guard=1.0)
    assert list(baseline["id_hex"]) == ["03FF1000"]


def test_report_with_no_marks_says_so():
    assert "no marks" in experiment.report(pd.DataFrame(), pd.DataFrame())
