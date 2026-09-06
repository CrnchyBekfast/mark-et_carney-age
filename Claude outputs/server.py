#!/usr/bin/env python3
"""
Exchange Server -- Day-0 connection-layer stub   (COL334 A2, "The Socket Exchange")

WHAT THIS FILE IS
    The transport layer, built exactly as the finished server will be:
    socket/bind/listen/accept driven by a single-threaded select.kqueue()
    event loop, with per-connection read draining, FIN vs RST discrimination,
    a userspace output buffer with lazily-registered write notification,
    deferred close (reap-after-batch), and full tracing.

WHAT THIS FILE IS NOT (yet)
    It does not parse the application protocol.  Phase 1 plugs in at the one
    marked TODO in handle_readable_bytes():
        framing.py    newline framing over the per-connection rbuf
        protocol.py   LOGIN / BUY / SELL / CANCEL / SUBSCRIBE / ... validation
        engine.py     order book, matching, subscriptions, sessions
    The inline line-splitter below is throwaway observability scaffolding so
    that Experiments 1-3 already produce evidence tonight; framing.py replaces
    it wholesale.

GATE THIS MUST PASS  (roadmap §6, Day 0)
        python3 experiment.py 1
    Start, survive experiment.py's SO_LINGER{1,0} readiness probe -- an
    abortive RST arriving as the very first client, before any experiment
    begins -- accept the experiment client, and hold the idle connection open
    indefinitely.  No idle timeouts, ever: Experiments 1, 2, 5 and 6 all use
    connections that send nothing at all.

CONCURRENCY / I-O DECISION  (report §8.1, README)
    Single-threaded event loop over select.kqueue(), called directly.
    Not `selectors` (it hides which syscall is in use, which is what §4.1.2
    targets and would wreck the viva answer), not asyncio (banned by name),
    no third-party networking libraries.

ENVIRONMENT KNOBS
    argv is fixed by the harness (<host> <port>), so every knob is an env var
    and the launcher passes the environment through for free:
        EXCH_TRACE=<path>    structured JSONL trace, for plotting
        EXCH_SNDBUF=<bytes>  SO_SNDBUF on accepted sockets (Exp 7 control, §5)
        EXCH_WBUF_MAX=<bytes>  userspace backlog cap before slow-consumer evict
"""

from __future__ import annotations

import os
import select
import signal
import socket
import sys

import engine
import framing
import protocol
import tracelog

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
MAX_LINE   = 4096                                         # inbound line cap (§3)
WBUF_MAX   = int(os.environ.get("EXCH_WBUF_MAX", 4 << 20))   # 4 MiB (§5)
READ_CHUNK = 65536
KQ_BATCH   = 256
BACKLOG    = 4096                       # large for the bonus-track ramp (§10)

UNTYPED, TRADER, MARKETDATA = 0, 1, 2
ROLE_NAME = {UNTYPED: "untyped", TRADER: "trader", MARKETDATA: "market-data"}


class Conn:
    """One TCP connection.

    __slots__ is not micro-optimisation here.  It is what keeps a connection
    near ~250 B instead of ~500 B, which is a bonus-track requirement at
    70,000 connections (roadmap §10).
    """

    __slots__ = ("sock", "fd", "sid", "role", "user", "subs",
                 "rbuf", "wbuf", "woff", "wwatch", "dead",
                 "n_recv", "bytes_in", "queued", "sent", "eagain", "wbuf_hwm")

    def __init__(self, sock, sid):
        self.sock     = sock
        self.fd       = sock.fileno()
        self.sid      = sid
        self.role     = UNTYPED       # inferred from first LOGIN / SUBSCRIBE
        self.user     = None
        self.subs     = 0             # bitmask: JNST=1, IMCT=2
        self.rbuf     = bytearray()   # inbound framing residual
        self.wbuf     = bytearray()   # OUTBOUND BACKLOG -- the whole of Exp 7
        self.woff     = 0             # consumed prefix (offset, not erase)
        self.wwatch   = False         # is KQ_FILTER_WRITE currently enabled?
        self.dead     = False         # reap AFTER the batch, never mid-batch
        self.n_recv   = 0
        self.bytes_in = 0
        self.queued   = 0
        self.sent     = 0
        self.eagain   = 0
        self.wbuf_hwm = 0

    def pending(self) -> int:
        return len(self.wbuf) - self.woff


