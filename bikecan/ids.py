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

  PP  a priority: values 0x02-0x1F, which is exactly the five bits available
      above the low 24, with low values on the most frequent traffic.
  TT  a message or command type. 0xFF appears only on periodic status messages
      and never on a command, so it probably means "unsolicited report".
  AA  a subsystem field. Confirmed: 0x36 is the headlight, 0x16 the rear light,
      0x46 the BMS, 0x64 the HubLock (see dbc/nodes.md for the evidence).
  BB  an index within that subsystem -- NOT simply a peer address. It is echoed
      exactly from request to reply, but for the BMS (AA=0x46) it runs 0x00-0x10
      selecting which block of cell data is reported, while for both confirmed
      lamps it is 0x06 with AA doing the distinguishing.

WHAT MAKES IT TESTABLE

`0x02294609` (zero-length) is answered 1.5 ms later by `0x05124609`. The pair
shares `AA BB` = `46 09` and differs in `TT`, which is what a request/response
protocol between one fixed pair of nodes looks like. If AA and BB really are
node addresses, every frame can be attributed to two physical components, and
the search space for every other question collapses.

A competing reading is that AA is itself two nibbles. It looked promising while
the low nibble of AA was 6 for all three confirmed components (0x36 headlight,
0x16 rear light, 0x46 BMS) -- but the HubLock then turned up at 0x64, low
nibble 4, so the pattern was a coincidence of the first three. `nibbles` still
exposes the reading; the evidence for it is now weak.

The open question that would settle it is 0x04FF3604: a 30 ms message at the
headlight address, far too fast for a lamp.
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
