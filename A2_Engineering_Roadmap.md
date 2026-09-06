# The Socket Exchange — Engineering Roadmap (Python 3, solo, bonus in scope)

**Context:** COL334 A2. Deadline Thu 10 Sep 2026, 23:59 IST — **5.5 working days**. Viva ~1 month out. Grading: 35% implementation + 40% experiments + 5% report + 20% viva + 10% bonus.

**Language: Python 3, `socket` + `select.kqueue()` used directly.** Supersedes the earlier C++ version of this document.

---

## §0. Why the pivot is right, and one premise worth correcting

**The decision is correct.** My earlier case for C++ rested on a single load-bearing claim: the viva is 20% and you explain C++ better. A month of preparation dissolves that. What's left is Python's ~8-hour implementation saving inside a 5.5-day solo window, which is not a marginal gain — it is the difference between the bonus being a stretch goal and being planned work.

**One premise to correct, because it will otherwise appear in your report:** the assignment was *not* intended to be done in Python. §4.1 gives C, C++, and Python equal standing, and the launcher indirection (§6.0.2) exists precisely so that `experiment.py` never learns which you chose — the handout says so explicitly: *"the experiment script may invoke `./server/run-server 127.0.0.1 5000` without knowing whether the server is implemented in C or Python."* The harness being Python is an implementation detail of the harness.

What *is* true, and is the better argument: the handout anticipated Python submissions specifically enough to legislate for them. It names `select()`, `poll()`, and `kqueue()` as allowed, states that "the use of the standard `socket` module is permitted," and bans `asyncio` **by name**. You are on a well-marked path, not an improvised one. Say that in your report; don't say the assignment was meant for Python.

**What you gain and what you now owe.** The gain: ~8 hours, no build system, no Makefile, faster iteration. The debt: Python replaces C++'s memory-safety hazards with one different failure mode (§2.4) and a sharper input-validation trap (§3), and it puts you closer to the §4.1.2 line, so you must be deliberate about imports (§1.9).

**Bonus, honestly restated:** Python can hold 70,000 idle sockets. Idle connections cost file descriptors and kernel memory, not interpreter cycles, and the kernel's ~2 KB per connection dwarfs your per-connection Python objects. Expect ~30–50 MB of userspace against ~140 MB of kernel state at 70k. Your architecture still matters enormously (§2.3); your language barely does.

**Develop on your Mac, measure in the VM.** macOS is BSD-family and `select.kqueue()` works there natively. So Phases 1–3 can be written and iterated on your laptop with the real kqueue API, with no dependency on the FreeBSD VM being finished. Everything in §8 — `sockstat`, `procstat`, `netstat -x`, `wchan` semantics — is FreeBSD-only and must happen in the VM. This decoupling takes the VM off your critical path for two full days.

---

## §1. The harness contract — get this right on Day 0 or every experiment fails

1. **`wait_for_server()` RSTs you before any experiment begins.** `experiment.py:172-183`: the readiness probe connects, sets `SO_LINGER{1,0}`, and closes → **your server's first ever client is an abortive reset.** If `ConnectionResetError` escapes your event loop, all 8 experiments die at startup. Test this first.
2. **SIGPIPE is already handled — the exception is not.** Python sets `SIGPIPE` to `SIG_IGN` at interpreter startup, so C++'s single most catastrophic failure mode does not exist for you. In its place: writing to a dead peer raises `BrokenPipeError`, and **an uncaught exception anywhere in the loop body kills every connection at once.** See §2.4 — this is now your #1 rule.
3. **No idle timeouts. Ever.** Exp 1, 2, and 5 (clients 2 and 4) connect and send *nothing at all*; Exp 6's clients connect and immediately FIN/RST without a byte; Exp 4's client 1 sends a partial line and goes silent forever. A login or handshake timeout fails experiments a correct server passes.
4. **Untyped connections are legal and held open indefinitely.** Role is inferred from the first successful `LOGIN` (→ Trader) or `SUBSCRIBE` (→ Market-Data). Nothing on the wire declares it; there is no HELLO in the spec, so do not invent one.
5. **Launchers: `exec`, `-u`, CWD-independent, LF endings, `chmod +x`.**
   ```sh
   #!/bin/sh
   exec python3 -u "$(dirname "$0")/../src/server.py" "$@"
   ```
   - `exec` — the harness does `os.killpg(pid, SIGTERM)`; without `exec` you orphan the real server behind a live shell and only the wrapper dies.
   - **`-u`** — unbuffered stdio. Your trace output is your screenshot evidence; without `-u` it can sit in a buffer while you photograph an empty terminal.
   - `$(dirname "$0")` — the harness runs `./server/run-server` relative to the submission root; a bare `python3 src/server.py` breaks under any other CWD.
   - **You are editing on macOS and running on FreeBSD: one CRLF makes `#!/bin/sh` fail with "bad interpreter".** Verify with `file server/run-server` — must say "ASCII text", never "with CRLF line terminators".
6. **`SO_REUSEADDR` on the listening socket.** You will restart hundreds of times in 5 days; without it you lose minutes to `EADDRINUSE`.
7. **You cannot add argv.** The harness invokes exactly `./server/run-server 127.0.0.1 5000`. All instrumentation knobs travel by **environment variable** — `EXCH_TRACE`, `EXCH_SNDBUF`, `EXCH_WBUF_MAX` — which the launcher passes through for free. So: `EXCH_TRACE=/tmp/exp7.jsonl python3 experiment.py 7`.
8. **The harness does not pipe stdout/stderr** (`Popen` with no `stdout=`), so your server writes straight to the terminal. That is a gift: **stderr is your screenshot channel**, `$EXCH_TRACE` JSONL is your plotting channel.
9. **§4.1.2 compliance — the import whitelist.** Decide this now and state it in your README; it is free marks to get right and painful to lose.

   | | |
   |---|---|
   | **Allowed and used** | `socket`, `select` (calling `select.kqueue()` directly), `os`, `sys`, `time`, `re`, `collections`, `errno`, `json`, `threading` (threads are explicitly permitted) |
   | **Banned by name** | `asyncio` |
   | **Avoid — technically stdlib, but against the point** | **`selectors`** — it abstracts over kqueue/epoll/poll/select and hides *which* syscall you used. That is exactly what §4.1.2 targets, and it wrecks your viva answer: "which mechanism did you use?" cannot be answered with "whatever `selectors` picked." Use `select.kqueue()` explicitly. Also **`socketserver`** (`ThreadingTCPServer` is literally "connection management"), `http.server`, `multiprocessing.connection` |
   | **Third-party, obviously out** | `twisted`, `trio`, `gevent`, `tornado`, anything providing framing or event loops |

   State in the README: *"Concurrency/I-O: single-threaded event loop over `select.kqueue()`, called directly. No `selectors`, no `asyncio`, no third-party networking libraries."*
10. **`python3` availability is self-solving.** §7.5 forbids assuming installed software — but the evaluator runs `python3 experiment.py`, so `python3` is necessarily present. Still document `pkg install python3` and your minimum version (3.8+) in the README, and confirm inside the VM that `python3 --version` resolves (FreeBSD may only install `python3.11` without the `python3` symlink).

---

## §2. Architecture

### 2.1 Thread-per-connection (the alternative you will reject — and later measure)

You need this for report §8.1, and in Python it is cheap enough (~40 lines) to actually **build purely as a measuring instrument** for bonus questions 3–5.

