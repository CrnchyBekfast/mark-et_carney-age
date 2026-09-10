#!/usr/bin/env python3
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
    s.setblocking(False)

    sfd, ifd = s.fileno(), sys.stdin.fileno()
    kq = select.kqueue()
    kq.control([
        select.kevent(sfd, select.KQ_FILTER_READ, select.KQ_EV_ADD | select.KQ_EV_ENABLE),
        select.kevent(ifd, select.KQ_FILTER_READ, select.KQ_EV_ADD | select.KQ_EV_ENABLE),
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
                        return
                    for line in fr.feed(data):
                        print(line.decode("utf-8", "replace"), flush=True)
                    if fr.overflowed():
                        return
                elif not quitting:
                    line = sys.stdin.readline()
                    if not line:
                        return
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        s.sendall((line + "\n").encode())
                    except (BrokenPipeError, OSError):
                        return
                    if line.upper() == "QUIT":
                        try:
                            s.shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                        try:
                            kq.control([select.kevent(ifd, select.KQ_FILTER_READ,
                                                      select.KQ_EV_DELETE)], 0)
                        except OSError:
                            pass
                        quitting = True
    except KeyboardInterrupt:
        pass
    finally:
        try:
            s.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
