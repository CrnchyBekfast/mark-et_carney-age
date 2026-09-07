#!/usr/bin/env python3
"""
Reduce an EXCH_TRACE JSONL to small time-bucketed CSVs for plotting.
NOT part of the submission.

    python3 src/tools/exp7_csv.py <trace.jsonl> <out-prefix> [--bucket-ms N]

Exp 7's volume-forced run produces ~5M trace events (~200 MB of JSONL). That
is far too large to move off the VM or feed to matplotlib directly, and the
figures only need a few thousand points. This streams the trace once, in
constant memory, and writes three small CSVs:

    <prefix>_conn.csv     t_ms, sid, pending, cum_queued, cum_sent
    <prefix>_engine.csv   t_ms, n, p50_ns, p90_ns, p99_ns, max_ns
    <prefix>_events.csv   t_ms, ev, sid

cum_sent is derived exactly, not approximated: `pending` is the count of bytes
handed to queue_out but not yet accepted by the kernel, so

    cum_sent(t) = cum_queued(t) - pending(t)

which is what makes P3 (cumulative queued vs sent) plottable from queue_out
events alone -- the flush loop does not emit a per-send trace line.

Buckets are flushed as the clock advances, relying on tracelog writing events
in monotonic order, so memory stays flat regardless of trace size.
"""

import json
import sys


def percentile(sorted_vals, q):
    if not sorted_vals:
        return 0
    i = int(round((len(sorted_vals) - 1) * q))
    return sorted_vals[i]


def main():
    argv = sys.argv[1:]
    bucket_ms = 100.0
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == "--bucket-ms":
            bucket_ms = float(argv[i + 1])
            i += 2
        elif argv[i].startswith("--"):
            i += 2
        else:
            args.append(argv[i])
            i += 1

    if len(args) != 2:
        sys.stderr.write(__doc__)
        raise SystemExit(2)

    path, prefix = args[0], args[1]

    conn_f = open(prefix + "_conn.csv", "w")
    eng_f = open(prefix + "_engine.csv", "w")
    evt_f = open(prefix + "_events.csv", "w")
    conn_f.write("t_ms,sid,pending,cum_queued,cum_sent\n")
    eng_f.write("t_ms,n,p50_ns,p90_ns,p99_ns,max_ns\n")
    evt_f.write("t_ms,ev,sid\n")

    # per-sid running state, carried across buckets
    cum_queued = {}
    pending = {}
    eng_bucket = []
    cur_bucket = None
    n_lines = 0
    n_bad = 0

    def flush(bucket):
        if bucket is None:
            return
        t = bucket * bucket_ms
        for sid in sorted(cum_queued):
            q = cum_queued[sid]
            p = pending.get(sid, 0)
            conn_f.write("%.1f,%s,%d,%d,%d\n" % (t, sid, p, q, q - p))
        if eng_bucket:
            eng_bucket.sort()
            eng_f.write("%.1f,%d,%d,%d,%d,%d\n"
                        % (t, len(eng_bucket),
                           percentile(eng_bucket, .50),
                           percentile(eng_bucket, .90),
                           percentile(eng_bucket, .99),
                           eng_bucket[-1]))

    with open(path, "r", errors="replace") as fh:
        for line in fh:
            n_lines += 1
            try:
                r = json.loads(line)
            except ValueError:
                n_bad += 1
                continue

            t = r.get("t_ms")
            if t is None:
                continue
            b = int(t // bucket_ms)
            if cur_bucket is None:
                cur_bucket = b
            elif b != cur_bucket:
                flush(cur_bucket)
                eng_bucket = []
                cur_bucket = b

            ev = r.get("ev")
            sid = r.get("sid")

            if ev == "queue_out":
                cum_queued[sid] = cum_queued.get(sid, 0) + (r.get("n") or 0)
                pending[sid] = r.get("pending") or 0
            elif ev == "engine":
                ns = r.get("engine_ns")
                if ns is not None:
                    eng_bucket.append(ns)
            elif ev in ("accept", "close", "send_would_block",
                        "write_enable", "write_disable", "notify_dropped",
                        "partial_write", "eof_fin", "eof_rst"):
                evt_f.write("%.3f,%s,%s\n" % (t, ev, sid))

    flush(cur_bucket)

    for f in (conn_f, eng_f, evt_f):
        f.close()

    sys.stderr.write("read %d lines (%d unparseable), bucket=%.0f ms\n"
                     % (n_lines, n_bad, bucket_ms))
    sys.stderr.write("wrote %s_conn.csv %s_engine.csv %s_events.csv\n"
                     % (prefix, prefix, prefix))


if __name__ == "__main__":
    main()
