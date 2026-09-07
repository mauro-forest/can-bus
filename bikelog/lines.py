"""Chunked reading that yields whole lines.

The recorder reads its children in large chunks rather than a line at a time:
if it were slow enough to fill a pipe, `candump` would block and frames would
be lost. Splitting the chunks into lines is this module's only job.
"""

from __future__ import annotations

import os


class LineBuffer:
    """Accumulates bytes from one fd and hands back complete lines."""

    def __init__(self, fd: int, chunk_size: int = 1 << 16) -> None:
        self.fd = fd
        self.chunk_size = chunk_size
        self._pending = bytearray()
        self.at_eof = False

    def read(self) -> list[bytes]:
        """Read what is available and return the complete lines in it."""
        try:
            chunk = os.read(self.fd, self.chunk_size)
        except BlockingIOError:
            return []
        except OSError:
            self.at_eof = True
            return self.drain()

        if not chunk:
            self.at_eof = True
            return self.drain()

        self._pending.extend(chunk)
        if b"\n" not in self._pending:
            return []
        *lines, rest = bytes(self._pending).split(b"\n")
        self._pending = bytearray(rest)
        return lines

    def drain(self) -> list[bytes]:
        """Whatever is left, whether or not it ended in a newline."""
        if not self._pending:
            return []
        rest = bytes(self._pending)
        self._pending.clear()
        return [rest]