```python
book_lock = threading.Lock()   # guards orders, level, next_id
reg_lock  = threading.Lock()   # guards sessions, usernames, subs
# LOCK ORDER: book_lock -> reg_lock.  NEVER the reverse.

class Session:
    __slots__ = ('fd','sock','sid','role','user','subs','outq','alive')
    def __init__(self, sock, sid):
        self.outq  = queue.Queue(maxsize=Q_MAX)   # bounded; mutex+condvar inside
        self.alive = True

def accept_loop(ls):                              # 1 thread total
    while True:
        cs, peer = ls.accept()                     # blocks — fine, dedicated thread
        s = Session(cs, next_sid())
        with reg_lock:                             # LOCK POINT A
            sessions[s.sid] = s
        threading.Thread(target=reader, args=(s,), daemon=True).start()
        threading.Thread(target=writer, args=(s,), daemon=True).start()
        #  ^^^ TWO threads per connection is the price of solving Exp 7 this way

def reader(s):                                     # 1 thread PER connection
    fr = Framer()
    try:
        while True:
            data = s.sock.recv(65536)              # BLOCKS — isolated to this thread
            if not data:                           # FIN
                break
            for line in fr.feed(data):
                cmd = parse(line)                  # pure, lock-free
                with book_lock:                    # *** LOCK POINT B ***
                    out = engine.handle(s.sid, cmd)  # ALL matching serialised here
                dispatch(out)                      # *** MUST be outside book_lock ***
    except (ConnectionResetError, OSError):
        pass
    finally:
        teardown(s)

def dispatch(out):
    for sid, msg in out:
        with reg_lock:                             # LOCK POINT C
            t = sessions.get(sid)
        if t is None or not t.alive:
            continue                               # §2.6: drop the notification
        try:
            t.outq.put_nowait(msg)                 # *** NEVER blocking put() ***
        except queue.Full:
            slow_consumer_policy(t)

def writer(s):                                     # 2nd thread PER connection
    while True:
        msg = s.outq.get()                         # blocks on condvar only
        if msg is SENTINEL:
            break
        while True:                                # coalesce → fewer send() calls
            try: msg += s.outq.get_nowait()
            except queue.Empty: break
        s.sock.sendall(msg)                        # BLOCKS ON TCP — Exp 7 lands HERE,
                                                   # harmlessly, on a dedicated thread

def teardown(s):
    with reg_lock:                                 # LOCK POINT D
        s.alive = False
        sessions.pop(s.sid, None)
        usernames.discard(s.user)
        for i in s.subs: subs[i].discard(s.sid)
    s.outq.put(SENTINEL)                           # wake the writer so it can exit
    s.sock.close()
    # orders / level DELIBERATELY UNTOUCHED -> resting orders survive (§2.6)
```

**The fatal variant to name in your report:** if `dispatch()` ran *inside* `book_lock`, a blocking `outq.put()` to a slow subscriber would stall the matching engine **while holding the global book lock** — every trader on every instrument freezes. Exp 7 escalates from "one slow client" to "total server deadlock." That is why `put_nowait` and dispatch-outside-the-lock are not stylistic choices.

**And the Python-specific kicker:** the GIL means `book_lock` contention buys you **no CPU parallelism whatsoever**. You pay for two threads per connection, a condvar wake per message, and GIL handoffs every `sys.setswitchinterval()` (5 ms default) — and get zero throughput in return. In C++ this design at least *could* use multiple cores. In Python it is pure overhead.

### 2.2 Single-threaded kqueue event loop (what you will build)

```python
#!/usr/bin/env python3
import socket, select, os, sys, time

UNTYPED, TRADER, MARKETDATA = 0, 1, 2
MAX_LINE  = 4096
WBUF_MAX  = int(os.environ.get('EXCH_WBUF_MAX', 4 << 20))   # 4 MiB

class Conn:
    __slots__ = ('sock','fd','sid','role','user','subs','rbuf','wbuf','woff',
                 'wwatch','dead','n_recv','bytes_in','queued','sent',
                 'eagain','wbuf_hwm')
    # __slots__ is not micro-optimisation here: it is what makes 70k connections
    # cost ~250 B each instead of ~500 B. It is a bonus-track requirement.
    def __init__(self, sock, sid):
        self.sock, self.fd, self.sid = sock, sock.fileno(), sid
        self.role, self.user, self.subs = UNTYPED, None, 0   # subs = bitmask JNST=1 IMCT=2
        self.rbuf, self.wbuf, self.woff = bytearray(), bytearray(), 0
        self.wwatch = self.dead = False
        self.n_recv = self.bytes_in = self.queued = 0
        self.sent = self.eagain = self.wbuf_hwm = 0

conns, by_sid, reap = {}, {}, []      # fd->Conn, sid->Conn, fds to close post-batch

def kq_set(kq, fd, filt, flags):
    kq.control([select.kevent(fd, filt, flags)], 0)

def want_write(kq, c, on):
    if on == c.wwatch: return
    kq_set(kq, c.fd, select.KQ_FILTER_WRITE,
           (select.KQ_EV_ADD | select.KQ_EV_ENABLE) if on else select.KQ_EV_DISABLE)
    c.wwatch = on
    trace(c, 'write_enable' if on else 'write_disable', pending=len(c.wbuf) - c.woff)

def main():
    host, port = sys.argv[1], int(sys.argv[2])
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((host, port))
    ls.listen(4096)                    # big backlog for the bonus ramp
    ls.setblocking(False)
    lfd = ls.fileno()

    kq = select.kqueue()
    kq_set(kq, lfd, select.KQ_FILTER_READ, select.KQ_EV_ADD | select.KQ_EV_ENABLE)

    while True:
        try:
            events = kq.control(None, 256, None)   # None timeout = block forever
        except InterruptedError:                   # EINTR
            continue
        # ^ blocked in kevent() -> `ps -o wchan` reports kqread, NEVER sbwait.
        #   That one word is your entire Experiment 4 proof.

        for ev in events:
            fd = ev.ident
            if fd == lfd:
                on_accept(kq, ls); continue
            c = conns.get(fd)
            if c is None or c.dead:
                continue                            # RULE 1 guard
            try:
                if ev.filter == select.KQ_FILTER_READ:
                    on_readable(kq, c, ev)
                if not c.dead and ev.filter == select.KQ_FILTER_WRITE:
                    flush(kq, c)
            except (ConnectionResetError, BrokenPipeError) as e:
                kill(c, type(e).__name__)
            except OSError as e:
                kill(c, 'errno=%d' % (e.errno or 0))
            except Exception as e:                  # *** RULE: never kill the loop ***
                trace_bug(c, e); kill(c, 'internal_error')

        for fd in reap:                             # ::close() ONLY here (RULE 1)
            destroy(kq, fd)
        reap.clear()

def on_accept(kq, ls):
    while True:                                     # drain the whole backlog
        try:
            cs, peer = ls.accept()
        except (BlockingIOError, OSError):
            return
        cs.setblocking(False)
        cs.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # documented choice
        sb = os.environ.get('EXCH_SNDBUF')          # Exp 7 experimental control
        if sb:
            cs.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, int(sb))
        c = Conn(cs, next_sid())
        conns[c.fd] = c; by_sid[c.sid] = c
        kq_set(kq, c.fd, select.KQ_FILTER_READ, select.KQ_EV_ADD | select.KQ_EV_ENABLE)
        trace(c, 'accept', peer=peer, nconn=len(conns))   # WRITE filter added lazily

def on_readable(kq, c, ev):
    while True:
        try:
            data = c.sock.recv(65536)
        except BlockingIOError:
            return
        except ConnectionResetError:
            trace(c, 'eof_rst', errno=54, fflags=ev.fflags)     # Exp 6B / probe
            kill(c, 'RST'); return
        if not data:
            trace(c, 'eof_fin', ev_eof=bool(ev.flags & select.KQ_EV_EOF))  # Exp 2/6A/8
            kill(c, 'FIN'); return
        c.n_recv += 1; c.bytes_in += len(data)
        trace_recv(c, data)                          # <-- Exp 3 evidence, one line per recv
        c.rbuf += data
        drain_lines(kq, c)
        if c.dead: return
        if len(c.rbuf) > MAX_LINE:                   # buffer-overflow guard (§3)
            queue_out(kq, c.sid, b'ERROR line_too_long\n')
            kill(c, 'overflow'); return

def drain_lines(kq, c):
    rbuf, start = c.rbuf, 0
    while True:
        nl = rbuf.find(b'\n', start)
        if nl < 0:
            break                                    # <-- partial: residual stays in rbuf
        line = bytes(rbuf[start:nl])
        if line.endswith(b'\r'):
            line = line[:-1]
        handle_line(kq, c, line)                     # parse -> engine -> queue_out
        start = nl + 1
        if c.dead: break
    if start:
        del rbuf[:start]                             # ONE memmove per recv, not per message

# ---- the write path: this is the whole of Experiment 7 -----------------------
def queue_out(kq, sid, msg):                         # msg is bytes
    c = by_sid.get(sid)
    if c is None or c.dead:
        trace_notify_dropped(sid, msg); return       # §2.6 order survival
    c.wbuf += msg
    c.queued += len(msg)
    pend = len(c.wbuf) - c.woff
    if pend > c.wbuf_hwm: c.wbuf_hwm = pend
    trace(c, 'queue_out', n=len(msg), pending=pend, hwm=c.wbuf_hwm)
    if pend > WBUF_MAX:
        kill(c, 'slow_consumer'); return
    flush(kq, c)                                     # opportunistic; usually completes

def flush(kq, c):
    while c.woff < len(c.wbuf):
        try:
            n = c.sock.send(memoryview(c.wbuf)[c.woff:])   # memoryview = zero-copy slice
        except BlockingIOError:
            c.eagain += 1
            trace(c, 'send_would_block',
                  pending=len(c.wbuf) - c.woff, eagain=c.eagain)   # *** Exp 7 money event
            if c.woff * 2 > len(c.wbuf):                            # amortised compaction
                del c.wbuf[:c.woff]; c.woff = 0
            want_write(kq, c, True)
            return                    # <-- engine path returns IMMEDIATELY. Never blocks.
        except (BrokenPipeError, ConnectionResetError):
            kill(c, 'peer_gone'); return                           # Exp 8
        if n < len(c.wbuf) - c.woff:
            trace(c, 'partial_write', n=n)
        c.woff += n; c.sent += n
    del c.wbuf[:]; c.woff = 0
    want_write(kq, c, False)
    # *** MUST disable. KQ_FILTER_WRITE is LEVEL-TRIGGERED: leave it enabled on an
    #     empty buffer and you spin at 100% CPU — visible and damning in Exp 7. ***
```

