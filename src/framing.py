class Framer:
    def __init__(self, max_line: int = 4096):
        self.buf = bytearray()
        self.max_line = max_line

    def feed(self, data: bytes):
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
        return len(self.buf) > self.max_line
