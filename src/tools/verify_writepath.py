#!/usr/bin/env python3
"""
Throwaway verification of server.py's write path WITHOUT kqueue.

queue_out() / flush() / want_write() touch the kqueue only through
Server._kq_set, so stubbing that one method lets the entire Experiment 7
mechanism be exercised on Linux against a real socket pair.

This file is NOT part of the submission. It exists to prove, before the
FreeBSD VM is even built, that:
  1. a normal (draining) peer never accumulates userspace backlog
  2. a peer that stops reading DOES accumulate backlog, and flush() returns
     immediately instead of blocking  <-- the whole point of Experiment 7
  3. BlockingIOError is counted, not raised
  4. buffer compaction keeps memory linear, not quadratic
  5. queue_out() to a departed session drops the message instead of raising
     (§2.6: a fill after its owner disconnected)
"""
import os
import tempfile
import socket
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_TRACE = os.path.join(tempfile.gettempdir(), "verify_trace.jsonl")
os.environ["EXCH_TRACE"] = _TRACE
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

# Shim the kqueue constants that only exist on BSD, so the write path (which
# does not depend on kqueue semantics, only on the constant values it passes
# through to _kq_set) can be exercised on Linux.
import select as _sel  # noqa: E402
for _n, _v in [("KQ_FILTER_READ", -1), ("KQ_FILTER_WRITE", -2),
               ("KQ_EV_ADD", 1), ("KQ_EV_DELETE", 2),
               ("KQ_EV_ENABLE", 4), ("KQ_EV_DISABLE", 8),
               ("KQ_EV_EOF", 0x8000)]:
    if not hasattr(_sel, _n):
        setattr(_sel, _n, _v)

import server as S  # noqa: E402

FAILS = []


def check(cond, label, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (("  " + detail) if detail else ""))
    if not cond:
        FAILS.append(label)


def make_pair(sndbuf=None):
    a, b = socket.socketpair()          # a = "server side", b = "peer"
    if sndbuf:
        a.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sndbuf)
        b.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, sndbuf)
    a.setblocking(False)
    return a, b


def new_server():
    srv = S.Server("127.0.0.1", 0)
    srv._kq_set = lambda fd, filt, flags: None      # stub out kqueue
    return srv


print("\n1. normal draining peer accumulates no backlog")
srv = new_server()
a, b = make_pair()
c = S.Conn(a, 1)
srv.conns[c.fd] = c
srv.by_sid[1] = c
for i in range(200):
    srv.queue_out(1, b"TRADE JNST 1 238\n")
check(c.pending() == 0, "wbuf drained after 200 messages", "pending=%d" % c.pending())
check(c.eagain == 0, "zero EAGAIN events", "eagain=%d" % c.eagain)
check(c.sent == 200 * 17, "all 3400 bytes handed to the kernel", "sent=%d" % c.sent)
check(c.wwatch is False, "KQ_FILTER_WRITE left disabled (no CPU spin)")
got = b.recv(65536)
check(got.startswith(b"TRADE JNST 1 238\n"), "peer received real protocol bytes")
a.close(); b.close()

print("\n2. peer that stops reading -> backlog grows, flush never blocks")
srv = new_server()
a, b = make_pair(sndbuf=4096)           # small buffers, like Exp 7 run B
c = S.Conn(a, 2)
srv.conns[c.fd] = c
srv.by_sid[2] = c
t0 = time.monotonic()
for i in range(5000):                   # experiment.py's TRADE_COUNT
    srv.queue_out(2, b"TRADE JNST 1 238\n")
elapsed = time.monotonic() - t0
check(c.eagain > 0, "BlockingIOError observed and counted", "eagain=%d" % c.eagain)
check(c.pending() > 0, "backlog parked in userspace", "pending=%d B" % c.pending())
check(c.wwatch is True, "KQ_FILTER_WRITE re-armed while backlog exists")
check(elapsed < 2.0, "5000 queue_out calls never blocked", "%.3f s total" % elapsed)
check(c.queued == 5000 * 17, "every message accounted for", "queued=%d" % c.queued)
check(c.queued == c.sent + c.pending(), "queued == sent + pending (no loss)")
check(c.wbuf_hwm >= c.pending(), "high-water mark tracked", "hwm=%d" % c.wbuf_hwm)
print("       feed=%d B  kernel_accepted=%d B  userspace_backlog=%d B"
      % (c.queued, c.sent, c.pending()))

print("\n3. backlog memory stays linear (compaction works)")
check(len(c.wbuf) <= c.pending() * 2 + 64, "wbuf not unboundedly larger than pending",
      "len(wbuf)=%d pending=%d" % (len(c.wbuf), c.pending()))

print("\n4. draining the peer lets flush() catch up")
drained = 0
for _ in range(4000):
    try:
        drained += len(b.recv(65536))
    except (BlockingIOError, OSError):
        break
    srv.flush(c)
    if c.pending() == 0:
        break
check(c.pending() == 0, "backlog fully flushed once peer read", "pending=%d" % c.pending())
check(c.wwatch is False, "KQ_FILTER_WRITE disabled again after drain")
a.close(); b.close()

print("\n5. queue_out to a departed session drops instead of raising (§2.6)")
srv = new_server()
try:
    srv.queue_out(999, b"BOUGHT JNST 60 238\n")     # sid never existed
    check(True, "unknown sid handled without exception")
except Exception as e:
    check(False, "unknown sid handled without exception", repr(e))

a, b = make_pair()
c = S.Conn(a, 3)
srv.conns[c.fd] = c
srv.by_sid[3] = c
srv.kill(c, "test_disconnect")
check(3 not in srv.by_sid, "kill() removes the session from by_sid")
check(c.fd in srv.reap, "kill() defers close to the reap list (RULE 1)")
try:
    srv.queue_out(3, b"BOUGHT JNST 60 238\n")
    check(True, "post-disconnect notification dropped without exception")
except Exception as e:
    check(False, "post-disconnect notification dropped without exception", repr(e))
srv.destroy(c.fd)
check(c.fd not in srv.conns, "destroy() removes the Conn")
b.close()

print("\n6. trace file is well-formed JSONL")
S.tracelog.close()
import json  # noqa: E402
n, bad = 0, 0
kinds = {}
with open(_TRACE) as fh:
    for ln in fh:
        ln = ln.strip()
        if not ln:
            continue
        n += 1
        try:
            rec = json.loads(ln)
            kinds[rec["ev"]] = kinds.get(rec["ev"], 0) + 1
        except Exception:
            bad += 1
check(n > 0 and bad == 0, "every trace line parses as JSON", "%d records, %d bad" % (n, bad))
check("send_would_block" in kinds, "send_would_block events present in trace",
      "count=%d" % kinds.get("send_would_block", 0))
check("notify_dropped" in kinds, "notify_dropped events present in trace",
      "count=%d" % kinds.get("notify_dropped", 0))
print("       event kinds: " + ", ".join("%s=%d" % kv for kv in sorted(kinds.items())))

print("\n" + "=" * 62)
if FAILS:
    print("FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("ALL CHECKS PASSED - Experiment 7 write path verified without kqueue")