**Two Python details that matter.** `memoryview(c.wbuf)[c.woff:]` avoids copying the pending tail on every send — during Exp 7 that tail reaches ~72 KB, and `c.sock.send(c.wbuf[c.woff:])` would copy it on every attempt, making your instrumentation O(n²) and perturbing the very measurement you are taking. And `del rbuf[:start]` once per `recv` rather than per message keeps framing linear.

**The one-sentence viva answer this design buys you:** *"The matching engine cannot block on a slow reader because the engine has no access to a socket — `engine.handle()` returns a list of `(sid, message)` pairs, and the only function in the program that calls `send()` is `flush()`."*

Enforce it as a checkable invariant, and put the command in your README:
```sh
grep -ln 'import socket\|\.send(\|\.recv(' src/*.py   # must print ONLY server.py (+ clients)
```

### 2.3 Selection criteria table (paste into report §8.1)

| Criterion | Thread-per-connection (2 threads/conn) | kqueue event loop (1 thread) |
|---|---|---|
| Threads at 10 clients | 21 | 1 |
| **Threads at 70,000 (bonus)** | **140,001 — exceeds `kern.threads.max_threads_per_proc` (expect 1500; verify). A thread *count* ceiling: no stack tuning moves it. Structurally impossible.** | 1 thread, 70k fds. Bounded by `kern.maxfilesperproc` + mbufs |
| **CPU parallelism gained (Python)** | **None — the GIL serialises bytecode.** All cost, no benefit | N/A, nothing to parallelise |
| Context switches per delivered message | ≥2 (reader → condvar → writer wake) **+ GIL handoff every 5 ms** (`sys.setswitchinterval`) | **0** — one thread frames, matches, and sends |
| Syscalls per delivered message | `recv` + futex + `send` ≥ 3 | amortised <1 `kevent` (batched over ≤256 ready fds) + 1 `send` |
| Readiness-check scaling | O(1)/thread but O(N) threads | **O(ready), not O(registered)** — unlike `poll()`'s O(N) and `select()`'s hard `FD_SETSIZE`=1024 wall |
| **Lock contention** | 2 locks; `book_lock` serialises *all* matching → degenerates to single-threaded **with lock overhead added**. Deadlock if the order is violated or a blocking `put` happens under `book_lock` | **None. No lock exists.** Data races structurally impossible |
| **Exp 7 decoupling machinery** | bounded `queue.Queue` + condvar + writer thread + sentinel shutdown + `put_nowait` discipline ≈ **70 LOC, 4 failure modes** | one `bytearray` + lazy `KQ_FILTER_WRITE` toggle ≈ **20 LOC, 1 failure mode** (forgetting to disable) |
| Exp 4 (idle client) | Passes — blocking `recv` isolated per thread | Passes — an idle fd never becomes ready |
| **Exp 4 evidence quality** | `wchan` shows N threads in `sbwait`; you must *argue* that's benign | `wchan` shows `kqread`, zero `sbwait`. **One-word proof** |
| Blast radius of one bad client | 1 conn — unless a lock is held across a blocking call | 1 conn — **unless an exception escapes the loop, then all.** §2.4 |
| Partial-write bookkeeping | free (`sendall` loops) | must implement (`wbuf` + `woff`) |
| Server-core LOC (Python) | ~300 | ~260 |

**Decision: single-threaded kqueue.** The bonus row alone is decisive, and Python adds a second independent reason — the GIL means the threaded design cannot even convert its overhead into throughput. Everything else compounds: no locks means no races to debug solo in 5 days, and the design answers the assignment's central question (Exp 7) with a `bytearray` instead of a subsystem.

**Where threads would genuinely win, and why they don't here:** a CPU-bound matching engine across multiple cores with per-instrument locks. This book has **two instruments and exact-price dict lookups** — there is nothing to parallelise, and in CPython there would be no parallelism available anyway. Say this; it shows you rejected threads on evidence.

### 2.4 The Python discipline rules

**RULE 1 (unchanged from C++, different reason) — never close a socket inside an event batch.** Python's GC removes the use-after-free hazard, but the **fd-identity hazard remains**: `kevent()` returns up to 256 events; if you `close()` fd 12 mid-batch, the kernel may immediately reassign fd 12 to a new `accept()` and a stale event for the old fd 12 lands on the new connection. Fix: `c.dead = True` + `reap.append(fd)`, guard every handler with `if c is None or c.dead: continue`, and `close()` only in the post-batch reap loop. Deferring the close is what makes fd reuse impossible mid-batch.

**RULE 2 (softened) — price levels store order *IDs*, not `Order` objects.** In Python this no longer prevents undefined behaviour, but it is still the right design: one owning `orders` dict, `deque` of ids per price level, and `CANCEL` becomes O(1) by tombstone instead of an O(n) deque scan.

**RULE 3 (replaces the SIGPIPE rule) — no exception may escape the event loop.** This is now your single most dangerous failure mode, and it is easier to hit than C++'s SIGPIPE because *any* bug — a `KeyError` in the engine, a `ValueError` in the parser, an `AttributeError` on a half-initialised `Conn` — takes down all clients at once and fails §4.4 catastrophically. The layered `except` in §2.2 is mandatory: specific socket errors kill one connection quietly; a bare `except Exception` logs the bug and kills that one connection while the loop survives. Add a top-level guard around `main()` too, so a crash still flushes your trace file.

---

## §3. Phase 1 — Protocol wire isolation (Day 1)

**Goal:** prove Exp 3 compliance with zero sockets involved, so that when you write the event loop you debug one thing instead of two. All of this runs on your Mac (or anywhere) with no VM.

**Module layout, with a grep-checkable invariant:**

```
src/framing.py      <- no socket import
src/protocol.py     <- no socket import
src/engine.py       <- no socket import   *** the invariant that wins Exp 7 ***
src/server.py       <- the ONLY server file that imports socket
src/trader.py       <- reuses framing.py
src/market_data.py  <- reuses framing.py
src/tools/plot.py   <- dev-only; not part of the graded server
tests/test_all.py
```

**Work in `bytes` end-to-end. Never decode to `str` in the protocol path.** This single decision eliminates an entire class of Python validation bug (see below), and it costs nothing — `bytes` supports `%` formatting (`b'ORDER_ACCEPTED %d\n' % oid`), `.find()`, `.split()`, and `.startswith()`.

**Framer.**

```python
class Framer:
    __slots__ = ('buf', 'max')
    def __init__(self, max_line=4096):
        self.buf, self.max = bytearray(), max_line
    def feed(self, data):
        """Yield complete lines (bytes, no trailing \\n). Keeps the partial tail."""
        self.buf += data
        start = 0
        while True:
            nl = self.buf.find(b'\n', start)
            if nl < 0:
                break
            line = bytes(self.buf[start:nl])
            yield line[:-1] if line.endswith(b'\r') else line
            start = nl + 1
        if start:
            del self.buf[:start]
    def overflowed(self):
        return len(self.buf) > self.max
```

