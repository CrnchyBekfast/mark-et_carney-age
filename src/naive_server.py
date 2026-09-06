#!/usr/bin/env python3
"""
Naive blocking-server CONTROL -- COL334 A2, artifact T5 (roadmap §8.4).

THIS FILE IS NOT PART OF THE GRADED SUBMISSION. It exists solely to give
Experiment 4 a deliberately-wrong architecture to contrast against: a
single-threaded server that calls blocking accept() and blocking recv() in
one straight-line loop, with no select/poll/kqueue multiplexing at all.

The point it proves: Client 1 sends 20 bytes with no trailing '\n' and then
goes silent. This server's recv() call for Client 1 blocks forever waiting
for more bytes (or EOF) that never come -- and because accept() for the
NEXT client only runs after handle_client() returns, Client 2 can never
even be accept()ed, let alone served, for as long as Client 1's connection
stays open. Contrast this file's `ps -o wchan` reading (`sbwait`, blocked
in a socket read) against src/server.py's (`kqread`, blocked in kevent())
-- that one-word difference is T5's entire proof.

Protocol handling here is intentionally minimal -- just enough to answer
Experiment 4's LOGIN lines with "OK\n". It is not framing.py/protocol.py/
engine.py; reusing those would be pointless because the whole point of this
file is the I/O model, not the parsing.
"""

from __future__ import annotations

import socket
import sys


def handle_client(conn: socket.socket, addr) -> None:
    """
    Fully blocking, one connection at a time. Never returns while the
    client stays open and silent -- that's the entire demonstration.
    """
    rbuf = bytearray()
    while True:
        data = conn.recv(4096)          # BLOCKING -- no timeout, no select
        if not data:                     # EOF: client closed
            break
        rbuf += data
        while True:
            nl = rbuf.find(b"\n")
            if nl < 0:
                break                    # partial line: loop back to the
                                          # blocking recv() above and WAIT
            line = bytes(rbuf[:nl])
            del rbuf[:nl + 1]
            tokens = line.split()
            if tokens[:1] == [b"LOGIN"]:
                conn.sendall(b"OK\n")
            else:
                conn.sendall(b"ERROR unknown_command\n")
    conn.close()


def main() -> None:
    host, port = sys.argv[1], int(sys.argv[2])
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((host, port))
    ls.listen(4096)
    print("Naive (blocking, single-threaded) server listening.", file=sys.stderr, flush=True)

    while True:
        conn, addr = ls.accept()         # BLOCKING -- next client waits
                                          # behind whatever this call to
                                          # handle_client() below is doing
        try:
            handle_client(conn, addr)
        except OSError:
            try:
                conn.close()
            except OSError:
                pass


if __name__ == "__main__":
    main()
