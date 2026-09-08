#!/usr/bin/env python3
"""
Throwaway verification of §2.2.5 QUIT handling, WITHOUT kqueue.
NOT part of the submission (same status as verify_writepath.py).

QUIT is one of only seven protocol verbs and is trivially testable by a
grader, so it is worth proving rather than assuming. Before this was fixed,
engine.handle() returned [] for QUIT and server.py had no QUIT handling at
all, so the connection simply stayed open.

The properties that must hold:
  1. QUIT closes the connection.
  2. QUIT is GRACEFUL: anything already queued for that client is delivered
     before the close, not truncated by it.
  3. A QUIT arriving while the socket is blocked does NOT close early -- the
     close waits for the backlog to drain.
  4. Lines after QUIT in the same batch are discarded.
  5. The normal (non-QUIT) path is unchanged.
  6. Role-gating still holds: a Market-Data client may QUIT too (§2.8).
"""
import os
import socket
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["EXCH_TRACE"] = os.path.join(tempfile.gettempdir(), "verify_quit.jsonl")
sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..")))

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


def make_pair(bufsize=None):
    a, b = socket.socketpair()
    if bufsize:
        a.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, bufsize)
        b.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, bufsize)
    a.setblocking(False)
    return a, b


def new_server():
    srv = S.Server("127.0.0.1", 0)
    srv._kq_set = lambda fd, filt, flags: None
    return srv


def attach(srv, sock, sid):
    c = S.Conn(sock, sid)
    srv.conns[c.fd] = c
    srv.by_sid[sid] = c
    return c


def drain(sock):
    sock.setblocking(False)
    out = b""
    while True:
        try:
            d = sock.recv(1 << 20)
        except (BlockingIOError, OSError):
            break
        if not d:
            break
        out += d
    return out


print("\n1. QUIT closes the connection")
srv = new_server()
a, b = make_pair()
c = attach(srv, a, 1)
srv.handle_readable_bytes(c, b"QUIT\n")
check(c.dead, "connection marked dead after QUIT")
check(c.fd in srv.reap, "fd queued for reap (deferred close, not mid-batch)")

print("\n2. QUIT is graceful -- queued output is delivered, not truncated")
srv = new_server()
a, b = make_pair()
c = attach(srv, a, 2)
srv.handle_readable_bytes(c, b"LOGIN alice\nQUIT\n")
got = drain(b)
check(got == b"OK\n", "the LOGIN reply still arrived before the close",
      "got=%r" % got)
check(c.dead, "connection closed after delivering it")

print("\n3. a trade notification queued in the same batch is not lost")
srv = new_server()
a, b = make_pair()
c = attach(srv, a, 3)
srv.handle_readable_bytes(c, b"LOGIN bob\nBUY JNST 10 500\nQUIT\n")
got = drain(b)
check(got == b"OK\nORDER_ACCEPTED 0\n", "OK + ORDER_ACCEPTED both delivered",
      "got=%r" % got)
check(c.dead, "then closed")

print("\n4. QUIT on a blocked socket waits for the backlog to drain")
srv = new_server()
a, b = make_pair(bufsize=4096)
c = attach(srv, a, 4)
# Fill the socket until the write path starts buffering in userspace.
for _ in range(20000):
    srv.queue_out(4, b"TRADE JNST 1 238\n")
    if c.pending() > 0:
        break
backlog = c.pending()
check(backlog > 0, "a userspace backlog exists", "pending=%d" % backlog)
srv.handle_readable_bytes(c, b"QUIT\n")
check(not c.dead, "QUIT did NOT close while output was still pending",
      "pending=%d" % c.pending())
check(c.quitting, "connection is marked quitting")
# Drain the peer, then let the write path run again as kqueue would.
total = b""
for _ in range(500):
    total += drain(b)
    if c.dead:
        break
    srv.flush(c)
check(c.dead, "closed once the backlog finally drained")
check(c.pending() == 0, "nothing left unsent", "pending=%d" % c.pending())

print("\n5. lines after QUIT in the same batch are discarded")
srv = new_server()
a, b = make_pair()
c = attach(srv, a, 5)
srv.handle_readable_bytes(c, b"LOGIN carol\nQUIT\nBUY JNST 1 1\n")
got = drain(b)
check(b"ORDER_ACCEPTED" not in got, "the post-QUIT BUY was not executed",
      "got=%r" % got)

print("\n6. a Market-Data client may QUIT too (§2.8)")
srv = new_server()
a, b = make_pair()
c = attach(srv, a, 6)
srv.handle_readable_bytes(c, b"SUBSCRIBE JNST\nQUIT\n")
got = drain(b)
check(got == b"OK\n", "SUBSCRIBE acknowledged first", "got=%r" % got)
check(c.dead, "then closed")

print("\n7. the normal path is unchanged (no premature close)")
srv = new_server()
a, b = make_pair()
c = attach(srv, a, 7)
srv.handle_readable_bytes(c, b"LOGIN dave\n")
check(not c.dead, "no QUIT -> connection stays open")
check(not c.quitting, "quitting flag not set spuriously")
srv.handle_readable_bytes(c, b"BUY JNST 5 100\n")
check(not c.dead, "still open after an order")
got = drain(b)
check(got == b"OK\nORDER_ACCEPTED 0\n", "normal replies intact", "got=%r" % got)

print("\n8. UNSUBSCRIBE is accepted and acknowledged (§2.4)")
srv = new_server()
a, b = make_pair()
c = attach(srv, a, 8)
srv.handle_readable_bytes(c, b"SUBSCRIBE JNST\nUNSUBSCRIBE JNST\n")
got = drain(b)
check(got == b"OK\nOK\n", "both acknowledged", "got=%r" % got)
check(8 not in srv.engine.subs[0], "sid removed from the JNST subscriber set")

print()
if FAILS:
    print("FAILED: %d" % len(FAILS))
    for f in FAILS:
        print("   - " + f)
    raise SystemExit(1)
print("all QUIT / UNSUBSCRIBE checks passed")