The three behaviours it must exhibit are exactly §2.9's three bullets: one message across many `feed()` calls (emits nothing until `\n`); many messages in one `feed()` (loops on `find`); split at an arbitrary byte (byte-level `find`, no token or character assumptions).

**`max_line` is the hidden buffer-overflow constraint.** Nothing in the handout says "bound your read buffer," but Exp 4 parks 20 bytes with no newline and stops; a client sending 100 MB with no `\n` would grow `rbuf` without limit. Cap it, reply `ERROR line_too_long`, kill that one connection. Unprompted robustness like this reads as engineering maturity in a report.

**Parser — and the Python trap that will silently corrupt your order book.**

`int()` is dangerously permissive. All of these succeed when you expect rejection:

| Input | `int()` gives | Why it's a bug |
|---|---|---|
| `int(" 5 ")` | 5 | whitespace stripped |
| `int("+5")` | 5 | sign accepted |
| `int("1_000")` | 1000 | **PEP 515 underscores** |
| `int("٥")` | 5 | Arabic-Indic digit |
| `int("५")` | 5 | Devanagari digit |
| `"٥".isdigit()` | `True` | **so `isdigit()` is not a safe guard either** |
| `"²".isdigit()` | `True` | superscripts count as digits |
| `"٥".isdecimal()` | `True` | `isdecimal()` isn't safe either |

Staying in `bytes` removes the unicode half of this outright (a `bytes` object cannot contain Devanagari), but underscores and signs still slip through `int()`. So validate explicitly, once, and use it everywhere:

```python
import re
_U32 = re.compile(rb'[0-9]+')          # bytes pattern, anchored by fullmatch

def parse_u32(tok, lo, hi):
    """Strictly /^[0-9]+$/ in [lo,hi]. Rejects +5 -5 5.0 5e3 ' 5' 0x10 1_000 ''."""
    if not _U32.fullmatch(tok):
        return None
    v = int(tok)
    return v if lo <= v <= hi else None
```

Range gates from §2.1: quantity and price ∈ **[1, 2147483647]**; order_id ∈ **[0, 2147483647]**. A grader will try exactly `0`, `-1`, `2147483648`, and `5.0` — reject all four with `ERROR <reason>` and **never let them reach the book.**

**Error vocabulary.** `<reason>` is free-form per spec, so define one and document the table in your README: `unknown_command`, `bad_arity`, `bad_instrument`, `bad_quantity`, `bad_price`, `bad_order_id`, `not_logged_in`, `duplicate_username`, `wrong_role`, `unknown_order`, `order_not_cancellable`, `not_owner`, `line_too_long`.