class Server:
    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self.conns  = {}      # fd  -> Conn
        self.by_sid = {}      # sid -> Conn   (live sessions only)
        self.reap   = []      # fds to close AFTER the batch (RULE 1)
        self.next_sid = 1     # monotonic, NEVER reused -> generation-safe id
        self.kq = None
        self.ls = None
        self.lfd = -1
        # Sole authority for order-book, subscription, and username state.
        # server.py owns transport only (sockets, framing buffers, kqueue);
        # it never mutates engine-owned collections directly.
        self.engine = engine.Engine()

    # -- setup ------------------------------------------------------------
    def listen(self) -> None:
        ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Without SO_REUSEADDR you lose minutes to EADDRINUSE every time a
        # previous run left connections behind, hundreds of times over 5 days.
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ls.bind((self.host, self.port))
        ls.listen(BACKLOG)
        ls.setblocking(False)
        self.ls, self.lfd = ls, ls.fileno()

        self.kq = select.kqueue()
        self._kq_set(self.lfd, select.KQ_FILTER_READ,
                     select.KQ_EV_ADD | select.KQ_EV_ENABLE)
        tracelog.emit("listen", fd=self.lfd, host=self.host, port=self.port,
                      backlog=BACKLOG)

    def _kq_set(self, fd: int, filt, flags) -> None:
        self.kq.control([select.kevent(fd, filt, flags)], 0)

    def want_write(self, c: Conn, on: bool) -> None:
        """Enable/disable KQ_FILTER_WRITE.

        KQ_FILTER_WRITE is LEVEL-TRIGGERED: left enabled on an empty buffer it
        fires continuously and the loop spins at 100% CPU -- visible and
        damning in Experiment 7's CPU column.  This is the one failure mode of
        the whole design, so it lives in its own function.
        """
        if on == c.wwatch:
            return
        flags = (select.KQ_EV_ADD | select.KQ_EV_ENABLE) if on \
            else select.KQ_EV_DISABLE
        try:
            self._kq_set(c.fd, select.KQ_FILTER_WRITE, flags)
        except OSError as e:
            tracelog.emit("kq_write_filter_error", sid=c.sid, fd=c.fd,
                          errno=e.errno)
            return
        c.wwatch = on
        tracelog.emit("write_enable" if on else "write_disable",
                      sid=c.sid, fd=c.fd, pending=c.pending())

    # -- main loop --------------------------------------------------------
    def run(self) -> None:
        while True:
            try:
                events = self.kq.control(None, KQ_BATCH, None)   # block forever
            except InterruptedError:                             # EINTR
                continue
            except OSError as e:
                tracelog.emit("kevent_error", errno=e.errno)
                return
            # Blocked in kevent() above, which is why `ps -o wchan` reports
            # kqread and never sbwait.  That one word is the entire
            # Experiment 4 proof (roadmap §8.4).

            for ev in events:
                fd = ev.ident

                if fd == self.lfd:
                    self.on_accept()
                    continue

                c = self.conns.get(fd)
                if c is None or c.dead:
                    continue                      # RULE 1 guard

                # RULE 3: no exception may escape this loop.  In C++ the fatal
                # trap is SIGPIPE; in Python it is an uncaught exception taking
                # every client down at once and failing §4.4.
                try:
                    if ev.filter == select.KQ_FILTER_READ:
                        self.on_readable(c, ev)
                    if not c.dead and ev.filter == select.KQ_FILTER_WRITE:
                        self.flush(c)
                except (ConnectionResetError, BrokenPipeError) as e:
                    self.kill(c, type(e).__name__)
                except OSError as e:
                    self.kill(c, "errno=%d" % (e.errno or 0))
                except Exception as e:            # a bug must cost 1 client, not all
                    tracelog.emit("internal_error", sid=c.sid, fd=c.fd,
                                  exc=repr(e))
                    self.kill(c, "internal_error")

            # RULE 1: close() happens ONLY here, outside the batch.  Closing
            # mid-batch lets the kernel recycle the fd for a new accept() in
            # this same batch, and a stale event then lands on the wrong Conn.
            for fd in self.reap:
                self.destroy(fd)
            del self.reap[:]

    # -- accept -----------------------------------------------------------
    def on_accept(self) -> None:
        while True:                               # drain the whole backlog
            try:
                cs, peer = self.ls.accept()
            except BlockingIOError:
                return
            except OSError as e:
                tracelog.emit("accept_error", errno=e.errno)
                return

            cs.setblocking(False)
            try:
                cs.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

            sb = os.environ.get("EXCH_SNDBUF")    # Experiment 7 control (§5)
            if sb:
                try:
                    cs.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, int(sb))
                except (OSError, ValueError) as e:
                    tracelog.emit("sndbuf_error", detail=str(e))

            c = Conn(cs, self.next_sid)
            self.next_sid += 1
            self.conns[c.fd] = c
            self.by_sid[c.sid] = c
            self._kq_set(c.fd, select.KQ_FILTER_READ,
                         select.KQ_EV_ADD | select.KQ_EV_ENABLE)
            # KQ_FILTER_WRITE is registered lazily, only when wbuf is non-empty.
            tracelog.emit("accept", sid=c.sid, fd=c.fd,
                          peer="%s:%d" % peer, nconn=len(self.conns))

    # -- read path --------------------------------------------------------
    def on_readable(self, c: Conn, ev) -> None:
        while True:
            try:
                data = c.sock.recv(READ_CHUNK)
            except BlockingIOError:
                return
            except ConnectionResetError:
                # Experiment 6B, Experiment 8, AND experiment.py's own
                # readiness probe -- which is the very first client this
                # server ever sees.  Treating this as fatal fails all 8
                # experiments before Experiment 1 starts.
                tracelog.emit("eof_rst", sid=c.sid, fd=c.fd, errno=54,
                              ev_fflags=ev.fflags)
                self.kill(c, "RST")
                return

            if not data:
                # FIN: orderly close or half-close (Exp 2, 6A), and also how
                # a SIGKILLed peer appears -- the kernel sends a normal FIN,
                # because TCP has no concept of "the process died" (Exp 8).
                tracelog.emit("eof_fin", sid=c.sid, fd=c.fd,
                              ev_eof=bool(ev.flags & select.KQ_EV_EOF))
                self.kill(c, "FIN")
                return

            c.n_recv   += 1
            c.bytes_in += len(data)
            before = len(c.rbuf)
            c.rbuf += data
            # Experiment 3's evidence: one line per recv() showing that a
            # single application message can arrive in several pieces.
            tracelog.emit("recv", sid=c.sid, fd=c.fd, n=len(data),
                          rbuf_before=before, rbuf_after=len(c.rbuf),
                          hex=bytes(data[:32]).hex())

            self.handle_readable_bytes(c)
            if c.dead:
                return

            # Buffer-overflow guard.  Nothing in the handout demands this, but
            # Experiment 4 parks 20 bytes with no newline and stops forever; a
            # client sending 100 MB with no '\n' would grow rbuf without limit.
            if len(c.rbuf) > MAX_LINE:
                self.queue_out(c.sid, b"ERROR line_too_long\n")
                self.kill(c, "line_too_long")
                return

    def handle_readable_bytes(self, c: Conn) -> None:
        """
        Phase 1/2 wiring: framing.py -> protocol.py -> engine.py.

        on_readable() already appended the newly-arrived bytes into c.rbuf
        before calling this, so rather than adding a second per-connection
        buffer (and a `framer` slot to Conn), a throwaway Framer is pointed
        directly at c.rbuf as its backing store: fr.buf = c.rbuf makes them
        the same bytearray object, so feed(b"") -- append nothing, just
        scan+compact what's already buffered -- performs PHASE_1_2_SPEC.md's
        mandated single eager compaction (amendment 2) in place on c.rbuf
        itself, correctly, even when the loop below breaks early on c.dead.
        The caller's existing `len(c.rbuf) > MAX_LINE` guard right after this
        call is therefore still checking the right (now-compacted) buffer.

        Every step below is pure in-memory computation on already-buffered
        bytes -- no send()/recv()/select() call anywhere in this path, so
        nothing here can block the kqueue loop. queue_out() only appends to
        c.wbuf; the actual write happens later, asynchronously, off the
        write-ready kqueue event.
        """
        fr = framing.Framer(max_line=MAX_LINE)
        fr.buf = c.rbuf
        for line in fr.feed(b""):
            ok, payload = protocol.parse_line(line)
            if not ok:
                self.queue_out(c.sid, b"ERROR %s\n" % payload)
                continue
            for sid, msg in self.engine.handle(c.sid, payload):
                self.queue_out(sid, msg)
            if c.dead:
                break

    # -- write path: this is the entire Experiment 7 mechanism -------------
    def queue_out(self, sid: int, msg: bytes) -> None:
        c = self.by_sid.get(sid)
        if c is None or c.dead:
            # §2.6: a resting order may fill after its owner disconnected.
            # The trade proceeds, subscribers still get TRADE, and the private
            # BOUGHT/SOLD is simply dropped.  Implemented by omission.
            tracelog.emit("notify_dropped", target_sid=sid, msg=msg)
            return

        c.wbuf   += msg
        c.queued += len(msg)
        pend = c.pending()
        if pend > c.wbuf_hwm:
            c.wbuf_hwm = pend
        tracelog.emit("queue_out", sid=c.sid, fd=c.fd, n=len(msg),
                      pending=pend, hwm=c.wbuf_hwm)

        if pend > WBUF_MAX:
            # Slow-consumer eviction: the spec is silent on permanently slow
            # readers, so the policy is chosen and documented (roadmap §5).
            self.kill(c, "slow_consumer")
            return

        self.flush(c)          # opportunistic; normally completes in one send

    def flush(self, c: Conn) -> None:
        while c.woff < len(c.wbuf):
            try:
                # memoryview slice = no copy.  During Experiment 7 the pending
                # tail reaches ~72 KB; c.wbuf[c.woff:] would copy it on every
                # attempt, making the write path O(n^2) and perturbing the very
                # measurement being taken.
                #
                # CAREFUL: this memoryview is deliberately an UNNAMED temporary.
                # A bytearray cannot be resized while a memoryview of it is
                # exported, and the compaction below resizes c.wbuf.  As a
                # temporary it is released during expression cleanup, so the
                # resize is safe -- but if you ever bind it to a variable you
                # MUST call .release() before the `del c.wbuf[...]`, or you get
                # BufferError: Existing exports of data: object cannot be re-sized.
                n = c.sock.send(memoryview(c.wbuf)[c.woff:])
            except BlockingIOError:
                c.eagain += 1
                tracelog.emit("send_would_block", sid=c.sid, fd=c.fd,
                              pending=c.pending(), eagain=c.eagain)
                if c.woff * 2 > len(c.wbuf):          # amortised compaction
                    del c.wbuf[:c.woff]
                    c.woff = 0
                self.want_write(c, True)
                return          # engine path returns IMMEDIATELY. Never blocks.
            except (BrokenPipeError, ConnectionResetError):
                self.kill(c, "peer_gone")             # Experiment 8
                return

            if n < c.pending():
                tracelog.emit("partial_write", sid=c.sid, fd=c.fd, n=n)
            c.woff += n
            c.sent += n

        del c.wbuf[:]
        c.woff = 0
        self.want_write(c, False)

    # -- teardown ---------------------------------------------------------
    def kill(self, c: Conn, why: str) -> None:
        if c.dead:
            return
        c.dead = True

        # engine.on_disconnect is the SOLE authority for session/subscription
        # cleanup (usernames, subs) and for computing the §2.6 proof metric.
        # It does NOT touch orders/level -- resting orders survive by design.
        surviving_orders = self.engine.on_disconnect(c.sid)

        # NOTE (Phase 3 decision point, not resolved here): c.role below is
        # never mutated anywhere in this file -- role commitment now lives
        # entirely in engine.Session, set only during handle_readable_bytes'
        # eventual real wiring. Once that wiring lands, prefer reading the
        # role for this trace line from self.engine.sessions.get(c.sid)
        # (captured BEFORE the on_disconnect() call above, since that pops
        # the session) rather than maintaining a second, parallel Conn.role.
        tracelog.emit("close", sid=c.sid, fd=c.fd, why=why,
                      role=ROLE_NAME[c.role], recvs=c.n_recv,
                      bytes_in=c.bytes_in, queued=c.queued, sent=c.sent,
                      eagain=c.eagain, wbuf_hwm=c.wbuf_hwm,
                      orders_surviving=surviving_orders)

        # Notifications addressed to this sid now drop (see queue_out).
        self.by_sid.pop(c.sid, None)
        # TODO Phase 2: subs[i].discard(c.sid); usernames.discard(c.user)
        #      and DO NOT touch the order book -- resting orders survive (§2.6).
        self.reap.append(c.fd)

    def destroy(self, fd: int) -> None:
        c = self.conns.pop(fd, None)
        if c is None:
            return
        try:
            c.sock.close()      # closing the fd drops its kqueue registrations
        except OSError:
            pass
        tracelog.emit("destroy", sid=c.sid, fd=fd, nconn=len(self.conns))


def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    try:
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    except ValueError:
        sys.stderr.write("usage: server.py <host> <port>\n")
        raise SystemExit(2)

    if not hasattr(select, "kqueue"):
        sys.stderr.write(
            "FATAL: select.kqueue() is unavailable on this platform (%s).\n"
            "This server targets FreeBSD, and macOS for development.\n"
            % sys.platform)
        raise SystemExit(2)

    # experiment.py's terminate_process() sends SIGTERM to our process group.
    # Python's DEFAULT SIGTERM action exits without running any cleanup, which
    # truncates the JSONL trace and loses the tail of every experiment.
    def _bye(signo, _frame):
        tracelog.emit("signal", signo=signo)
        tracelog.close()
        os._exit(0)

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)

    srv = Server(host, port)
    srv.listen()
    try:
        srv.run()
    finally:
        tracelog.close()


if __name__ == "__main__":
    main()
