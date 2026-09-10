#!/usr/bin/env python3

"""single-threaded kqueue tcp server. transport only, typeshi."""

from __future__ import annotations

import os
import select
import signal
import socket
import sys
import time

import framing
import engine
import protocol
import tracelog

# config n stuff
MAX_LINE   = 4096                                            # no giant lines
WBUF_MAX   = int(os.environ.get("EXCH_WBUF_MAX", 4 << 20))  # slow-client cap
READ_CHUNK = 65536
KQ_BATCH   = 256
BACKLOG    = 4096                                            # bonus grind

UNTYPED, TRADER, MARKETDATA = 0, 1, 2
ROLE_NAME = {UNTYPED: "untyped", TRADER: "trader", MARKETDATA: "market-data"}


class Conn:
    """one tcp connection, packed light for the 70k bonus."""

    __slots__ = ("sock", "fd", "sid", "role", "user", "subs",
                 "framer", "wbuf", "woff", "wwatch", "dead", "quitting",
                 "n_recv", "bytes_in", "queued", "sent", "eagain", "wbuf_hwm")

    def __init__(self, sock, sid):
        self.sock     = sock
        self.fd       = sock.fileno()
        self.sid      = sid
        self.role     = UNTYPED       # guessed from the first command
        self.user     = None
        self.subs     = 0             # jnst=1, imct=2
        self.framer   = None          # made on first recv
        self.wbuf     = bytearray()   # slow-client backlog
        self.woff     = 0             # sent prefix
        self.wwatch   = False         # write watch armed or nah
        self.dead     = False         # close after the batch
        self.quitting = False         # drain then dip
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
        self.conns  = {}      # fd to connection
        self.by_sid = {}      # live sid to connection
        self.reap   = []      # close these after the batch
        self.next_sid = 1     # fresh forever, no recycled aura
        self.kq = None
        self.ls = None
        self.lfd = -1
        # engine owns all market state, as it should
        self.engine = engine.Engine()

    # setup vibes
    def listen(self) -> None:
        ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # reuse the address so restarts don't get cursed
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
        """arm writes only while bytes are waiting."""
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

    # the main event-loop situation
    def run(self) -> None:
        while True:
            try:
                events = self.kq.control(None, KQ_BATCH, None)   # nap till an event
            except InterruptedError:                             # signal said hey
                continue
            except OSError as e:
                tracelog.emit("kevent_error", errno=e.errno)
                return
            # kqread means we're waiting on everyone, not one client

            for ev in events:
                fd = ev.ident

                if fd == self.lfd:
                    self.on_accept()
                    continue

                c = self.conns.get(fd)
                if c is None or c.dead:
                    continue                      # stale event, begone

                # one client bug doesn't get to smoke the server
                try:
                    if ev.filter == select.KQ_FILTER_READ:
                        self.on_readable(c, ev)
                    if not c.dead and ev.filter == select.KQ_FILTER_WRITE:
                        self.flush(c)
                except (ConnectionResetError, BrokenPipeError) as e:
                    self.kill(c, type(e).__name__)
                except OSError as e:
                    self.kill(c, "errno=%d" % (e.errno or 0))
                except Exception as e:            # one client takes the l
                    tracelog.emit("internal_error", sid=c.sid, fd=c.fd,
                                  exc=repr(e))
                    self.kill(c, "internal_error")

            # close after the batch so recycled fds don't haunt us
            for fd in self.reap:
                self.destroy(fd)
            del self.reap[:]

    # new connection just dropped
    def on_accept(self) -> None:
        while True:                               # empty the accept queue
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

            sb = os.environ.get("EXCH_SNDBUF")    # slow-client experiment knob
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
            # log the real kernel buffer sizes, no guessing
            try:
                so_snd = cs.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
                so_rcv = cs.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            except OSError:
                so_snd = so_rcv = -1

            tracelog.emit("accept", sid=c.sid, fd=c.fd,
                          peer="%s:%d" % peer, nconn=len(self.conns),
                          sndbuf=so_snd, rcvbuf=so_rcv)

    # read path
    def on_readable(self, c: Conn, ev) -> None:
        while True:
            try:
                data = c.sock.recv(READ_CHUNK)
            except BlockingIOError:
                return
            except ConnectionResetError:
                tracelog.emit("eof_rst", sid=c.sid, fd=c.fd, errno=54,
                              ev_fflags=ev.fflags)
                self.kill(c, "RST")
                return

            if not data:
                tracelog.emit("eof_fin", sid=c.sid, fd=c.fd,
                              ev_eof=bool(ev.flags & select.KQ_EV_EOF))
                self.kill(c, "FIN")
                return

            c.n_recv   += 1
            c.bytes_in += len(data)

            self.handle_readable_bytes(c, data)
            if c.dead:
                return

    def handle_readable_bytes(self, c: Conn, data: bytes) -> None:
        """frame it, parse it, run it, queue it. clean separation typeshi."""
        if c.framer is None:
            c.framer = framing.Framer(max_line=MAX_LINE)

        before = len(c.framer.buf)
        lines = list(c.framer.feed(data))     # compact now, chill later
        tracelog.emit("recv", sid=c.sid, fd=c.fd, n=len(data),
                      rbuf_before=before, rbuf_after=len(c.framer.buf),
                      lines_out=len(lines), hex=bytes(data[:32]).hex())

        for line in lines:
            ok, payload = protocol.parse_line(line)
            if not ok:
                self.queue_out(c.sid, b"ERROR %s\n" % payload)
                if c.dead:
                    return
                continue

            # quit drains promised output before dipping
            if payload[0] == b"QUIT":
                c.quitting = True
                self.flush(c)
                return          # anything after quit gets ghosted

            t0 = time.monotonic_ns()
            results = self.engine.handle(c.sid, payload)
            engine_ns = time.monotonic_ns() - t0
            tracelog.emit("engine", sid=c.sid, n_msgs=len(results), engine_ns=engine_ns)

            for sid, msg in results:
                self.queue_out(sid, msg)

            if c.dead:
                return

        if c.framer.overflowed():
            self.queue_out(c.sid, b"ERROR line_too_long\n")
            self.kill(c, "overflow")
            return

    # write path, aka the slow-client lore
    def queue_out(self, sid: int, msg: bytes) -> None:
        c = self.by_sid.get(sid)
        if c is None or c.dead:
            # owner left; the trade lives but their private alert doesn't
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
            # too slow for this timeline
            self.kill(c, "slow_consumer")
            return

        self.flush(c)          # send now if the kernel's feeling it

    def flush(self, c: Conn) -> None:
        while c.woff < len(c.wbuf):
            try:
                # no-copy slice; don't bind the view or compaction gets mad
                n = c.sock.send(memoryview(c.wbuf)[c.woff:])
            except BlockingIOError:
                c.eagain += 1
                tracelog.emit("send_would_block", sid=c.sid, fd=c.fd,
                              pending=c.pending(), eagain=c.eagain)
                if c.woff * 2 > len(c.wbuf):          # tidy the sent half
                    del c.wbuf[:c.woff]
                    c.woff = 0
                self.want_write(c, True)
                return          # kernel said nah, wait for writable
            except (BrokenPipeError, ConnectionResetError):
                self.kill(c, "peer_gone")             # peer vanished, tragic
                return

            if n < c.pending():
                tracelog.emit("partial_write", sid=c.sid, fd=c.fd, n=n)
            c.woff += n
            c.sent += n

        del c.wbuf[:]
        c.woff = 0
        self.want_write(c, False)

        # empty buffer means quit can finally dip
        if c.quitting:
            self.kill(c, "quit")

    # cleanup arc
    def kill(self, c: Conn, why: str) -> None:
        if c.dead:
            return
        c.dead = True

        # grab the role before engine deletes the session
        _sess = self.engine.sessions.get(c.sid)
        role_name = ROLE_NAME[_sess.role if _sess is not None else c.role]

        surviving_orders = self.engine.on_disconnect(c.sid)

        tracelog.emit("close", sid=c.sid, fd=c.fd, why=why,
                      role=role_name, recvs=c.n_recv,
                      bytes_in=c.bytes_in, queued=c.queued, sent=c.sent,
                      eagain=c.eagain, wbuf_hwm=c.wbuf_hwm,
                      orders_surviving=surviving_orders)

        # future private alerts to this sid vanish
        self.by_sid.pop(c.sid, None)
        # engine cleaned the identity; resting orders stay
        self.reap.append(c.fd)

    def destroy(self, fd: int) -> None:
        c = self.conns.pop(fd, None)
        if c is None:
            return
        try:
            c.sock.close()      # closing also drops kqueue watches
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

    # save the trace before signals take us out
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
