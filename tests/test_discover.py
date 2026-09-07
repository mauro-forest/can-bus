"""Tests for the analysis primitives, checked against the real corpus."""

import pandas as pd

from bikecan import discover, logreader


def _frames(lines):
    return logreader.to_dataframe(
        logreader.parse_line(line, "test", n) for n, line in enumerate(lines, 1)
    )


def test_constant_payload_has_no_changing_bytes():
    can = _frames(
        [
            "(1.000) can0 03FF1000#0840112200000001",
            "(1.105) can0 03FF1000#0840112200000001",
            "(1.210) can0 03FF1000#0840112200000001",
        ]
    )
    row = discover.inventory(can).iloc[0]
    assert row["changing_bytes"] == "-"
    assert row["distinct_payloads"] == 1
    assert row["max_byte_entropy"] == 0.0  # not -0.0
    assert row["periodic"]
    assert row["period_ms"] == 105.0


def test_changing_byte_is_located():
    can = _frames(
        [
            "(1.0) can0 04FF3400#0000090200000000",
            "(2.0) can0 04FF3400#0000090300000000",
            "(3.0) can0 04FF3400#0000090400000000",
        ]
    )
    row = discover.inventory(can).iloc[0]
    assert row["changing_bytes"] == "3"

    detail = discover.byte_detail(can, "04FF3400").set_index("byte")
    assert detail.loc[0, "constant"]
    assert not detail.loc[3, "constant"]
    assert detail.loc[3, "distinct"] == 3


def test_short_payload_bytes_are_not_read_as_constant_zero():
    """The 7-byte reply must not gain a constant eighth byte from padding."""
    can = _frames(
        [
            "(1.0) can0 05124609#0100037003E800",
            "(2.0) can0 05124609#0100037003E800",
        ]
    )
    detail = discover.byte_detail(can, "05124609")
    assert list(detail["byte"]) == [0, 1, 2, 3, 4, 5, 6]


def test_candidate_fields_finds_the_contiguous_run(legacy_session):
    """The uptime field's active bits form one run, not scattered flags."""
    runs = discover.candidate_fields(legacy_session.can, "04FF3400")
    assert len(runs) == 1
    assert int(runs.iloc[0]["start_bit"]) == 22
    assert int(runs.iloc[0]["length"]) == 10


def test_request_response_pairs_the_real_exchange(legacy_session):
    pairs = discover.request_response(legacy_session.can)
    assert len(pairs) == 2
    assert set(pairs["request_id"]) == {"02294609"}
    assert set(pairs["reply_id"]) == {"05124609"}
    assert pairs["same_node_pair"].all()
    assert (pairs["gap_ms"] < 2.0).all()


def test_request_response_ignores_a_reply_outside_the_window():
    can = _frames(
        [
            "(1.000) can0 02294609#",
            "(1.500) can0 05124609#0100037003E800",  # 500 ms later: too late
        ]
    )
    assert len(discover.request_response(can, window_ms=10)) == 0


def test_node_summary_groups_the_pair(legacy_session):
    nodes = discover.node_summary(legacy_session.can).set_index("node_pair")
    assert nodes.loc["4609", "ids"] == 2
    assert nodes.loc["1000", "frames"] == 7243


def test_inventory_of_empty_input_is_empty():
    assert discover.inventory(pd.DataFrame()).empty
