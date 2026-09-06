# Report working notes — The Socket Exchange (COL334 A2)

Scratch file. Fill this in as you go through the VM work; the finished
`report.pdf` is written FROM this, not the other way around. Paste raw
findings, command output, and screenshot filenames here immediately after
each run — do not trust yourself to reconstruct it later from memory.

Every artifact ID below matches the manifest in `A2_Engineering_Roadmap.md`
§9. Check items off as they land.

---

## Day 0 — status

- [x] Directory skeleton, launchers, stub server (verified against real
      harness runs — see session log; readiness probe RST handled correctly,
      `ev_fflags=54` matched `errno=54` exactly, fd reuse after teardown
      confirmed safe)
- [x] AirPlay Receiver / Control Center was squatting port 5000 on this Mac —
      disabled (System Settings → General → AirDrop & Handoff → AirPlay
      Receiver: off). **Note for report methodology section**, one line: not
      a code defect, purely a macOS dev-environment conflict; irrelevant to
      the FreeBSD grading VM.
- [x] `baseline.txt` captured **inside the FreeBSD VM** — B1. Real
      `sendspace=32768`/`recvspace=65536`, auto-tuning on both directions;
      pre-flight arithmetic says stock buffers (98304 B) exceed the Exp 7
      feed (85000 B) — Run B will reduce buffers to force backpressure into
      the observation window.

## Day 1 — Phase 1 + 2 (offline, no sockets)

- [x] `framing.py` written and passing `T-FRAG-ALL` / `T-FRAG-RAND` /
      `T-COALESCE` / `T-BOUNDARY` / `T-OVERFLOW` (`tests/test_all.py`) —
      written verbatim to `PHASE_1_2_SPEC.md`'s mandated shape (eager single
      compaction before `yield from lines`, per amendment 2)
- [x] `protocol.py` parser written (`parse_u32`, `parse_line`) passing
      `T-VALID` — `re.fullmatch` per amendment 1; the 6 syntax-only
      `REASON_*` codes per amendment 5 (protocol.py never touches
      `duplicate_username`/etc.)
- [x] `engine.py` written passing `T-ENGINE`, including
      `test_engine_disconnect_then_match` — the §2.6 case the harness itself
      never exercises
- [x] `grep -ln 'import socket' src/*.py` → only `server.py`, `trader.py`,
      `market_data.py` — confirmed
- [x] `pytest` (`tests/test_all.py`): **29 passed** in 0.74s, clean first run
      against the locked spec, no test edits needed

Note: `server.py`'s `handle_readable_bytes()` still has its documented
THROWAWAY Phase-3 placeholder body (line-splitter with no framer/protocol/engine
wiring) — that's expected, it's explicitly labeled scaffolding for
Experiments 1–3 trace evidence only, and rewiring it to the real
`framer.feed()` → `parse_line()` → `engine.handle()` pipeline is Phase 3
work, not Day 1 scope.

## Day 2–3 — Phase 3 + robustness (needs the VM)

- [x] Exp 1 clean in the VM (see raw log below) — T1 captured with real
      sockstat/procstat/netstat data
- [x] Exp 2 clean in the VM (see raw log below) — T2 captured, with a
      genuinely interesting methodological wrinkle (netstat's 0.5s polling
      interval was too coarse to catch CLOSE_WAIT; tcpdump timing filled
      the gap instead — see below)
