#!/usr/bin/env python3
"""
Market-Data Client   (COL334 A2)

    run-market-data <host> <port> <instrument> [instrument ...]

Read-only with respect to trading (§2.4): only SUBSCRIBE, UNSUBSCRIBE, QUIT
are permitted. Subscribes on connect, then prints TRADE updates as pushed.

Uses framing.Framer for the inbound stream so that multiple TRADE lines
coalesced into one segment, or one TRADE split across two recv()s, are both
handled correctly rather than corrupting the display.
"""

from __future__ import annotations

import socket
import sys

import framing


def main() -> None:
    if len(sys.argv) < 3:
        sys.stderr.write("usage: market_data.py <host> <port> [instrument ...]\n")
        raise SystemExit(2)

    host, port = sys.argv[1], int(sys.argv[2])
    instruments = sys.argv[3:] or ["JNST"]

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, port))
    for inst in instruments:
        s.sendall(("SUBSCRIBE %s\n" % inst).encode())
        print("[sent] SUBSCRIBE %s" % inst, file=sys.stderr, flush=True)

    fr = framing.Framer()
    try:
        while True:
            data = s.recv(65536)
            if not data:
                print("[server closed the connection]", file=sys.stderr)
                return
            for line in fr.feed(data):
                print(line.decode("utf-8", "replace"), flush=True)
            if fr.overflowed():
                print("[server sent an oversized line -- disconnecting]",
                      file=sys.stderr)
                return
    except KeyboardInterrupt:
        pass
    finally:
        try:
            s.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
