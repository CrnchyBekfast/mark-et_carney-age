"""
Line framing over a byte stream -- COL334 A2 (roadmap §3, PHASE_1_2_SPEC.md).

Invariant: no `socket` import. Bytes in, bytes out -- no decoding to `str`
anywhere. Zero semantic validation: this module does not know what a valid
command is and does not reject empty lines. That is entirely protocol.py's
job.
"""


class Framer:
    def __init__(self, max_line: int = 4096):
        self.buf = bytearray()
        self.max_line = max_line

    def feed(self, data: bytes):
        """
        Yield each complete line (bytes, without the trailing '\\n', and
        without a trailing '\\r' if one was present) found after appending
        `data` to the internal buffer.

        Buffer compaction happens eagerly, in one shot, before the first
        yield -- not per-line inside the scan loop. This matters because a
        naive scan-yield-delete-per-iteration generator would leave the
        buffer only partially compacted if the caller's consuming `for`
        loop `break`s out early (server.py does exactly this: `if c.dead:
        break` after a command triggers kill() mid-batch). Doing the whole
        scan and the single compaction synchronously, then `yield from
        lines`, means self.buf is always fully appended-to and compacted as
        soon as feed() is called, regardless of how many yielded items the
        caller actually consumes.
        """
        self.buf += data
        lines = []
        start = 0
        while True:
            nl = self.buf.find(b'\n', start)
            if nl < 0:
                break
            line = bytes(self.buf[start:nl])
            if line.endswith(b'\r'):
                line = line[:-1]
            lines.append(line)
            start = nl + 1
        if start:
            del self.buf[:start]        # ONE compaction per feed() call
        yield from lines

    def overflowed(self) -> bool:
        """
        True if the current unterminated residual exceeds max_line. Pure
        function of current state -- safe to call any time. Caller must
        check this immediately after consuming each feed() call and, if
        True, reply ERROR line_too_long and kill() the connection before
        further data can arrive.
        """
        return len(self.buf) > self.max_line
