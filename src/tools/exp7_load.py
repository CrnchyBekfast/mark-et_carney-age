#!/usr/bin/env python3
"""
Experiment 7 supplementary load generator.  NOT part of the submission
(same status as verify_writepath.py / verify_naive_stall.py).

WHY THIS EXISTS
    experiment.py 7 cannot force backpressure, and the reason is arithmetic,
    not a server property:

        offered to each subscriber = TRADE_COUNT * len("TRADE JNST 1 238\\n")
                                   = 5000 * 17 = 85,000 B
        stock absorption capacity  = server SO_SNDBUF (32768)
                                   + client SO_RCVBUF (65536)
                                   = 98,304 B          > 85,000 B

    so the null result is guaranteed before the server is even started.  The
    harness also self-rate-limits to ~850 B/s (TRADE_INTERVAL plus two
    settimeout(0.01) drains per trade), so shrinking buffers is the only knob
    it leaves -- and a shrink that does not take effect is indistinguishable
    from a server that never backs up.

    This generator removes the rate limit (pipelined orders, batched writes,
    no per-trade drain) so that TOTAL VOLUME forces the onset.  Volume is a
    deterministic control: no socket buffer can absorb more than
    kern.ipc.maxsockbuf, whatever the tuning sysctls actually do.

    It also prints getsockopt(SO_RCVBUF) for the non-reading socket over
    time.  That is the kernel's own answer, unmediated by netstat's ambiguous
    R-HIWA column, to "did the buffer stay small, or did it auto-grow?"

USAGE
    python3 src/tools/exp7_load.py <host> <port> <n_pairs>

CONNECTIONS
    slow    SUBSCRIBE JNST, then never read again        <- the victim
    buyer   LOGIN, pipelined BUY  JNST 1 238            <- drained continuously
    seller  LOGIN, pipelined SELL JNST 1 238            <- drained continuously

    Each matched pair emits exactly one 17-byte TRADE to every subscriber, so
    md_offered = n_pairs * 17 bytes.  The traders are drained hard on purpose:
    a congested trader would be a second slow consumer and would muddy which
    connection the evidence belongs to.
"""

import socket
import select
import sys
import time

BATCH = 500          # order lines per send() -- keeps the generator off the
                     # critical path so the server, not Python, sets the rate
TRADE_BYTES = 17     # len(b"TRADE JNST 1 238\n")
REPORT_EVERY = 20000


def drain(sock):
    """Read and discard everything currently available. Returns bytes read."""
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
    """
    Non-blocking sendall that keeps the trader sockets drained while it waits,
    so a full inbound buffer on a trader can never deadlock the generator.
    """
    off = 0
    n = len(data)
    while off < n:
        try:
            off += sock.send(data[off:])
        except BlockingIOError:
            for d in drains:
                drain(d)
            select.select([], [sock], [], 0.05)


def rcvbuf(sock):
    try:
        return sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    except OSError:
        return -1


def sndbuf(sock):
    try:
        return sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
    except OSError:
        return -1


def main():
    if len(sys.argv) != 4:
        sys.stderr.write(__doc__)
        raise SystemExit(2)

    host, port, n_pairs = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])

    print("[gen] wall_clock_start=%.6f" % time.time(), flush=True)

    # -- the victim: subscribes, then never reads again -------------------
    slow = socket.create_connection((host, port))
    slow.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    slow.sendall(b"SUBSCRIBE JNST\n")
    slow_rcvbuf_at_connect = rcvbuf(slow)
    print("[gen] slow    local=%s SO_RCVBUF=%d SO_SNDBUF=%d"
          % (slow.getsockname()[1], slow_rcvbuf_at_connect, sndbuf(slow)),
          flush=True)
    time.sleep(0.3)          # let the subscribe ack land; deliberately unread

    # -- the traders: drained continuously -------------------------------
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

    print("[gen] buyer   local=%s   seller local=%s"
          % (buyer.getsockname()[1], seller.getsockname()[1]), flush=True)
    print("[gen] target  n_pairs=%d md_offered=%.2f MiB"
          % (n_pairs, n_pairs * TRADE_BYTES / 1048576.0), flush=True)
    print(flush=True)

    buy_line = b"BUY JNST 1 238\n"
    sell_line = b"SELL JNST 1 238\n"
    traders = (buyer, seller)

    sent = 0
    t0 = time.monotonic()
    next_report = REPORT_EVERY

    while sent < n_pairs:
        k = min(BATCH, n_pairs - sent)
        # Resting side first, then the sweeping side: k BUYs rest on the book,
        # then k SELLs sweep them -> exactly k trades, and the notifications
        # coalesce into large segments (the write path this experiment tests).
        send_all_nb(buyer, buy_line * k, traders)
        send_all_nb(seller, sell_line * k, traders)
        drain(buyer)
        drain(seller)
        sent += k

        if sent >= next_report:
            el = time.monotonic() - t0
            print("[gen] pairs=%-8d elapsed=%6.1fs rate=%7.0f/s "
                  "md_offered=%6.2f MiB slow_SO_RCVBUF=%d"
                  % (sent, el, sent / el if el else 0,
                     sent * TRADE_BYTES / 1048576.0, rcvbuf(slow)), flush=True)
            next_report += REPORT_EVERY

    el = time.monotonic() - t0
    print(flush=True)
    print("[gen] DONE pairs=%d elapsed=%.1fs md_offered=%.2f MiB"
          % (sent, el, sent * TRADE_BYTES / 1048576.0), flush=True)
    print("[gen] slow SO_RCVBUF now=%d (was %d at connect)  <-- growth here "
          "means receive auto-tuning is still active"
          % (rcvbuf(slow), slow_rcvbuf_at_connect), flush=True)
    print("[gen] holding all three connections open; the slow socket has "
          "still never been read.", flush=True)
    print("[gen] take your netstat/ps snapshots now, then Ctrl-C.", flush=True)

    try:
        while True:
            drain(buyer)
            drain(seller)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[gen] interrupted; closing.", flush=True)
    finally:
        for s in (slow, buyer, seller):
            try:
                s.close()
            except OSError:
                pass


if __name__ == "__main__":
    main()