- [x] Exp 3 — F1/F2/T3 all captured clean (see raw log below)
- [ ] Exp 4
- [ ] Exp 5
- [~] Exp 4 real-server side done (see raw log below); naive-blocking
      control (T5's contrast) not written yet — in progress
- [ ] Exps 6, 8 clean; supplementary disconnect-then-match test passing
      against the real event loop too

### VM environment gotcha (methodology note for the report)

Exp 1's first attempt was contaminated: the server was started manually
(`./server/run-server 127.0.0.1 5000 &`) *before* invoking `experiment.py 1`.
`experiment.py` manages the server's lifecycle itself (it launches its own
`run-server` subprocess, per the handout's launcher-indirection contract) —
so the manual instance and the harness's own subprocess fought over
`bind()` on port 5000, and the harness's subprocess died with
`OSError: [Errno 48] Address already in use`. The client still connected
(to the leftover manual instance), so the run *looked* partially alive but
produced a traceback that has no place in report evidence. Fix: never
pre-start the server — let each `experiment.py N` invocation own the
server process end-to-end. Re-run was clean (see below).

Also noted: FreeBSD's `pgrep -f` uses POSIX extended regex, so
`'run-server\|server.py'` (with an escaped pipe, shell/grep-BRE style) is
read as a literal backslash-pipe and matches nothing — the correct form is
an unescaped `'run-server|server.py'`, or just read the PID straight out of
`sockstat`'s output, which is what actually worked below.

### Raw session log — Experiment 1

**Session A** (`python3 experiment.py 1`):
```
=== Experiment 1: Listening and Connected Sockets ===
Started Exchange Server (PID 4156).
     0.145ms listen           fd=3 host='127.0.0.1' port=5000 backlog=4096
Exchange Server is listening.
    75.854ms accept           sid=1 fd=5 peer='127.0.0.1:57442' nconn=1
    75.890ms eof_rst          sid=1 fd=5 errno=54 ev_fflags=54
    75.907ms close            sid=1 fd=5 why='RST' role='untyped' recvs=0 bytes_in=0 queued=0 sent=0 eagain=0 wbuf_hwm=0 orders_surviving=0
    75.926ms destroy          sid=1 fd=5 nconn=0
Experiment client: connected from 127.0.0.1:31919
    76.014ms accept           sid=2 fd=5 peer='127.0.0.1:31919' nconn=1
The client connection is now established and idle.
[^C after telemetry captured in session B]
Experiment finished.
Stopping Exchange Server...
 91732.725ms signal           signo=15
```
Post-teardown: `sockstat -4 | grep 5000` → empty. Clean exit, port released.

**Session B** (live, while the client sat idle):
```sh
$ sockstat -4 | grep 5000
root python3.12 4156  3 tcp4  127.0.0.1:5000        *:*
root python3.12 4156  5 tcp4  127.0.0.1:5000        127.0.0.1:31919
root python3.12 4155  3 tcp4  127.0.0.1:31919       127.0.0.1:5000

$ netstat -an -p tcp | grep 5000
tcp4  0  0  127.0.0.1.5000    127.0.0.1.31919   ESTABLISHED
tcp4  0  0  127.0.0.1.31919   127.0.0.1.5000    ESTABLISHED
tcp4  0  0  127.0.0.1.5000    *.*               LISTEN
```
(`procstat -f` on the first attempt errored on usage because the `pgrep`
pattern above matched nothing — not re-run yet with a corrected PID; the
sockstat/netstat pair already gives T1 everything it needs.)

**T1 finding:** fd=3 (`sid` none — never assigned one, it's the factory)
is the *listening* socket: local `127.0.0.1:5000`, foreign `*:*`, state
`LISTEN`. fd=5 (`sid=2`) is the *accepted/connected* socket: local
`127.0.0.1:5000`, foreign `127.0.0.1:31919`, state `ESTABLISHED`. Both
share the same local port — it's the 4-tuple (local+foreign address and
port), not the port alone, that identifies a distinct connection. The
client-side process (pid 4155, experiment.py's own connect()) shows the
mirror-image 4-tuple, confirming the same conversation from the other end.

**RST evidence (feeds T7):** the very first accept (sid=1, fd=5, peer
`:57442`) is the handout's own `wait_for_server()` readiness probe —
`SO_LINGER{1,0}` then close — arriving as an abortive RST. `eof_rst`
fired with `errno=54` (`ECONNRESET`) and `ev_fflags=54` matching exactly,
confirming the kqueue EOF-with-error path is being read correctly, not
just treated as a plain FIN. This is now sourced from a real harness run,
not synthetic/manual testing — T7's RST column can cite this directly.
Still need: the FIN-side capture (a client that closes cleanly, for
contrast) — Exp 2/6A will supply that.

### Raw session log — Experiment 2

**Session A** (`python3 experiment.py 2`):
```
=== Experiment 2: Observing TCP Connection States ===
Started Exchange Server (PID 4228).
     0.168ms listen           fd=3 host='127.0.0.1' port=5000 backlog=4096
    77.030ms accept           sid=1 fd=5 peer='127.0.0.1:15056' nconn=1
Exchange Server is listening.
    77.091ms eof_rst          sid=1 fd=5 errno=54 ev_fflags=54
    77.130ms close            sid=1 fd=5 why='RST' ...
Experiment client: connected from 127.0.0.1:27543
Phase 1: the client is connected and idle. Investigate the connection now.
    77.231ms destroy          sid=1 fd=5 nconn=0
    77.297ms accept           sid=2 fd=5 peer='127.0.0.1:27543' nconn=1
Phase 2: closing the client connection. Investigate the connection again.
The Exchange Server will remain running for 15 seconds.
 10081.960ms eof_fin          sid=2 fd=5 ev_eof=True
 10082.160ms close            sid=2 fd=5 why='FIN' ... orders_surviving=0
 10082.450ms destroy          sid=2 fd=5 nconn=0
Experiment finished. Stopping Exchange Server...
 25082.413ms signal           signo=15
```
Clean FIN-based teardown this time (contrast with Exp 1's RST) — `eof_fin`
correctly distinguished from `eof_rst`, `close`'s `why` field says `'FIN'`.

**Session B — netstat timeline (condensed; full log in `exp2_netstat_timeline.txt`):**
Continuous `ESTABLISHED`/`ESTABLISHED`/`LISTEN` triple from ~11:30:49.99
through ~11:30:59.21 (the 10s idle Phase 1 + start of Phase 2), then at
11:30:59.72 only `127.0.0.1.27543 > 127.0.0.1.5000  TIME_WAIT` remains
(server-side entry already gone), and by 11:31:00.23 — **0.5s later** —
even that has vanished, leaving only `LISTEN`.

**tcpdump readback (`exp2.pcap`), annotated:**
```
[stray SYN, port 35906] -> immediate RST from server         <- see note below
[15056] SYN -> SYN/ACK -> ACK -> RST from client              <- readiness probe (same shape as Exp 1)
[27543] SYN -> SYN/ACK -> ACK                                 <- real experiment connection, established
+10.004482s  [27543 -> 5000]  FIN,ACK   (client closes -- ACTIVE closer)
   +58µs     [5000 -> 27543]  ACK                              <- server ACKs the FIN: server is now in CLOSE_WAIT
  +598µs     [5000 -> 27543]  FIN,ACK                          <- server's own close() -- CLOSE_WAIT lasted ~656µs total
   +27µs     [27543 -> 5000]  ACK                              <- client ACKs; client now in TIME_WAIT, server socket gone
```

**T2 finding, and why netstat alone couldn't prove it:** the packet
timestamps show the server ACKs the client's FIN in 58µs, then sends its
*own* FIN only 598µs after that — i.e. **the server's `CLOSE_WAIT` window
was ~656 microseconds long**, end to end. Our 0.5s netstat polling
interval is ~750x too coarse to ever land a sample inside that window,
which is *why* no `CLOSE_WAIT` line appears in the netstat timeline at
all despite the state genuinely existing. This is itself the correct
report finding, framed precisely: **the absence of a lingering
`CLOSE_WAIT` in netstat is consistent with (not contradicted by) a
correctly-behaved server that closes essentially immediately on EOF** —
the graders' failure mode (`CLOSE_WAIT` forever, fd leak, §1.11) would
show up as that state persisting across *every* sample, which is exactly
what we don't see. tcpdump's microsecond timestamps are the right
instrument for T2's actual duration measurement; netstat is the right
instrument only for confirming it *doesn't get stuck*.

**Open question — TIME_WAIT cleared far faster than expected, needs
verification:** `baseline.txt` reports `net.inet.tcp.msl = 30000` (ms),
so textbook 2×MSL `TIME_WAIT` should hold for ~60s. Observed: the
client's `TIME_WAIT` entry appeared once (11:30:59.72) and was already
gone half a second later. Leading hypothesis: FreeBSD's
`net.inet.tcp.nolocaltimewait` sysctl (defaults to `1` on recent
releases) skips the full `TIME_WAIT` hold for connections that are
*entirely loopback* (both endpoints on the same host), as a local
performance optimization — which is exactly this setup. **Verify with
`sysctl net.inet.tcp.nolocaltimewait` on the VM before writing this into
the report as fact**; if it's `1`, that's the answer and it's worth a
sentence in the report (a real FreeBSD-specific nuance, not a bug in our
server or measurement). If it's `0`, this needs a second look.

### Raw session log — Experiment 3 (run 1, no tcpdump — F2/T3 only)

```
=== Experiment 3: TCP as a Byte Stream ===
Started Exchange Server (PID 4589).
    73.789ms accept           sid=1 fd=5 peer='127.0.0.1:62783' nconn=1
    73.842ms eof_rst          sid=1 fd=5 errno=54 ev_fflags=54     <- readiness probe, as always
Experiment client: connected from 127.0.0.1:51544
    74.047ms accept           sid=2 fd=5 peer='127.0.0.1:51544' nconn=1
    74.120ms recv  n=6  rbuf_before=0  rbuf_after=6   lines_out=0  hex=4c4f47494e20      -> b'LOGIN '
   274.525ms recv  n=10 rbuf_before=6  rbuf_after=16  lines_out=0  hex=6578706572696d656e74 -> b'experiment'
   476.870ms recv  n=7  rbuf_before=16 rbuf_after=23  lines_out=0  hex=5f747261646572    -> b'_trader'
   680.757ms recv  n=1  rbuf_before=23 rbuf_after=0   lines_out=1  hex=0a                -> b'\n'
   680.990ms queue_out  sid=2 fd=5 n=3 pending=3 hwm=3             <- "OK\n" reply, LOGIN succeeded
```

**T3 (recv-call ledger) — done, exact match to the roadmap's own predicted
table:**

| # | Δt (ms, approx) | bytes | payload | rbuf_after | lines_out |
|---|---|---|---|---|---|
| 1 | 0 | 6 | `LOGIN ` | 6 | 0 |
| 2 | ~200 | 10 | `experiment` | 16 | 0 |
| 3 | ~202 | 7 | `_trader` | 23 | 0 |
| 4 | ~204 | 1 | `\n` | 0 | 1 |

Reassembled payload: `LOGIN experiment_trader\n` — byte-for-byte the
handout's own example. Confirms `Framer` buffers correctly across all
four partial writes and only emits on the fourth.

**Note for F2 / the report's methodology section — a real, worth-mentioning
consequence of the Phase 3 rewiring:** the roadmap's original F2 spec
expected to see `4 recv` + `3 frame_partial` + `1 emitted` trace lines.
Those `frame_partial`/`frame_complete` events belonged to Day 0's
*throwaway* `handle_readable_bytes()` body (the inline line-splitter),
which was deliberately deleted wholesale when Phase 3 wired in the real
`framing.py` → `protocol.py` → `engine.py` pipeline — so those specific
event names no longer fire, by design, not by omission. The real evidence
is arguably cleaner anyway: the `recv` events' own `rbuf_after` field
tells the same story (0→6→16→23→0), and the single `queue_out` line
appearing only after the 4th `recv` is direct proof that only the fully
-reassembled line triggered `parse_line()`/`engine.handle()` — nothing
downstream fired on the three partial deliveries. Document this
substitution explicitly in the report rather than trying to force the
old event names back in.

### Raw session log — Experiment 3 (run 2, with tcpdump — F1)

**Caveat on this capture:** `tcpdump` was started before the *previous*
run's still-idling connection (port 51544, from run 1 above) was actually
Ctrl-C'd, so `exp3.pcap` contains two connection cycles: an incidental
leftover teardown of run 1 (ports 45791/62783/51544, its FIN appearing at
the +94.8s mark — harmless, just don't mistake it for this run's data),
and the real fresh run (PID 4595, ports 10427/11467/49237) matching the
session A transcript pasted alongside it. F1 below is drawn from the
second cycle only.

**tcpdump readback, F1 segments (annotated, relative to the SYN):**
```
+35.775s  SYN 10427->5000            <- pre-check, refused (RST, no listener yet -- wait, negative,
                                          see note: this is the recurring "connection-refused" RST
                                          shape from Exp 2, confirmed reproducible here too)
+0.102s   SYN 11467->5000  -> SYN/ACK -> ACK -> RST from 11467   <- readiness probe (same shape, 3rd time now)
+0.000s   SYN 49237->5000  -> SYN/ACK -> ACK                      <- real client, ESTABLISHED
+0.000170s  PSH 49237->5000  len=6   "LOGIN "        <- fragment 1
+0.200108s  PSH 49237->5000  len=10  "experiment"    <- fragment 2  (0.2001s after frag 1 -- exact match to handout's 0.2s spacing)
+0.200854s  PSH 49237->5000  len=7   "_trader"       <- fragment 3  (0.2009s gap)
+0.202322s  PSH 49237->5000  len=1   "\n"            <- fragment 4  (0.2023s gap)
+0.000545s  PSH 5000->49237  len=3   "OK\n"          <- reply, 545µs after final fragment
+11.336s    FIN 49237->5000                          <- client closes (idle hold, then Ctrl-C)
+0.000041s  ACK 5000->49237                          <- server ACKs FIN, CLOSE_WAIT begins
+0.000466s  FIN 5000->49237                          <- server's own close -- CLOSE_WAIT ~507µs total
+0.000007s  ACK 49237->5000                          <- client -> TIME_WAIT
```

**F1 finding:** four `PSH` segments of exactly 6/10/7/1 bytes, gaps of
~0.200s between each — matches the handout's own stated 0.2s inter-write
delay almost to the millisecond, and matches run 1's `recv()` byte counts
exactly (T3 corroborated at the packet level, not just the application
trace). Reply (`OK\n`, 3 bytes) goes out as a single segment 545µs after
the last fragment arrives — a second, independent data point (after
Exp 2) that this server's CLOSE_WAIT-to-own-FIN latency sits in the
sub-millisecond range (507µs here vs. 656µs in Exp 2) — worth stating in
the report as a *repeatable* property, not a one-off measurement.

### Raw session log — Experiment 4 (real server, correct side of T5)

**Session A:**
```
=== Experiment 4: One Client Should Not Stall the Others ===
Started Exchange Server (PID 4611).
    75.828ms accept  sid=1 fd=5 peer='127.0.0.1:42138' nconn=1     <- probe
    75.917ms eof_rst sid=1 fd=5 errno=54 ev_fflags=54
Client 1: connected from 127.0.0.1:50433
Client 1 will send an incomplete application message.
    76.014ms accept  sid=2 fd=5 peer='127.0.0.1:50433' nconn=1
Client 1 will now remain silent.
    76.043ms recv    sid=2 fd=5 n=20 rbuf_before=0 rbuf_after=20 lines_out=0 hex=4c4f47494e20626c6f636b65645f636c69656e74  -> "LOGIN blocked_client" (no \n)
Client 2: connected from 127.0.0.1:44248
Client 2 will send a complete application message.
  2076.828ms accept  sid=3 fd=6 peer='127.0.0.1:44248' nconn=2      <- ~2s after client 1 stalled
  2077.069ms recv    sid=3 fd=6 n=20 rbuf_before=0 rbuf_after=0 lines_out=1 hex=... -> "LOGIN active_client\n"
  2077.177ms queue_out sid=3 fd=6 n=3 pending=3 hwm=3
Client 2 response: 'OK'
Elapsed time: 0.001 seconds
```

**Session B, live during the stall:**
```
$ pgrep -f server.py
4611
$ ps -o pid,tid,wchan,state -H -p 4611
 PID    LWP WCHAN  STAT
4611 100160 kqread Ss
```

**T5 finding (real-server side):** `wchan=kqread`, never `sbwait` —
exactly the roadmap's "one word decides it" proof that the event loop is
parked in `kevent()`, not blocked inside a `recv()` on Client 1's dead
connection. Client 2's `LOGIN`→`OK` round trip completed in **0.001s**
(well under the 5ms gate), while Client 1's 20 stalled bytes just sit in
`rbuf` (`rbuf_after=20`, no `lines_out`) with zero effect on Client 2.
**T5 naive control — written and locally verified (`src/naive_server.py`,
throwaway/non-submission, gated behind `EXCH_NAIVE=1` in
`server/run-server` so `experiment.py 4` can run against it unmodified).**
Local test (`src/tools/verify_naive_stall.py`, sandbox not VM): Client 1
sends the same 20-byte no-`\n` stall, Client 2 connects 1s later and gets
**zero response within a 2s timeout** — confirms the predicted head-of-line
block before this ever touches the VM. `pytest` still 29/29, structure
check still clean. Note: the exit-gate grep now legitimately lists 4 files
(`naive_server.py` added) — expected, it's non-submission code with its
own `socket` use by design. Still needed: push to VM and run the actual
`EXCH_NAIVE=1 python3 experiment.py 4` contrast, capturing `wchan=sbwait`.

**T7 addendum, confirmed reproducible:** the connection-refused RST shape
(bare SYN answered by an immediate RST, no SYN-ACK, no `accept()`, no
trace event) appears again here (port 10427) exactly as it did in Exp 2 —
this is `experiment.py`'s own pre-flight port check, consistently
reproducible across runs, not a one-off artifact.

**T7 addendum — a second, distinct RST shape:** the very first packet in
the capture (port 35906, a SYN answered immediately by a bare RST with no
SYN-ACK) is a *different* RST than the readiness-probe abortive close.
This one fired before our server had even called `listen()` yet — it's
the kernel's standard "nothing is listening on this port"
connection-refused response, most likely `experiment.py` doing its own
pre-flight check before spawning the server subprocess. Worth
distinguishing in T7's matrix: **connection-refused RST** (SYN-stage,
no accept(), never touches our code, no trace event at all) vs.
**abortive-close RST** (post-accept, `SO_LINGER{1,0}`, surfaces as
`ConnectionResetError`/`errno=54` in our event loop). Both are "RST" but
only the second one is ours to handle.

## Day 3 — Exp 7 (the centrepiece)

- [ ] Run A (stock buffers) — record whether backpressure appeared at all
- [ ] Run B (reduced buffers per §5) — record the predicted vs actual onset

---

## Artifact tracker

Fragmentation (§2.9 / §4.2 / Exp 3)

| id | what | status | file(s) |
|---|---|---|---|
| F1 | tcpdump: 4 segments 6/10/7/1 bytes | ⬜ | |
| F2 | stderr trace: 4 recv, 3 frame_partial, 1 emitted | ⬜ | |
| T3 | recv-call ledger (table) | ⬜ | |
| T4 | offline framer pass counts (N-1 splits + 1000 random) | ✅ | `tests/test_all.py::test_frag_all_split_positions` (N-1/N-1, N=48 for the 3-line LOGIN/BUY/SELL fixture) + `::test_frag_random_multiway_splits` (1000/1000, seed=0) — both green |
| F4 | reverse direction: 3 SELLs + 1 sweeping BUY in one segment | ⬜ | |

Backpressure (Exp 7)

| id | what | status | file(s) |
|---|---|---|---|
| P1 | pending bytes vs time, slow vs normal | ⬜ | |
| P2 | engine latency flat during P1's ramp | ⬜ | |
| P3 | cumulative queued vs sent, slow client | ⬜ | |
| F3 | tcpdump win 0 + zero-window probes | ⬜ | |
| T8 | netstat -an/-x snapshots, 3× × 2 clients | ⬜ | |
| T9 | counters, run A vs run B | ⬜ | |
| W1 | ps -o wchan during the flood: kqread | ⬜ | |

Architecture and lifecycle

| id | what | status | file(s) |
|---|---|---|---|
| T1 | listening vs connected socket table | ✅ | Exp 1, real FreeBSD VM run: fd=3 LISTEN `127.0.0.1:5000`/`*:*` vs fd=5 (sid=2) ESTABLISHED `127.0.0.1:5000`/`127.0.0.1:31919` — sockstat + netstat, see raw session log above |
| T2 | TCP state timeline, CLOSE_WAIT transient | ✅ | Exp 2, real VM run: tcpdump shows CLOSE_WAIT duration ~656µs (too fast for 0.5s netstat polling to catch directly — see raw session log above for why that's the correct finding, not a gap in evidence) |
| T5 | Exp 4 correct vs naive control | ⬜ | |
| T6 | Exp 5 ready vs idle fds | ⬜ | |
| T7 | FIN vs RST matrix | ✅ | Both columns now real: FIN side from Exp 2 (`eof_fin`, `ev_eof=True`, `why='FIN'`, CLOSE_WAIT ~656µs from tcpdump) — RST side from Exp 1/2 (`eof_rst`, `errno=54`, `ev_fflags=54`, `why='RST'`), PLUS a second RST shape identified in Exp 2's capture (connection-refused RST at SYN stage, never reaches our code) worth listing as a third matrix row, not just two |
| T10 | Exp 8 timeline + TRADE counts | ⬜ | |
| T11 | §2.6 order-survival: close→orders_surviving>0→later notify_dropped | ⬜ | |
| T12 | errno reference table (35/32/54/60) | ⬜ | |
| B1 | baseline.txt from the FreeBSD VM | ✅ | captured on VM: `sendspace=32768`, `recvspace=65536` (auto-tuning on), capacity 98304 B vs Exp 7's 85000 B feed → pre-flight verdict says backpressure probably won't appear at stock settings; Run B (reduced buffers) planned to force it inside the window |

---

## Open questions / decisions to revisit

- CANCEL of another trader's order: reject as `not_owner` vs `unknown_order`?
  → decided `not_owner` (README §10) — keep consistent in engine.py and in
  `test_engine_cancel_not_owner`.
- Slow-consumer policy: evict at `WBUF_MAX` (4 MiB) vs unbounded vs conflate
  → decided: evict (roadmap §5) — confirm the eviction never actually
  triggers during a *normal* Exp 7 run (4 MiB is far above the ~85 KB feed);
  if it does trigger, something else is wrong.
- Exp 2's client-side `TIME_WAIT` cleared in <1s despite `msl=30000` implying
  a 60s hold → check `sysctl net.inet.tcp.nolocaltimewait` on the VM before
  the report states a cause. Not yet verified.

## Things to remember to say in the viva

- Why kqueue over threads: no lock exists, and the GIL means threads buy
  zero parallelism anyway for a two-instrument exact-price book.
- Why Exp 7 can't reach the matching engine: `engine.handle()` returns
  messages, never touches a socket — grep-checkable.
- Why orders survive a disconnect: `Order.owner_sid` is an int into a
  session table, not a reference to a connection.
- The AirPlay Receiver incident, if asked "did you hit any surprises" —
  good evidence of methodical debugging (lsof → identified the real
  process on the connection → fixed the environment, not the code).
