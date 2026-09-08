#!/usr/bin/env python3
"""
Market-Data Client   (COL334 A2)

    run-market-data <host> <port> <instrument> [instrument ...]

Read-only with respect to trading (§2.4): only SUBSCRIBE, UNSUBSCRIBE and QUIT
are permitted. The instruments named on the command line are subscribed on
connect; TRADE updates are then printed as the server pushes them.

WHY THIS CLIENT MULTIPLEXES
    §2.8 lists SUBSCRIBE, UNSUBSCRIBE *and* QUIT as Market-Data Client
    messages. A client that only ever read its socket could not offer
    UNSUBSCRIBE at all without disconnecting first, and could not issue a
    graceful QUIT. So stdin and the socket are both registered with
    select.kqueue() -- the same mechanism the server uses -- and commands may
    be typed while updates are arriving.

    Commands accepted on stdin:  SUBSCRIBE <instr> | UNSUBSCRIBE <instr> | QUIT

FRAMING
    Uses framing.Framer for the inbound stream, so multiple TRADE lines
    coalesced into one segment, or one TRADE split across two recv()s, are
    both handled correctly rather than corrupting the display. This matters
    here in particular: the server emits one TRADE per match, and a burst of
    matches can arrive as a single segment.
"""

from __future__ import annotations

import select
import socket
import sys

import framing


def main() -> None:
    if len(sys.argv) < 3:
        sys.stderr.write("usage: market_data.py <host> <port> [instrument ...]\n")
        raise SystemExit(2)

    host, port = sys.argv[1], int(sys.argv[2])
    instruments = sys.argv[3:] or ["JNST"]

    if not hasattr(select, "kqueue"):
        sys.stderr.write("FATAL: select.kqueue() unavailable on %s\n" % sys.platform)
        raise SystemExit(2)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, port))
    for inst in instruments:
        s.sendall(("SUBSCRIBE %s\n" % inst).encode())
        print("[sent] SUBSCRIBE %s" % inst, file=sys.stderr, flush=True)
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
                        print(line.decode("utf-8", "replace"), flush=True)
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
                        # then keep reading until it closes from its end. That
                        # is a half-close -- exactly the FIN semantics
                        # Experiment 6 Part A investigates, performed by the
                        # client rather than only described in the report.
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