Be liberal inbound, strict outbound (Postel's principle — worth naming): use `line.split()` so runs of whitespace collapse on the way in, but emit exactly one space and exactly one trailing `\n` on the way out.

**Decisions to make now and write down** (the spec is silent; vivas probe silence):
- `BUY` before `LOGIN` → `ERROR not_logged_in` (recommended).
- Verb case → accept exact uppercase only, matching the spec literally.
- Duplicate `SUBSCRIBE` / `UNSUBSCRIBE` when not subscribed → idempotent `OK`.
- Self-matching (alice's BUY crosses her own SELL) → **allowed**; the spec has no self-trade prevention, so she receives both `BOUGHT` and `SOLD`. Note that real exchanges add STP.

**Tests (`pytest`, no sockets) — your Exp 3 proof independent of the harness:**

| Test | Method | Report value |
|---|---|---|
| `T-FRAG-ALL` | K messages as one byte stream; split at **every one of the N−1 positions**, feed as 2 chunks, assert identical output | "N−1/N−1 split positions parse identically" |
| `T-FRAG-RAND` | 1,000 random multi-way splits (1–8 chunks) | "1000/1000 random fragmentations identical" |
| `T-COALESCE` | all K messages in a single `feed()` | proves the reverse direction of §2.9 |
| `T-BOUNDARY` | split immediately before `\n`, immediately after, and `\n` alone | the Exp 3 case is literally this |
| `T-OVERFLOW` | 8 KB with no `\n` → flags at cap, buffer bounded | proves the overflow guard |
| `T-VALID` | ~60 table-driven `(line → expected reply)` rows, including every trap above | proves §2.1 range gates |
| `T-ENGINE` | handout's BUY 100/SELL 60; multi-fill sweep; cancel partial; cancel filled; cancel another trader's; **disconnect-then-match** | proves §2.6 |

**Day 1 gate:** all tests green, and `grep -ln 'import socket' src/*.py` prints only `server.py`, `trader.py`, `market_data.py`.

---

## §4. Phase 2 — Order book layout and order survival (Day 1–2)

### Memory layout

Matching is **exact-price only** (§2.6: "exactly the same price"). This is the biggest simplification in the assignment and it changes the data structure: a real limit order book needs a *sorted* map because it matches on price crossing (bid ≥ ask) and must locate the best bid/ask. Yours matches on price *equality*, so a dict keyed by price is O(1) — no heap, no tree, no skip list. Being able to state that contrast is strong viva material, because it shows you know what you simplified.

```python
from collections import deque

JNST, IMCT = 0, 1
BUY,  SELL = 0, 1

class Order:
    __slots__ = ('id','owner_sid','owner_user','instr','side','price',
                 'qty_left','cancelled','t_ns')

orders    = {}                      # oid -> Order            (sole owner)
level     = {}                      # (instr, side, price) -> deque[oid]
subs      = {JNST: set(), IMCT: set()}   # instr -> set[sid]
usernames = set()                   # currently-connected traders only
next_id   = 0                       # unique for the server's lifetime
```

Tuple keys are clearer than bit-packing in Python and hash just as well. Complexity: insert O(1), exact-price lookup O(1), fill from the front O(1), **cancel O(1) by tombstone**. Physically removing a cancelled order from the middle of a `deque` is O(n); instead set `cancelled = True; qty_left = 0` and discard the id when the level is next swept. In the viva: "lazy deletion with tombstones reaped on sweep." FIFO within a price level gives time priority — not required, but the only explainable choice, and it makes tests deterministic.

### Matching, in the order the spec demands

```python
def on_buy(sid, instr, qty, price, out):
    global next_id
    oid = next_id; next_id += 1
    out.append((sid, b'ORDER_ACCEPTED %d\n' % oid))     # §2.6: FIRST, before matching
    o = Order(); o.id, o.owner_sid, o.qty_left = oid, sid, qty
    ...
    orders[oid] = o
    opp = level.get((instr, SELL, price))                # O(1): only exact price matches
    while o.qty_left and opp:
        rid = opp[0]
        r = orders.get(rid)
        if r is None or r.cancelled or r.qty_left == 0:
            opp.popleft(); continue                      # reap tombstone
        q = min(o.qty_left, r.qty_left)
        o.qty_left -= q; r.qty_left -= q
        out.append((sid,         b'BOUGHT %s %d %d\n' % (NAME[instr], q, price)))  # may DROP
        out.append((r.owner_sid, b'SOLD %s %d %d\n'   % (NAME[instr], q, price)))  # may DROP
        for sid2 in subs[instr]:
            out.append((sid2,    b'TRADE %s %d %d\n'  % (NAME[instr], q, price)))  # ALWAYS
        if r.qty_left == 0:
            opp.popleft()
    if o.qty_left:
        level.setdefault((instr, BUY, price), deque()).append(oid)   # rest the remainder
```

One notification pair **per fill**, not aggregated: the handout's own example (BUY 100 vs SELL 60 → "a trade of 60") treats each matched pair as one trade, so a BUY 100 sweeping SELL 60 + SELL 40 emits two `TRADE`s and two `BOUGHT`s. Keep fully-filled orders in `orders` with `qty_left = 0` so `CANCEL` can distinguish `unknown_order` from `order_not_cancellable`.

### How orders outlive TCP drops — the mechanism, not the intention

The whole requirement reduces to one type decision: **`Order.owner_sid` is an `int`, not a `Conn`.** Everything else follows.

```python
def kill(c, why):
    c.dead = True
    surviving = sum(1 for o in orders.values()
                    if o.owner_sid == c.sid and o.qty_left > 0 and not o.cancelled)
    trace(c, 'close', why=why, orders_surviving=surviving, subs_removed=bin(c.subs).count('1'))
    for i in (JNST, IMCT):
        if c.subs & (1 << i): subs[i].discard(c.sid)
    if c.role == TRADER: usernames.discard(c.user)
    by_sid.pop(c.sid, None)          # notifications to this sid now DROP silently
    reap.append(c.fd)                # close() after the batch (RULE 1)
    # `orders` and `level` ARE NOT TOUCHED.
```

`queue_out()` then looks up `by_sid`, finds nothing, and calls `trace_notify_dropped()`. The trade still happened; the `TRADE` still reached subscribers. That is §2.6's final paragraph implemented **by omission rather than by special-case code**, which is exactly why it's worth pointing at in the viva. The `orders_surviving` field in the close trace is your greppable proof.

**The subtle bug the sid design prevents:** usernames are unique only among *currently connected* traders, so after alice disconnects, a different person may log in as `alice`. If orders referenced the *username*, the new alice would receive the old alice's `BOUGHT` notifications. Because `sid` is monotonic and never reused, that is impossible. Consequence to document: after a disconnect, that trader's resting orders can no longer be cancelled by anyone — ownership is bound to a session that no longer exists, so they live until matched. Consistent with "closing the connection does not cancel orders," and a good answer to have ready.

**Debug invariants** behind `EXCH_SELFCHECK=1`, on in tests and off in graded runs: every id in every `level` deque exists in `orders` or is a reapable tombstone; no live order is missing from its level; `Σ qty_left` per level matches an independently tracked total.

---

## §5. Phase 3 — Decoupled backpressure (Day 2–3)

### The pattern

Formal name: **per-connection userspace output buffer with lazily-registered writability notification** — what nginx, Redis, and HAProxy do; in kqueue idiom, "buffer + `KQ_FILTER_WRITE` toggle."

Three properties, each mapping to a requirement:

1. **The engine's only outbound operation is `wbuf += msg`** — an O(1) memcpy that cannot block, cannot fail, and cannot raise `BlockingIOError`. TCP backpressure therefore has no path to the matching engine. (Exp 7)
2. **`KQ_FILTER_WRITE` is enabled only while `wbuf` is non-empty.** Level-triggered filters fire continuously on a writable idle socket; forgetting `want_write(kq, c, False)` is a 100%-CPU spin that will show up in Exp 7's CPU column as an obvious defect. This is the design's one failure mode — know it for the viva.
3. **Bounded by policy, with the policy stated.** The spec is silent on permanently slow consumers, so choose and document:
   - *unbounded* — memory grows without limit; one stalled subscriber is a DoS. Reject.
   - **evict at a cap (recommended)** — `WBUF_MAX = 4 MiB`, then disconnect with reason `slow_consumer`. This is what real market-data feeds do ("slow consumer disconnect"). 4 MiB sits comfortably above anything Exp 7 generates, so Exp 7 observes pure buffering with no eviction — which is what you want to measure.
   - *conflate* — drop or coalesce stale updates. Market-data-native, but wrong here: each `TRADE` is a distinct event, not a snapshot.

**Why not a ring buffer:** at Exp 7's volumes (~85 KB total, ~32 msg/s) a `bytearray` + `memoryview` offset is simpler, allocation-free per send, and already O(1) amortised. Ring buffers earn their complexity at millions of messages per second. If you want the sophisticated variant for the report, cite `socket.sendmsg()` with a list of buffers over a `deque[bytes]` — scatter-gather, one syscall for many queued messages, no concatenation. FreeBSD and Python both support it. Don't build it; name it as the next step.

**Fairness guard:** cap bytes flushed per connection per loop iteration (e.g. 1 MiB) so one client's backlog cannot monopolise the loop. Otherwise a single 4 MiB drain delays every other connection — a head-of-line problem the event loop reintroduces if you're careless.

### Sizing Exp 7 before you run it — do this arithmetic, it changes your setup

**This is the most important paragraph in this document.**

**Feed volume:** `b"TRADE JNST 1 238\n"` = **17 bytes**. `TRADE_COUNT = 5000` matched pairs → 5000 trades → **85,000 bytes** to each subscribed Market-Data client.

**Buffer capacity on the server→slow-client path:**
```sh
sysctl net.inet.tcp.sendspace net.inet.tcp.recvspace \
       net.inet.tcp.sendbuf_auto net.inet.tcp.recvbuf_auto \
       net.inet.tcp.sendbuf_max  net.inet.tcp.recvbuf_max
```
Expect roughly `sendspace = 32768`, `recvspace = 65536` on FreeBSD 14 — **confirm on your VM; do not take my word for it.** That is ~96 KB of combined kernel buffering against 85 KB of feed.

**Conclusion: at stock settings the entire feed fits in the socket buffers and `send()` may never raise `BlockingIOError`. Experiment 7 can silently produce no backpressure at all.** Many students will screenshot "nothing happened" and write the wrong answer.

**The harness is also slow.** Per trade, `submit_buy_sell_pair` calls `drain_socket` twice (each ending in a 0.01 s timeout), the caller drains `normal_md` (another 0.01 s), plus `TRADE_INTERVAL` 0.001 s → **≈0.031 s/trade ≈ 32 trades/s**, so Exp 7 runs **~2.5–3 minutes** and the slow client accrues only ~550 B/s. It is not hung. Don't Ctrl-C it.

**Experimental control — run Exp 7 twice.**

*Run A, stock settings.* Report what you observe, including possibly no `BlockingIOError` at all. That is a legitimate finding: it quantifies socket-buffer capacity.

*Run B, buffers reduced so the onset lands inside the observation window.* Pure OS tuning, no code change — the cleanest kind of control:
```sh
sysctl net.inet.tcp.recvbuf_auto=0 net.inet.tcp.sendbuf_auto=0
sysctl net.inet.tcp.recvspace=8192 net.inet.tcp.sendspace=4096
# capacity ~12 KB ~= 723 messages ~= 23 s in; then ~72 KB accumulates in YOUR wbuf
```
Belt-and-braces if you lack privileges mid-run: `EXCH_SNDBUF=4096 python3 experiment.py 7`.

Reporting both runs with the arithmetic **predicted in advance** — "we computed 85 KB of feed against 96 KB of buffering, predicted marginal blocking, then reduced the buffers to 12 KB to bring the onset inside the window and confirmed the predicted behaviour" — is worth substantially more than one run of either.

---

## §6. Chronological milestones with hard gates

Python's ~8-hour saving is banked into Wednesday and Thursday, which is what makes the bonus real. Each gate is go/no-go; if a gate slips, cut from the Optional column, never from the gate. **Phases 1–3 need only your Mac** (`select.kqueue()` works on macOS); the VM is required only for §8's evidence.

| Day | Focus | Exit gate (must pass to proceed) | Cuttable |
|---|---|---|---|
| **Sat 5 (eve)** | Directory skeleton, launchers, stub server that binds/listens/accepts/holds. FreeBSD VM install in parallel. `sysctl` baseline → `baseline.txt` | Stub survives the `SO_LINGER` RST probe and holds an idle connection. `file server/run-server` says ASCII (no CRLF) | VM can slip to Sunday |
| **Sun 6** | Phases 1 + 2 offline on the Mac: framer, parser, engine, book, full test suite | §3's tests all green. `grep -ln 'import socket' src/*.py` → only server/clients | `T-FRAG-RAND` → 200 cases |
| **Mon 7** | Phase 3: kqueue loop, `Conn` table, `queue_out`/`flush`, accept, teardown, trace to stderr + JSONL. Both clients | Exps **1–5** behave correctly in the VM. Exp 4 elapsed **< 5 ms**. `ps -o wchan` → `kqread`. Exp 2 shows `CLOSE_WAIT` **transient, not permanent** | Exp 5 (optional per handout) |
| **Tue 8** | Robustness: FIN/RST/EPIPE/ECONNRESET, role enforcement, validation, disconnect cleanup. Trader client stdin+socket multiplexing | Exps **6, 8** clean. 10-client soak (2 T + 4 MD + 4 idle) 10 min, no fd or memory growth. **Supplementary test: trader with resting order disconnects → later match delivers `TRADE`, drops `BOUGHT`** | — |
| **Wed 9** | Exp 7 runs A and B; all `tcpdump`/`netstat`/`sockstat` captures; plots P1–P3. **Upload a complete, bonus-free zip tonight as insurance** | Every artifact in §9 exists as a file. Your own structure check passes | Plot P3 |
| **Thu 10** | **Bonus (§10) — now planned work, not a stretch.** Then report prose, README, implementation-decisions section. Replace the zip by 18:00 | Submitted with ≥5 h buffer | Bonus, if anything slipped |

**Risk management, stated plainly:** a zip failing the automated structure check costs 10% and buys one correction; late costs 20%/day. Upload a complete working submission **Wednesday night**, then replace it Thursday if the bonus lands. Never let 10% endanger 80%.

**Client design note that catches people out:** a Trader Client must display an asynchronous `BOUGHT`/`SOLD` arriving while the user is mid-typing. A blocking read-a-line-then-read-a-reply client cannot, and violates §2.3/§2.7. Multiplex `{sys.stdin, sock}` with a small kqueue (~30 lines in Python) and **reuse `framing.py`** — the server may coalesce `ORDER_ACCEPTED` and `BOUGHT` into one segment, so the client needs the same framer. That is the payoff for isolating it in Phase 1.

Launcher argument shapes, per §6.0.2's examples: `run-trader <host> <port> <username>` auto-sends `LOGIN <username>` on connect; `run-market-data <host> <port> <instrument>` auto-sends `SUBSCRIBE <instrument>` (accept one or more instruments, defensively).

---

## §7. Instrumentation spec

Two channels from one `trace()` call: **stderr** (human-readable, one line per event — your screenshots) and **`$EXCH_TRACE` JSONL** (your plots). Use `time.monotonic_ns()`. Buffer writes; never `flush()` per event in the hot path or your instrumentation becomes the bottleneck you're measuring. `python3 -u` in the launcher keeps stderr live for screenshots without per-event flushing of the JSONL file.

Common fields: `t_mono_ns, ev, sid, fd`.

| Event | Payload | Serves |
|---|---|---|
| `accept` | `peer, nconn` | 1, 5 |
| `recv` | `n, rbuf_before, rbuf_after, lines_out, hex[:32]` | **3**, 4 |
| `frame_partial` | `rbuf_len, has_nl=false` | **3, 4** |
| `cmd` | `verb, ok, err_reason` | all |
| `engine` | `verb, n_out, engine_ns` | **7** (flat ⇒ engine never blocked) |
| `queue_out` | `target_sid, n, pending, hwm` | **7** |
| `partial_write` | `n` | 7 |
| `send_would_block` | `pending, eagain` | **7 — the money event** |
| `write_enable` / `write_disable` | `pending` | 7 |
| `eof_fin` | `ev_eof` | **2, 6A, 8** |
| `eof_rst` | `errno=54, fflags` | **6B, 8** |
| `notify_dropped` | `target_sid, msg` | **§2.6 proof** |
| `close` | `why, orders_surviving, subs_removed` | **§2.6 proof**, 2, 6, 8 |
| `kq_batch` | `n_events, ready_fds[], nregistered` | **5** |

FreeBSD errno values you will screenshot: **EAGAIN/EWOULDBLOCK 35, EPIPE 32, ECONNRESET 54, ETIMEDOUT 60**. Tabulate them in the report so `errno=54` in a log screenshot is readable.

---

## §8. Experiment-by-experiment checklist

Standing capture before each run:
```sh
tcpdump -i lo0 -n -s0 -w exp<N>.pcap 'tcp port 5000' &
tcpdump -r exp<N>.pcap -n -ttt -S       # read back
```
FreeBSD `lo0` MTU is **16384**, so loopback segments can be ~16 KB — do not expect 1460 bytes, and say so when interpreting captures.

### 8.1 Listening vs connected sockets
- **Log:** listening fd and accepted fd distinctly, with both 4-tuples.
- **OS:** `sockstat -4 | grep python` · `procstat -f <pid>` · `netstat -an -p tcp | grep 5000`
- **Answer:** the listening socket has a wildcard foreign address (`*:*`) in state `LISTEN` — a *factory* that never carries data. The accepted socket is a distinct fd with a fully-specified 4-tuple in `ESTABLISHED`. Both share the same local port; the **4-tuple, not the port**, identifies a connection.
- **Artifact: T1** — fd / role / local / foreign / state.
- *Python note:* `sockstat` will show `python3.x` as the command, not `exchange_server`. Grep by PID from the harness output, and say so in the report caption so the grader isn't confused.

### 8.2 TCP connection states
- **Log:** `eof_fin` and `close` with timestamps.
- **OS:** `netstat -an -p tcp | grep 5000` at each phase (10 s idle, then 15 s post-close).
- **Answer:** `LISTEN` → handshake → `ESTABLISHED` both sides → client `close()` sends FIN → **server enters `CLOSE_WAIT`**, client `FIN_WAIT_2` → your server sees `recv()==b''` and closes → server `LAST_ACK` → `CLOSED`; the client, as active closer, sits in **`TIME_WAIT` for 2×MSL** (`sysctl net.inet.tcp.msl`, expect 30000 ms → ~60 s).
- **The defect graders look for:** a server that ignores EOF leaves `CLOSE_WAIT` **forever** and leaks an fd. Prove yours is transient — capture `netstat` at T+0.1 s and T+2 s and show it gone.
- **Artifact: T2** — time / event / client state / server state / cause.

### 8.3 TCP as a byte stream — *fragmentation proof #1*
- Harness (`experiment.py:481`) sends `b"LOGIN "`, `b"experiment"`, `b"_trader"`, `b"\n"` — **6, 10, 7, 1 bytes** — with 0.2 s gaps. Those gaps exceed loopback RTT by ~4 orders of magnitude, so Nagle cannot coalesce: you will genuinely get four `recv()` returns.
- **Log:** four `recv` events `n=6,10,7,1`, `rbuf_after=6,16,23,24`, `lines_out=0,0,0,1`; three `frame_partial` between.
- **OS:** `tcpdump -r exp3.pcap -n -ttt -S` → four PSH segments of 6/10/7/1.
- **Answer:** the message arrives in four pieces. Segment boundaries are a transport artefact of *when the sender wrote* and carry no application meaning — only the `\n` delimiter does.
- **Artifacts: F1** annotated tcpdump · **F2** stderr trace · **T3** the ledger below · **T4** offline framer pass counts (which generalise beyond this one lucky case).

| # | t_rel (ms) | bytes | payload | rbuf after | msgs emitted |
|---|---|---|---|---|---|
| 1 | 0 | 6 | `LOGIN ` | 6 | 0 |
| 2 | 200 | 10 | `experiment` | 16 | 0 |
| 3 | 400 | 7 | `_trader` | 23 | 0 |
| 4 | 600 | 1 | `\n` | 0 | **1** |

### 8.4 One client must not stall the others — *the architecture proof*
- Harness sends `"LOGIN blocked_client"` — **20 bytes, no `\n`** — then goes silent, waits 2 s, connects client 2, and times its `LOGIN`→`OK`.
- **Log:** client 1 → one `recv{n=20}` + `frame_partial{rbuf_len=20}`, then nothing forever. Client 2 → `accept`, `recv`, `cmd`, `queue_out`, `send`, full round trip in microseconds.
- **OS, in descending order of evidence quality:**
  1. `ps -o pid,tid,wchan,state -H -p <pid>` → **`kqread`**. A blocking-recv server shows **`sbwait`**. One word decides it.
  2. `truss -p <pid>` → parked in `kevent()`, not `recvfrom()`.
  3. `procstat -kk <pid>` → kernel stack.
  4. `netstat -an -p tcp` → **client 1's `Recv-Q` is 0.** Subtle and worth spelling out: the 20 bytes are not in the kernel, they are in *your* `rbuf`. You read them and are correctly waiting for the delimiter. `Recv-Q=0` **plus** `frame_partial{rbuf_len=20}` tells the whole story.
- **Build the control — 15 minutes in Python, highest-leverage work available.** A `server/run-server-naive` doing blocking `accept`→`recv`→reply on one thread:
  ```python
  ls = socket.socket(); ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  ls.bind((sys.argv[1], int(sys.argv[2]))); ls.listen(8)
  while True:
      cs, _ = ls.accept()
      fr = Framer()
      while True:
          d = cs.recv(4096)          # <-- blocks forever on the silent client
          if not d: break
          for line in fr.feed(d): cs.sendall(b'OK\n')
      cs.close()
  ```
  Run Exp 4 against it: `Elapsed: 5.000 s` (harness timeout) and `wchan=sbwait`, versus `0.0004 s` and `wchan=kqread`. A/B controls are the single highest-value thing you can do for a 40%-weighted investigation component.
- **Artifact: T5** — correct vs naive: elapsed, wchan, client 2 serviced?, client 1 `Recv-Q`.

### 8.5 Multiple clients and I/O multiplexing (optional)
- **Log:** `kq_batch{ready_fds[]}` on every wake. Only clients 1, 3, 5 appear.
- **OS:** `netstat -an` shows all five `ESTABLISHED`, `Recv-Q=0` for 2 and 4.
- **Answer:** *connected* ≠ *ready*. Five sockets exist; readiness is per-socket and event-driven, and `kevent()` returns only the ready subset. Cost is O(ready), not O(registered).
- **Artifact: T6** — time / ready fds / idle fds.

### 8.6 FIN vs RST
- Part A: `shutdown(SHUT_WR)` → FIN. Part B: `SO_LINGER{1,0}` + `close()` → RST.
- **Log:** `eof_fin{ev_eof}` vs `eof_rst{errno=54}`. **Precision available only with kqueue:** on `KQ_FILTER_READ`, FreeBSD sets `KQ_EV_EOF` in `ev.flags` and puts the **socket error in `ev.fflags`** — so you can distinguish FIN from RST *at the readiness-notification level, before calling `recv()`*. Log both; it's a genuinely sharp observation and Python exposes it directly as `ev.flags` / `ev.fflags`.
- **OS:** `tcpdump` → `Flags [F.]` vs `Flags [R]`/`[R.]`. `netstat -an` → `CLOSE_WAIT` then clean teardown (A) vs the entry **vanishing with no TIME_WAIT** (B).
- **Answer:** FIN is in-band and sequenced — "no more data from me"; data already sent still arrives; half-close is legal; the active closer pays 2×MSL of `TIME_WAIT`. RST is out-of-band and unsequenced — it aborts immediately, **discards queued data in both directions**, produces no `TIME_WAIT`, and surfaces as `ConnectionResetError` rather than EOF.
- **Nuance worth one sentence:** in Part A the client closed only its *write* side and could still receive for 10 s. Your server treats EOF as full teardown (document it); a half-close-aware server would drain `wbuf` first. Naming this shows you know why `shutdown()` exists separately from `close()`.
- **Artifact: T7** — aspect × {FIN, RST}: wire flag, `recv()` result, exception type, errno, `ev.flags`/`ev.fflags`, server state after, TIME_WAIT?, in-flight data preserved?

### 8.7 Backpressure and the slow receiver — *the centrepiece*
Read §5's arithmetic first; run A (stock) and B (reduced buffers).
- **Log per MD client:** `queue_out{pending}` every message; `send_would_block` count; cumulative `queued` vs `sent`; `wbuf_hwm`.
- **Log for the engine:** `engine{engine_ns}` per BUY/SELL. **This is the proof that matters** — it must stay flat (single-digit µs) while the slow client's `pending` climbs.
- **OS, sampled at t≈0, mid, end:**
  - `netstat -an -p tcp | grep 5000` → `Send-Q` for the slow client's server-side socket climbs and pins at the send-buffer limit; the normal client's stays ~0.
  - `netstat -x` → `S-BCNT` approaching `S-HIWA` for the slow client only. Richer than `-an`; fall back to `-an` if the columns differ on your build.
  - `tcpdump` → **`win 0`** advertisements from the slow client, then zero-window probes from the persist timer. Definitive network-level evidence of receiver flow control.
  - `ps -o wchan` during the flood → still `kqread`, **never `sbwait`**. One line proving the engine never blocked in a socket write.
- **Answer:** the slow client stops reading → its receive buffer fills → it advertises a zero window → the server's send buffer fills → `send()` raises `BlockingIOError` → the server stops writing to *that socket only*, parks the backlog in that connection's userspace buffer, and re-arms `KQ_FILTER_WRITE`. The engine and every other client are untouched because the engine never calls `send()`. Flow control is per-connection; head-of-line blocking appears only if you share one output path across connections.
- **Artifacts: P1** `pending` bytes vs time, two series (the most persuasive figure in your report) · **P2** engine latency vs trade index, flat, overlaid on P1's ramp · **P3** cumulative queued vs sent for the slow client (the gap is the backlog) · **F3** tcpdump `win 0` + probes · **T8** the 3×2 socket-buffer snapshot table · **T9** counters (EAGAIN count, `wbuf_hwm`, TRADEs generated/queued/sent, per client) for runs A and B.

### 8.8 Unexpected client disconnection
- Harness `SIGKILL`s a real helper process (`experiment.py:912`), so the kernel — not the application — closes the fd.
- **Log:** `eof_fin` for the killed fd with its timestamp and delta from the kill. Then `close{orders_surviving}`. Then confirm later `TRADE`s produce `notify_dropped` for that sid and normal delivery to the survivor.
- **OS:** `tcpdump` → FIN from the dead client's socket; if your server writes *after* that, the peer's now-nonexistent socket answers **RST**, and your next `send()` raises `BrokenPipeError` (EPIPE, 32). Capture the FIN → data → RST sequence; that is the crisp answer.
- **Answer:** SIGKILL is indistinguishable from `close()` on the wire — the kernel sends a normal FIN, because TCP has no concept of "the process died." Detection is therefore **reactive**: you learn only on the next read or write of that socket. An event loop watching the fd for readability learns in microseconds; a server that never touches an idle subscriber's fd again wouldn't learn until it tried to push. TCP carries no liveness signal unless you build one (`SO_KEEPALIVE`, or an application heartbeat) — name that as the fix.
- **The gap in the harness you must fill yourself:** Exp 8 kills a *Market-Data* client, which owns no orders, so it never tests §2.6's order-survival clause. Write the supplementary test: trader logs in, `BUY JNST 100 238`, disconnects; a second trader `SELL JNST 60 238`. Assert the trade executes, subscribers get `TRADE JNST 60 238`, and your log shows `notify_dropped` for the departed buyer's `BOUGHT`. **Graders will test this even though the harness does not.**
- **Artifact: T10** — time / event / wire / server observation / server action, plus received-TRADE counts for killed vs surviving client.

---

## §9. Report artifact manifest

Tick these off; each is a file on disk before you write prose.

**Fragmentation (§2.9 / §4.2 / Exp 3)**
- **F1** tcpdump: 4 segments, 6/10/7/1 bytes, 0.2 s apart
- **F2** stderr trace: 4 `recv`, 3 `frame_partial`, 1 message emitted
- **T3** recv-call ledger (§8.3)
- **T4** offline framer counts — "all N−1 split positions + 1000 random fragmentations parse identically." **This is the one that proves generality rather than one lucky case**
- **F4** the reverse direction: 3 resting SELLs at one price, one large BUY, and `tcpdump` showing **`ORDER_ACCEPTED` + 3×`BOUGHT` in a single segment** because `flush()` coalesces one loop iteration's output into one `send()`. F1 and F4 together prove app boundaries ≠ segment boundaries in *both* directions — and F4 exercises your client's framer too

**Backpressure (Exp 7)**
- **P1** `pending` bytes vs time, slow vs normal — *the* figure
- **P2** engine latency flat while P1 ramps — *the* proof
- **P3** cumulative queued vs sent, slow client
- **F3** tcpdump `win 0` + zero-window probes
- **T8** `netstat -an`/`-x` snapshots, 3 times × 2 clients
- **T9** counters for run A and run B, with the 85 KB vs 96 KB arithmetic stated as a prediction
- **W1** `ps -o wchan` during the flood: `kqread`

**Architecture and lifecycle**
- **T5** Exp 4 correct vs naive control · **T1** listening vs connected · **T2** state timeline with `CLOSE_WAIT` transient · **T7** FIN vs RST matrix · **T10** Exp 8 timeline + TRADE counts · **T11** §2.6 order survival: `close{orders_surviving=1}` → later `TRADE` delivered → `notify_dropped` · **T12** errno reference (35/32/54/60) · **B1** `baseline.txt` of every sysctl you depend on, captured Day 0

Plot P1–P3 from the JSONL with `src/tools/plot.py` (matplotlib). Not part of the graded server, but keep it in the submission and mention it in the README for reproducibility.

---

## §10. Bonus — 70,000 idle connections

Start only once Wednesday's gate has passed and a working zip is uploaded. Python is not a barrier here; two walls are, and neither is about language.

**Wall 1 — ephemeral ports. Arithmetic, not tuning.** A connection is identified by its 4-tuple. With every connection going to `127.0.0.1:5000` from source `127.0.0.1`, uniqueness demands a unique source port, and `net.inet.ip.portrange.first/last` defaults to roughly 10000–65535 = **~55,500 ports**. Widened to 1024–65535, ~64,500. **70,000 connections from one source IP to one destination is impossible.**
```sh
ifconfig lo0 alias 127.0.0.2/32          # then 127.0.0.3, ...
sysctl net.inet.ip.portrange.first=1024
sysctl net.inet.ip.portrange.randomized=0   # sequential allocation; random probing
                                            # thrashes badly near exhaustion
```
Have your client generator `bind()` to a specific alias before `connect()`. Two aliases give ~110k of headroom. Split the generator across ~4 processes (each on its own alias, ~17.5k connections) — easier on per-process fd limits and simpler to restart.

**Wall 2 — file descriptors.**
```sh
sysctl kern.maxfiles=400000 kern.maxfilesperproc=200000
sysctl kern.ipc.soacceptqueue=4096        # accept backlog during the ramp
limits -n 200000 ./server/run-server 127.0.0.1 5000
# or set openfiles in /etc/login.conf and run cap_mkdb /etc/login.conf
```

**Measurement commands, mapped to the handout's exact table columns:**

| Column | Command |
|---|---|
| Server memory usage | `ps -o rss,vsz -p <pid>` (`procstat -v <pid>` for detail) |
| Server CPU usage | `ps -o %cpu,time -p <pid>` · `top -H -p <pid>` |
| Open fds for server | `procstat -f <pid> \| wc -l` |
| System-wide open files | `sysctl kern.openfiles kern.maxfiles` |
| Socket-buffer usage/limit | `netstat -m` · `sysctl kern.ipc.nmbclusters kern.ipc.maxsockbuf` |
| Max connections established | `netstat -an -p tcp \| grep -c ESTABLISHED` |

**Python-specific tuning for the ramp.** `__slots__` on `Conn` is what keeps you near ~250 B/connection instead of ~500 B; at 70k that is a 17 MB difference. If RSS turns out to be your reported bottleneck, the documented next step is to drop the per-connection Python `socket` object entirely: `fd = cs.detach()` after `accept()`, then `os.read(fd, n)` / `os.write(fd, data)` on the bare integer (both raise `BlockingIOError` exactly as `recv`/`send` do, and Python already ignores SIGPIPE so `MSG_NOSIGNAL` isn't needed). Measure before doing this — it costs readability and probably isn't your bottleneck.

**The answers you should expect** (measure, don't assume): userspace RSS grows ~250–400 B per connection (~20–30 MB at 70k) while the kernel spends **~2 KB per connection** on `inpcb`+`tcpcb`+socket (~140 MB at 70k). CPU ≈ 0, because idle connections generate no events. So the first bottleneck is almost certainly **file descriptors or kernel socket memory, not your process** — and the well-supported version of that conclusion is exactly what bonus questions 2–4 ask for. Note that idle sockets do **not** reserve their full `sendspace`/`recvspace`; mbufs are allocated on demand, which is why 70k idle connections cost far less than 70k × 96 KB.

**Bonus questions 3–5 have a cheap, decisive answer in Python.** Take the §2.1 thread-per-connection sketch, wire it to just the accept path, and run your client generator against it. It will die somewhere in the low thousands, because `kern.threads.max_threads_per_proc` (expect 1500 — verify) is a thread *count* ceiling that no stack-size tuning can move, and 70k connections need 70k–140k threads. **One measurement answers questions 3, 4, and 5 together**, and building it costs ~40 lines because you already have the engine.

---

## §11. Trap ledger

Ordered by time lost when missed.

1. **An exception escaping the event loop** → every client dies at once, failing §4.4. Python's replacement for the SIGPIPE trap, and easier to hit. §2.4 RULE 3
2. **`ConnectionResetError` treated as fatal** → the harness's readiness probe kills you before Experiment 1 begins. §1.1
3. **`KQ_FILTER_WRITE` left enabled on an empty `wbuf`** → 100% CPU spin, visible and damning in Exp 7's CPU column. §2.2
4. **Exp 7 run at stock buffer sizes** → 85 KB of feed fits in ~96 KB of buffering, nothing blocks, wrong conclusion drawn. §5
5. **`int()` for quantity/price** → `+5`, `1_000`, `" 5"`, `5.0` slip into the book. `isdigit()`/`isdecimal()` are *not* safe guards on `str`. Stay in `bytes` and use `re.fullmatch(rb'[0-9]+', ...)`. §3
6. **Closing a socket mid-batch** → the kernel recycles the fd and a stale event lands on a new connection. §2.4 RULE 1
7. **`Order` holding a `Conn`** → breaks §2.6 and raises on a fill after the owner disconnected. §4
8. **CRLF in a launcher** → `bad interpreter`, every experiment fails. macOS→FreeBSD editing makes this live. §1.5
9. **Launcher without `exec`** → `killpg` kills the wrapper, server orphaned, harness hangs. §1.5
10. **Launcher without `-u`** → your trace sits in a buffer while you screenshot an empty terminal. §1.5
11. **Ignoring EOF** → sockets stuck in `CLOSE_WAIT` forever, fd leak, visible in Exp 2. §8.2
12. **An idle/handshake timeout** → fails Exp 1, 2, 5, 6, all of which use silent connections. §1.3
13. **Importing `selectors`, `socketserver`, or `asyncio`** → §4.1.2 violation, and `selectors` destroys your viva answer about which mechanism you used. §1.9
14. **Unbounded `rbuf`** → a client that never sends `\n` grows it without limit. §3
15. **Blocking request/response Trader Client** → cannot display async `BOUGHT`; violates §2.3/§2.7. §6
16. **`c.wbuf[c.woff:]` instead of `memoryview(c.wbuf)[c.woff:]`** → copies the pending tail on every send; O(n²) during Exp 7, perturbing the measurement. §2.2
17. **`__MACOSX`/`.DS_Store` in the zip** → risks the structure check (10% + one correction). `zip -r A2_<roll1>_<roll2>.zip server client src README.md report.pdf -x '*.DS_Store' -x '__MACOSX/*'`, then verify with `unzip -l`.

---

## Summary

Build a **single-threaded event loop on `select.kqueue()`**, called directly, with a per-connection `bytearray` output buffer and lazily-registered write notification. That one choice answers the assignment's three hard questions structurally rather than by special-case code: framing lives in an accumulating `rbuf` scanned for `\n`; Experiment 7 is solved because `engine.handle()` returns a list of messages and cannot see a socket, so TCP backpressure has no path to the matching engine; and orders outlive TCP drops because `Order.owner_sid` is an `int` into a session table rather than a reference to a connection. The bonus forecloses thread-per-connection outright, and in CPython the GIL means that design couldn't convert its overhead into throughput anyway — which turns your architecture section from a preference into two independent measurements.

Python's real cost is concentrated in two places, and both are cheap to defend against. **Input validation:** `int()` accepts `1_000` and `" 5"`, and `isdigit()` returns `True` for Devanagari and superscript digits — so stay in `bytes` end to end and validate with an explicit anchored regex. **Failure isolation:** where C++ would die from SIGPIPE, you die from an uncaught exception taking every client with it, so the layered `except` around each event handler is not defensive padding, it is the thing that satisfies §4.4.

Two moves will decide your experiment marks more than code quality. **Do the Exp 7 arithmetic before you run it** — 5000 trades × 17 bytes = 85 KB of feed against roughly 96 KB of default socket buffering, so at stock settings nothing blocks and the naive conclusion is wrong; predict it, then reduce the buffers and report both runs. And **build the naive blocking server as a control for Exp 4** — fifteen minutes in Python turns "my server is fast" into a measured contrast of 0.4 ms against a 5-second timeout, with `wchan` reading `kqread` against `sbwait`. Those two, plus the offline framer test that proves fragmentation handling across every split position rather than one lucky case, are what separate a correct implementation from a convincing investigation.
