#!/usr/bin/env python3
"""
Minimal standalone socket-buffer probe.  NOT part of the submission.
No exchange server, no engine, no protocol -- just a blasting sender and a
receiver that never reads.

Isolates the Experiment 7 anomaly (a non-reading TCP receiver on lo0 absorbing
~143x its sb_hiwat with the sender never seeing EWOULDBLOCK) from every piece
of project-specific code, so the phenomenon can be attributed to the kernel
path rather than to anything server.py does.

BLASTER (sender):
    python3 sb_probe.py --server <bind-host> <port> [--sndbuf N] [--bytes N]

VICTIM (receiver that never reads):
    python3 sb_probe.py --client <host> <port> [--rcvbuf N]

Run the victim first, then the blaster.

The blaster reports the byte offset at which send() first returned EWOULDBLOCK
-- that offset IS the total absorption capacity of the path (its own send
buffer plus the peer's receive buffer plus anything in flight).  Compare it
against the peer's sb_hiwat.

The victim reports FIONREAD (the kernel's own count of readable bytes) and
SO_RCVBUF once a second without ever calling recv(), then drains and counts on
Ctrl-C.

To test whether the anomaly is loopback-specific, run the victim on FreeBSD and
the blaster on another machine so the traffic crosses a real interface. Keeping
the victim on FreeBSD is the point: it is the receiving kernel under test.
"""

import socket
import struct
import sys
import time

MSG = b"TRADE JNST 1 238\n"          # 17 B, same shape as the real feed

try:
    import fcntl
    import termios

    def fionread(sock):
        try:
            return struct.unpack("i", fcntl.ioctl(sock.fileno(),
                                                  termios.FIONREAD,
                                                  b"\0" * 4))[0]
        except Exception:
            return -1
except ImportError:
    def fionread(sock):
        return -1


def opt(sock, which):
    try:
        return sock.getsockopt(socket.SOL_SOCKET, which)
    except OSError:
        return -1


def run_server(host, port, sndbuf, limit):
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((host, port))
    ls.listen(8)
    print("blaster: listening on %s:%d, waiting for the victim..." % (host, port))

    cs, peer = ls.accept()
    if sndbuf is not None:
        cs.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sndbuf)
    cs.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    cs.setblocking(False)

    print("blaster: peer=%s:%d SO_SNDBUF=%d (requested %s)"
          % (peer[0], peer[1], opt(cs, socket.SO_SNDBUF), sndbuf))
    print("blaster: writing %d-byte messages until EWOULDBLOCK or %d B"
          % (len(MSG), limit))
    print()
    print("%14s %14s %10s" % ("bytes_accepted", "messages", "elapsed_s"))

    total = 0
    msgs = 0
    eagain = 0
    first_eagain_at = None
    t0 = time.monotonic()
    next_report = 1 << 20
    stall_since = None

    while total < limit:
        try:
            n = cs.send(MSG)
        except BlockingIOError:
            eagain += 1
            if first_eagain_at is None:
                first_eagain_at = total
                print()
                print("    (first EWOULDBLOCK at %d B -- may be a transient "
                      "early stall, not the capacity)" % total)
                print()
            if stall_since is None:
                stall_since = time.monotonic()
            elif time.monotonic() - stall_since > 10.0:
                print()
                print("*** SUSTAINED STALL: no progress for 10 s at %d bytes "
                      "(%d messages)" % (total, msgs))
                break
            time.sleep(0.01)
            continue
        except ConnectionResetError:
            print()
            print("blaster: peer reset the connection at %d B "
                  "(victim exited?)" % total)
            break
        except BrokenPipeError:
            print()
            print("blaster: peer closed at %d B" % total)
            break
        stall_since = None
        total += n
        msgs += 1
        if total >= next_report:
            print("%14d %14d %10.2f" % (total, msgs, time.monotonic() - t0))
            next_report += 1 << 20

    print()
    print("=" * 66)
    print("blaster RESULT")
    print("=" * 66)
    print("  PATH CAPACITY (bytes accepted before sustained stall) : %d" % total)
    print("  messages                  : %d" % msgs)
    print("  first EWOULDBLOCK at      : %s"
          % ("NEVER -- no backpressure at any point" if first_eagain_at is None
             else "%d B (possibly transient)" % first_eagain_at))
    print("  EWOULDBLOCK count         : %d" % eagain)
    print("  SO_SNDBUF in force        : %d" % opt(cs, socket.SO_SNDBUF))
    print()
    print("  Capacity should be roughly own_sndbuf + peer_rcvbuf. If it is")
    print("  orders of magnitude larger, the peer's sb_hiwat is not gating")
    print("  acceptance -- which is the anomaly under investigation.")
    print()
    print("  holding the connection open; Ctrl-C when the victim is done.")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    cs.close()
    ls.close()


def run_client(host, port, rcvbuf):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if rcvbuf is not None:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    s.connect((host, port))
    rcv0 = opt(s, socket.SO_RCVBUF)
    print("victim: connected. local_port=%d SO_RCVBUF=%d (requested %s)"
          % (s.getsockname()[1], rcv0, rcvbuf))
    print("victim: NEVER calling recv(). Ctrl-C to drain and count.")
    print()
    print("%10s %16s %14s" % ("elapsed_s", "FIONREAD", "SO_RCVBUF"))

    t0 = time.monotonic()
    try:
        while True:
            print("%10.1f %16d %14d"
                  % (time.monotonic() - t0, fionread(s), opt(s, socket.SO_RCVBUF)))
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass

    fr = fionread(s)
    print()
    print("victim: draining. FIONREAD said %d readable." % fr)
    s.setblocking(False)
    total = 0
    idle = 0
    t1 = time.monotonic()
    while True:
        try:
            b = s.recv(1 << 20)
        except BlockingIOError:
            idle += 1
            if idle >= 20:
                break
            time.sleep(0.05)
            continue
        except OSError:
            break
        if not b:
            break
        idle = 0
        total += len(b)

    print()
    print("=" * 66)
    print("victim RESULT")
    print("=" * 66)
    print("  FIONREAD before drain : %d B" % fr)
    print("  ACTUALLY DRAINED      : %d B" % total)
    print("  drain took            : %.2f s" % (time.monotonic() - t1))
    print("  SO_RCVBUF start / end : %d / %d" % (rcv0, opt(s, socket.SO_RCVBUF)))
    s.close()


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.stderr.write(__doc__)
        raise SystemExit(2)

    mode = argv[0]

    flags = {}
    rest = []
    i = 1
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            flags[a] = int(argv[i + 1])
            i += 2
        else:
            rest.append(a)
            i += 1

    def flag(name):
        return flags.get(name)

    if mode == "--server":
        if len(rest) != 2:
            sys.stderr.write(__doc__)
            raise SystemExit(2)
        run_server(rest[0], int(rest[1]), flag("--sndbuf"),
                   flag("--bytes") or (64 << 20))
    elif mode == "--client":
        if len(rest) != 2:
            sys.stderr.write(__doc__)
            raise SystemExit(2)
        run_client(rest[0], int(rest[1]), flag("--rcvbuf"))
    else:
        sys.stderr.write(__doc__)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
