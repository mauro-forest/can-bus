from bikecan import ids


def test_field_split():
    decoded = ids.decode(0x02181606)
    assert (decoded.priority, decoded.msg_type) == (0x02, 0x18)
    assert (decoded.node_a, decoded.node_b) == (0x16, 0x06)
    assert decoded.hex == "02181606"


def test_request_and_reply_share_the_node_pair():
    """The one exchange the recordings caught: same AA BB, different TT."""
    request = ids.decode(0x02294609)
    reply = ids.decode(0x05124609)
    assert request.node_pair == reply.node_pair == "4609"
    assert request.msg_type != reply.msg_type


def test_nibble_reading_is_available():
    """The competing reading of AA, kept until a session disproves one."""
    assert ids.decode(0x02181606).nibbles == (0x1, 0x6)


def test_error_flag():
    assert ids.is_error_frame(0x20000004)
    assert not ids.is_error_frame(0x03FF1000)


def test_eff_flag_is_stripped():
    assert ids.decode(0x80000000 | 0x03FF1000).raw == 0x03FF1000
