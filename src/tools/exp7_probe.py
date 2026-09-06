#!/usr/bin/env python3
"""
Experiment 7 diagnostic probe.  NOT part of the submission.

    python3 src/tools/exp7_probe.py <host> <port> <n_pairs> [--rcvbuf N]

Answers the one question exp7_load.py cannot: does the non-reading socket
ACTUALLY hold the bytes netstat claims it holds?

exp7_load.py showed the server writing 11,900,003 B to a subscriber that never
calls recv(), with zero EWOULDBLOCK, while that socket's sb_hiwat (netstat -x
R-HIWA) was 81,720.  Either the buffer really absorbed 143x its high-water
mark, or netstat's Recv-Q is not reporting what we think.

This probe reads the buffer occupancy TWO independent ways:

  FIONREAD    ioctl on the client socket -- the kernel's own answer to "how
              many bytes are readable", taken from inside the receiving
              process, never through netstat.
  drain count after the push phase, every byte is read off and counted.  If
              the count matches what the server sent, the bytes were really
              buffered.  If it stops near 81,720, they were not.

--rcvbuf N calls setsockopt(SO_RCVBUF, N) on the slow socket BEFORE connect.
An explicit SO_RCVBUF also clears SB_AUTOSIZE for that sockbuf, so this is the
test of whether an explicitly pinned buffer is enforced when the sysctl-derived
default apparently is not.  Try --rcvbuf 8192.
"""

import socket
import select
import struct
import sys
import time

BATCH = 500
TRADE_BYTES = 17

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


def sockbuf(sock, which):
    try:
        return sock.getsockopt(socket.SOL_SOCKET, which)
    except OSError:
        return -1


def drain(sock):
    total = 0
    while True:
        try:
            b = sock.recv(1 << 20)
        except (BlockingIOError, InterruptedError):
            return total
        except OSError:
            return total
        if not b:
            return total
        total += len(b)


def send_all_nb(sock, data, drains):
    off = 0
    n = len(data)
    while off < n:
        try:
            off += sock.send(data[off:])
        except BlockingIOError:
            for d in drains:
                drain(d)
            select.select([], [sock], [], 0.05)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pin_rcvbuf = None
    for i, a in enumerate(sys.argv):
        if a == "--rcvbuf":
            pin_rcvbuf = int(sys.argv[i + 1])

    if len(args) != 3:
        sys.stderr.write(__doc__)
        raise SystemExit(2)

    host, port, n_pairs = args[0], int(args[1]), int(args[2])
    offered = n_pairs * TRADE_BYTES

    print("=" * 70)
    print("exp7_probe: offering %d B (%.2f MiB) to a socket that never reads"
          % (offered, offered / 1048576.0))
    if pin_rcvbuf is not None:
        print("            SO_RCVBUF explicitly pinned to %d before connect"
              % pin_rcvbuf)
    print("=" * 70)

    # -- the victim -------------------------------------------------------
    slow = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if pin_rcvbuf is not None:
        slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, pin_rcvbuf)
    slow.connect((host, port))
    slow.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    slow.sendall(b"SUBSCRIBE JNST\n")
    rcv0 = sockbuf(slow, socket.SO_RCVBUF)
    print("slow: local_port=%d SO_RCVBUF_at_connect=%d"
          % (slow.getsockname()[1], rcv0))
    time.sleep(0.3)

    # -- the traders ------------------------------------------------------
    buyer = socket.create_connection((host, port))
    seller = socket.create_connection((host, port))
    for s, who in ((buyer, b"exp7_buyer"), (seller, b"exp7_seller")):
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.sendall(b"LOGIN " + who + b"\n")
    time.sleep(0.3)
    buyer.setblocking(False)
    seller.setblocking(False)
    drain(buyer)
    drain(seller)
    traders = (buyer, seller)

    print("buyer local_port=%d  seller local_port=%d"
          % (buyer.getsockname()[1], seller.getsockname()[1]))
    print()
    print("%-10s %14s %14s %14s" % ("pairs", "offered_B", "FIONREAD", "SO_RCVBUF"))

    buy_line = b"BUY JNST 1 238\n"
    sell_line = b"SELL JNST 1 238\n"
    sent = 0
    report_every = max(BATCH, n_pairs // 12)
    next_report = report_every
    t0 = time.monotonic()

    while sent < n_pairs:
        k = min(BATCH, n_pairs - sent)
        send_all_nb(buyer, buy_line * k, traders)
        send_all_nb(seller, sell_line * k, traders)
        drain(buyer)
        drain(seller)
        sent += k
        if sent >= next_report:
            print("%-10d %14d %14d %14d"
                  % (sent, sent * TRADE_BYTES, fionread(slow),
                     sockbuf(slow, socket.SO_RCVBUF)))
            next_report += report_every

    push_elapsed = time.monotonic() - t0
    print()
    print("push complete: %d pairs, %d B offered, %.1f s"
          % (sent, offered, push_elapsed))
    print("settling 2 s before the drain...")
    time.sleep(2.0)

    fr_before = fionread(slow)
    rcv_end = sockbuf(slow, socket.SO_RCVBUF)
    print()
    print("IMMEDIATELY BEFORE DRAIN:")
    print("  FIONREAD (kernel says readable) : %d" % fr_before)
    print("  SO_RCVBUF                       : %d  (at connect: %d)"
          % (rcv_end, rcv0))
    print()

    # -- THE DECISIVE MEASUREMENT ----------------------------------------
    print("draining the slow socket and counting every byte...")
    slow.setblocking(False)
    t1 = time.monotonic()
    total = 0
    idle_rounds = 0
    while True:
        n = drain(slow)
        total += n
        if n == 0:
            idle_rounds += 1
            if idle_rounds >= 20:
                break
            time.sleep(0.05)
        else:
            idle_rounds = 0
    drain_elapsed = time.monotonic() - t1

    print()
    print("=" * 70)
    print("RESULT")
    print("=" * 70)
    print("  offered by server (n_pairs * 17) : %d B" % offered)
    print("  FIONREAD before drain            : %d B" % fr_before)
    print("  ACTUALLY DRAINED                 : %d B" % total)
    print("  drain took                       : %.2f s" % drain_elapsed)
    print("  SO_RCVBUF at connect / at end    : %d / %d" % (rcv0, rcv_end))
    print()
    if total >= offered * 0.95:
        print("  => the buffer REALLY held it. sb_hiwat did not gate acceptance.")
    elif fr_before > 0 and abs(total - fr_before) < max(4096, fr_before * 0.05):
        print("  => drained == FIONREAD, but far short of what the server sent.")
        print("     The missing bytes were never actually delivered; look at the")
        print("     server's sent counter and the wire capture.")
    else:
        print("  => drained and FIONREAD disagree; netstat/FIONREAD accounting")
        print("     is not measuring what we assumed. Compare against tcpdump.")
    print()

    for s in (slow, buyer, seller):
        try:
            s.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
