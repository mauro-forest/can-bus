"""Decoding the 29-bit CAN identifiers -- a hypothesis, held loosely.

Nothing about this bus is documented, so the structure below is inferred from
the recordings and is not yet proven. It is written down as code so notebooks
can test it against every session rather than argue about it.

THE HYPOTHESIS

Every observed identifier is 29-bit (extended) and reads naturally as four
bytes, `PP TT AA BB`:

    03 FF 10 00       0x03FF1000, the 10 Hz heartbeat
    02 18 16 06       0x02181606
    02 18 16 08       0x02181608   same TT and AA, different BB
    05 12 46 09       0x05124609   the reply to 0x02294609

  PP  a priority: only 0x02-0x08 ever seen, low values on frequent traffic,
      consistent with a CAN arbitration priority in the top bits.
  TT  a message or command type: 0xFF on the broadcast-looking heartbeats,
      small values (0x12, 0x18, 0x20, 0x29, 0x40, 0x47) on the rest.
  AA  a node field. Values seen: 0x10 0x16 0x26 0x34 0x36 0x46 0x76.
  BB  a second node field. Values seen: 0x00 0x02 0x03 0x06 0x08 0x09.

WHAT MAKES IT TESTABLE

`0x02294609` (zero-length) is answered 1.5 ms later by `0x05124609`. The pair
shares `AA BB` = `46 09` and differs in `TT`, which is what a request/response
protocol between one fixed pair of nodes looks like. If AA and BB really are
node addresses, every frame can be attributed to two physical components, and
the search space for every other question collapses.

A competing reading is that AA is itself two nibbles -- note 0x16/0x06 and
0x36/0x03 and 0x46/0x09 pair up suspiciously -- so `nibbles` exposes that too.
Both survive until a session disproves one.
"""

from __future__ import annotations

from dataclasses import dataclass

CAN_ERR_FLAG = 0x20000000
CAN_EFF_FLAG = 0x80000000
CAN_EFF_MASK = 0x1FFFFFFF


@dataclass(frozen=True)
class DecodedId:
    raw: int
    priority: int  # PP
    msg_type: int  # TT
    node_a: int  # AA
    node_b: int  # BB

    @property
    def hex(self) -> str:
        return f"{self.raw:08X}"

    @property
    def node_pair(self) -> str:
        """The AA BB suffix, which is what request/response pairs share."""
        return f"{self.node_a:02X}{self.node_b:02X}"

    @property
    def nibbles(self) -> tuple[int, int]:
        """AA read as two nibbles, the competing reading of the node field."""
        return (self.node_a >> 4, self.node_a & 0x0F)

    def describe(self) -> str:
        return (
            f"{self.hex}  prio {self.priority}  type {self.msg_type:02X}  "
            f"nodes {self.node_a:02X}->{self.node_b:02X}"
        )


def decode(raw: int) -> DecodedId:
    """Split an identifier into the four hypothesised fields."""
    value = raw & CAN_EFF_MASK
    return DecodedId(
        raw=value,
        priority=(value >> 24) & 0xFF,
        msg_type=(value >> 16) & 0xFF,
        node_a=(value >> 8) & 0xFF,
        node_b=value & 0xFF,
    )


def is_error_frame(raw: int) -> bool:
    """Error frames carry SocketCAN's error flag, never a real identifier.

    Their presence in quantity almost always means the interface bitrate does
    not match the bus.
    """
    return bool(raw & CAN_ERR_FLAG)
