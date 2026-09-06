#!/usr/bin/env python3
"""
Experiment 7 trace reducer.  NOT part of the submission.

    python3 src/tools/exp7_report.py <trace.jsonl>

Reads an EXCH_TRACE JSONL file ({"t_ms":..., "ev":..., "sid":..., ...}) and
prints every number the Experiment 7 write-up needs to assert, split around
the backpressure onset (the first send_would_block event).

The three claims it is built to adjudicate:

  P1  backpressure occurred          -- send_would_block > 0, wbuf grew
  P2  the engine stayed decoupled    -- engine_ns distribution unchanged
                                        across the onset, throughput flat
  P3  only the slow consumer suffered -- per-sid queued/sent divergence
"""

import json
import sys
from collections import defaultdict, Counter


def pct(sorted_vals, q):
    if not sorted_vals:
        return 0
    i = int(round((len(sorted_vals) - 1) * q))
    return sorted_vals[i]


def fmt_us(ns):
    return "%.1f" % (ns / 1000.0)


def main():
    if len(sys.argv) != 2:
        sys.stderr.write(__doc__)
        raise SystemExit(2)

    path = sys.argv[1]

    ev_counts = Counter()
    accepts = []
    closes = []
    swb_by_sid = Counter()
    onset_t = None
    onset_sid = None
    partial_by_sid = Counter()
    queued_by_sid = defaultdict(int)
    trade17_by_sid = Counter()
    hwm_by_sid = defaultdict(int)
    pending_max_by_sid = defaultdict(int)
    notify_dropped = 0

    eng_pre, eng_post = [], []
    eng_t_pre, eng_t_post = [], []
    bad = 0

    with open(path, "r", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                bad += 1
                continue

            e = r.get("ev")
            t = r.get("t_ms")
            sid = r.get("sid")
            ev_counts[e] += 1

            if e == "accept":
                accepts.append(r)
            elif e == "close":
                closes.append(r)
            elif e == "notify_dropped":
                notify_dropped += 1
            elif e == "partial_write":
                partial_by_sid[sid] += 1
            elif e == "send_would_block":
                swb_by_sid[sid] += 1
                if onset_t is None:
                    onset_t, onset_sid = t, sid
            elif e == "queue_out":
                n = r.get("n") or 0
                queued_by_sid[sid] += n
                if n == 17:
                    trade17_by_sid[sid] += 1
                h = r.get("hwm") or 0
                if h > hwm_by_sid[sid]:
                    hwm_by_sid[sid] = h
                p = r.get("pending") or 0
                if p > pending_max_by_sid[sid]:
                    pending_max_by_sid[sid] = p
            elif e == "engine":
                ns = r.get("engine_ns")
                if ns is None:
                    continue
                if onset_t is None:
                    eng_pre.append(ns)
                    eng_t_pre.append(t)
                else:
                    eng_post.append(ns)
                    eng_t_post.append(t)

    W = 78
    print("=" * W)
    print("Experiment 7 trace report:", path)
    print("=" * W)
    if bad:
        print("unparseable lines skipped: %d" % bad)
    print()

    print("-- event counts " + "-" * (W - 16))
    for e, n in ev_counts.most_common():
        print("  %-20s %10d" % (e, n))
    print()

    print("-- connections (kernel buffers actually in force) " + "-" * (W - 50))
    print("  %-5s %-5s %-22s %10s %10s" % ("sid", "fd", "peer", "SO_SNDBUF", "SO_RCVBUF"))
    for a in accepts:
        print("  %-5s %-5s %-22s %10s %10s"
              % (a.get("sid"), a.get("fd"), a.get("peer"),
                 a.get("sndbuf", "n/a"), a.get("rcvbuf", "n/a")))
    print()

    print("-- P1  backpressure onset " + "-" * (W - 26))
    if onset_t is None:
        print("  NO send_would_block EVENT -- backpressure never occurred.")
        print("  The write path was never exercised. Increase offered volume or")
        print("  lower the absorption ceiling; do not report this as a finding")
        print("  about the server.")
    else:
        print("  first send_would_block : t=%.3f ms  sid=%s" % (onset_t, onset_sid))
        print("  send_would_block/sid   : %s" % dict(swb_by_sid))
        print("  partial_write/sid      : %s" % (dict(partial_by_sid) or "{}"))
        pre_trades = len(eng_pre)
        print("  engine calls before onset: %d" % pre_trades)
    print()

    print("-- P2  engine decoupling (engine_ns, microseconds) " + "-" * (W - 50))
    a_s, b_s = sorted(eng_pre), sorted(eng_post)
    print("  %-14s %8s %9s %9s %9s %9s %9s"
          % ("window", "n", "p50", "p90", "p99", "p99.9", "max"))
    for name, vals in (("pre-onset", a_s), ("post-onset", b_s)):
        if not vals:
            print("  %-14s %8d %9s %9s %9s %9s %9s"
                  % (name, 0, "-", "-", "-", "-", "-"))
            continue
        print("  %-14s %8d %9s %9s %9s %9s %9s"
              % (name, len(vals), fmt_us(pct(vals, .50)), fmt_us(pct(vals, .90)),
                 fmt_us(pct(vals, .99)), fmt_us(pct(vals, .999)),
                 fmt_us(vals[-1])))
    if a_s and b_s:
        r50 = pct(b_s, .50) / float(pct(a_s, .50) or 1)
        r99 = pct(b_s, .99) / float(pct(a_s, .99) or 1)
        print()
        print("  ratio post/pre         : p50 x%.2f   p99 x%.2f" % (r50, r99))
        print("  ASSERTION: both ratios must be O(1). A ratio >> 1 would mean the")
        print("             matching engine slowed down once a consumer stopped")
        print("             reading, i.e. head-of-line blocking.")
    print()
    print("  throughput (engine calls / s)")
    for name, ts in (("pre-onset", eng_t_pre), ("post-onset", eng_t_post)):
        if len(ts) >= 2:
            span = (ts[-1] - ts[0]) / 1000.0
            print("    %-12s %9.0f/s over %.2f s" % (name, len(ts) / span if span else 0, span))
        else:
            print("    %-12s %9s" % (name, "-"))
    print()

    print("-- P3  per-connection isolation " + "-" * (W - 32))
    print("  %-5s %12s %12s %12s %10s" % ("sid", "queued_B", "TRADEs(17B)",
                                          "wbuf_hwm_B", "max_pend"))
    for sid in sorted(queued_by_sid):
        print("  %-5s %12d %12d %12d %10d"
              % (sid, queued_by_sid[sid], trade17_by_sid[sid],
                 hwm_by_sid[sid], pending_max_by_sid[sid]))
    print()
    print("  ASSERTION: the non-reading subscriber shows wbuf_hwm >> 17 while")
    print("             every other sid stays at or near 17 (one message).")
    print("             That is per-connection isolation: the backlog is")
    print("             charged to the slow socket, not to the server.")
    print()

    if closes:
        print("-- close events " + "-" * (W - 16))
        for c in closes:
            print("  sid=%-4s why=%-15s queued=%-10s sent=%-10s eagain=%-7s "
                  "hwm=%-10s orders_surviving=%s"
                  % (c.get("sid"), c.get("why"), c.get("queued"), c.get("sent"),
                     c.get("eagain"), c.get("wbuf_hwm"),
                     c.get("orders_surviving")))
        print()
        for c in closes:
            q, s = c.get("queued") or 0, c.get("sent") or 0
            if q > s:
                print("  sid=%s undelivered backlog at close: %d B (queued %d - sent %d)"
                      % (c.get("sid"), q - s, q, s))
        print()

    if notify_dropped:
        print("notify_dropped (fills for departed sessions): %d" % notify_dropped)
        print()


if __name__ == "__main__":
    main()
