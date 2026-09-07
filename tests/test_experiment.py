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


# -- compare_states ------------------------------------------------------

def _series(rows):
    """rows of (timestamp, id_hex, payload_hex) as a frames DataFrame."""
    return logreader.to_dataframe(
        logreader.parse_line(f"({ts:.3f}) can0 {cid}#{payload}")
        for ts, cid, payload in rows
    )


LOCK_INTERVALS = [
    (0.0, 10.0, "LOCKED"),
    (10.0, 20.0, "UNLOCKED"),
    (20.0, 30.0, "LOCKED"),
    (30.0, 40.0, "UNLOCKED"),
]


def test_compare_states_finds_a_disjoint_byte():
    """A byte that is 0 in one state and 1 in the other, as the lock is."""
    rows = []
    for ts in (1.0, 5.0, 21.0, 25.0):
        rows.append((ts, "13B76400", "00"))
    for ts in (11.0, 15.0, 31.0, 35.0):
        rows.append((ts, "13B76400", "01"))
    found = experiment.compare_states(_series(rows), LOCK_INTERVALS)

    assert len(found) == 1
    row = found.iloc[0]
    assert row["id_hex"] == "13B76400" and row["byte"] == 0
    assert row["values_LOCKED"] == [0]
    assert row["values_UNLOCKED"] == [1]
    assert not row["looks_like_counter"]


def test_compare_states_ignores_a_byte_that_spans_states():
    rows = [(ts, "03FF1000", "0840112200000001") for ts in (1.0, 11.0, 21.0, 31.0)]
    assert experiment.compare_states(_series(rows), LOCK_INTERVALS).empty


def test_compare_states_flags_a_monotonic_counter():
    """A counter is disjoint across any two intervals and is never the answer.

    04FF3400's uptime byte does exactly this on real data, so the ranking has
    to push it below a genuine state flag rather than hide it.
    """
    rows = [
        (float(ts), "04FF3400", f"0000{value:04X}00000000")
        for ts, value in zip(range(1, 40, 4), range(100, 200, 10))
    ]
    rows += [(ts, "13B76400", "00" if ts < 10 or 20 <= ts < 30 else "01")
             for ts in (1.0, 11.0, 21.0, 31.0)]

    found = experiment.compare_states(_series(rows), LOCK_INTERVALS)
    by_id = found.set_index("id_hex")
    assert bool(by_id.loc["04FF3400", "looks_like_counter"])
    assert not bool(by_id.loc["13B76400", "looks_like_counter"])
    # The genuine flag must rank above the counter.
    assert found.iloc[0]["id_hex"] == "13B76400"


def test_compare_states_requires_two_intervals_per_state():
    """With one interval each, any single change looks like a perfect predictor."""
    rows = [(1.0, "13B76400", "00"), (11.0, "13B76400", "01")]
    single = [(0.0, 10.0, "LOCKED"), (10.0, 20.0, "UNLOCKED")]
    assert experiment.compare_states(_series(rows), single).empty


def test_compare_states_with_no_intervals():
    rows = [(1.0, "13B76400", "00")]
    assert experiment.compare_states(_series(rows), []).empty
