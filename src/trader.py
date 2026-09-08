#!/usr/bin/env python3
"""
Trader Client   (COL334 A2)

    run-trader <host> <port> <username>

Sends LOGIN on connect, then reads commands from stdin (BUY / SELL / CANCEL /
QUIT) while simultaneously displaying whatever the server pushes.

BOUGHT/SOLD are asynchronous (§2.3, §2.7): a trader may submit an order, get
ORDER_ACCEPTED, and receive BOUGHT much later when a matching order arrives.
A blocking request/response client cannot display that while the user is
mid-typing, so stdin and the socket are both registered with
select.kqueue() -- the same mechanism the server uses.

Uses framing.Framer for the inbound socket stream: the server coalesces one
event-loop iteration's output into a single send(), so ORDER_ACCEPTED and a
BOUGHT can arrive in ONE segment, and a segment can also split an
application message across two recv()s. Framer handles both directions.
"""

from __future__ import annotations

import select
import socket
import sys

import framing


def main() -> None:
    if len(sys.argv) < 4:
        sys.stderr.write("usage: trader.py <host> <port> <username>\n")
        raise SystemExit(2)

    host, port, user = sys.argv[1], int(sys.argv[2]), sys.argv[3]

    if not hasattr(select, "kqueue"):
        sys.stderr.write("FATAL: select.kqueue() unavailable on %s\n" % sys.platform)
        raise SystemExit(2)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, port))
    s.sendall(("LOGIN %s\n" % user).encode())
    print("[sent] LOGIN %s" % user, file=sys.stderr, flush=True)
    s.setblocking(False)

    sfd, ifd = s.fileno(), sys.stdin.fileno()
    kq = select.kqueue()
    kq.control([
        select.kevent(sfd, select.KQ_FILTER_READ,
                      select.KQ_EV_ADD | select.KQ_EV_ENABLE),
        select.kevent(ifd, select.KQ_FILTER_READ,
                      select.KQ_EV_ADD | select.KQ_EV_ENABLE),
    ], 0)

    fr = framing.Framer()
    quitting = False

    try:
        while True:
            try:
                events = kq.control(None, 8, None)
            except InterruptedError:
                continue

            for ev in events:
                if ev.ident == sfd:
                    try:
                        data = s.recv(65536)
                    except BlockingIOError:
                        continue
                    except (ConnectionResetError, OSError) as e:
                        print("[connection error: %r]" % e, file=sys.stderr)
                        return
                    if not data:
                        print("[server closed the connection]", file=sys.stderr)
                        return
                    for line in fr.feed(data):
                        print("<< " + line.decode("utf-8", "replace"), flush=True)
                    if fr.overflowed():
                        print("[server sent an oversized line -- disconnecting]",
                              file=sys.stderr)
                        return
                elif not quitting:
                    line = sys.stdin.readline()
                    if not line:                      # EOF on stdin
                        return
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        s.sendall((line + "\n").encode())
                    except (BrokenPipeError, OSError) as e:
                        print("[send failed: %r]" % e, file=sys.stderr)
                        return
                    if line.upper() == "QUIT":
                        # §2.2.5 graceful disconnection, and the one place the
                        # shutdown() required by §4.1 genuinely belongs: close
                        # our write direction so the server sees a clean EOF,
                        # then keep reading until it closes from its end. Any
                        # BOUGHT/SOLD the server had already queued still
                        # arrives -- a bare close() here could discard it.
                        try:
                            s.shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                        try:
                            kq.control([select.kevent(ifd,
                                                      select.KQ_FILTER_READ,
                                                      select.KQ_EV_DELETE)], 0)
                        except OSError:
                            pass
                        quitting = True
                        print("[sent QUIT; half-closed, awaiting server close]",
                              file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            s.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
