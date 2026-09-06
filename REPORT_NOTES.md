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
      `market_data.py` (+ `naive_server.py`, added later, non-submission —
      see Day 2-3) — confirmed
- [x] `pytest` (`tests/test_all.py`): **29 passed**, clean, no test edits
      needed

Note: Phase 3 (`handle_readable_bytes()` wired to
`framer.feed()` → `parse_line()` → `engine.handle()`) is done and deployed —
this is the code currently running on the VM for all experiments below.

## Day 2–3 — Phase 3 + robustness (needs the VM)

**Restarting Exp 1–4 from scratch (decision made mid-project after a git
baseline was established) — all four experiments below are pending fresh
runs. Previous findings for Exp 1–4 were valid but have been cleared here;
new runs will repopulate this section and the artifact tracker.**

- [x] Exp 1 (redo) — clean, T1 captured (see raw log below)
- [x] Exp 2 (redo) — clean, T2 captured (see raw log below)
- [x] Exp 3 (redo) — F1/F2/T3 all captured clean, single-cycle capture (see raw log below)
- [x] Exp 4 (real server, redo) — clean, wchan=kqread (see raw log below)
- [x] Exp 4 (naive control, `EXCH_NAIVE=1`) — T5 contrast complete, wchan=sbwait (see raw log below)
- [x] Exp 5 — T6 done, ready-vs-idle multiplexing confirmed (see raw log below)
- [x] Exp 6 — T7 done, FIN vs RST matrix complete (see raw log below)
- [ ] Exp 8 (+ supplementary disconnect-then-match test against the real
      event loop, not just the offline engine test)

### Raw session log — Experiment 1 (redo)

**Session A:**
```
=== Experiment 1: Listening and Connected Sockets ===
Started Exchange Server (PID 4898).
     0.184ms listen           fd=3 host='127.0.0.1' port=5000 backlog=4096
Exchange Server is listening.
    76.643ms accept           sid=1 fd=5 peer='127.0.0.1:36613' nconn=1
    76.682ms eof_rst          sid=1 fd=5 errno=54 ev_fflags=54
    76.699ms close            sid=1 fd=5 why='RST' role='untyped' recvs=0 bytes_in=0 queued=0 sent=0 eagain=0 wbuf_hwm=0 orders_surviving=0
    76.715ms destroy          sid=1 fd=5 nconn=0
Experiment client: connected from 127.0.0.1:28907
    76.861ms accept           sid=2 fd=5 peer='127.0.0.1:28907' nconn=1
The client connection is now established and idle.
[^C after telemetry captured in session B]
Experiment finished. Stopping Exchange Server...
 76991.300ms eof_fin          sid=2 fd=5 ev_eof=True
 76991.477ms signal           signo=15
```

**Session B (live, while the client sat idle):**
```
$ sockstat -4 | grep 5000
root python3.12 4898  3 tcp4  127.0.0.1:5000        *:*
root python3.12 4898  5 tcp4  127.0.0.1:5000        127.0.0.1:28907
root python3.12 4897  3 tcp4  127.0.0.1:28907       127.0.0.1:5000

$ netstat -an -p tcp | grep 5000
tcp4  0  0  127.0.0.1.5000    127.0.0.1.28907   ESTABLISHED
tcp4  0  0  127.0.0.1.28907   127.0.0.1.5000    ESTABLISHED
tcp4  0  0  127.0.0.1.5000    *.*               LISTEN
```

**T1 finding:** fd=3 (never assigned a `sid` -- it's the factory) is the
*listening* socket: local `127.0.0.1:5000`, foreign `*:*`, state `LISTEN`.
fd=5 (`sid=2`) is the *accepted/connected* socket: local `127.0.0.1:5000`,
foreign `127.0.0.1:28907`, state `ESTABLISHED`. Both share the same local
port -- it's the 4-tuple, not the port alone, that identifies a distinct
connection. Consistent with the pre-redo run: readiness-probe RST first
(`errno=54`/`ev_fflags=54`), then the real client accepted cleanly.

**Minor timing curiosity (not a bug):** on this run, `eof_fin` (the server
observing the harness's client sending its own FIN as part of Ctrl-C
cleanup) fired only 177 microseconds before `signal signo=15` (the
harness's SIGTERM to stop the server) -- so close/destroy for sid=2 never
got to print before the process was terminated. Harmless (`sockstat`
post-teardown confirmed the port was fully released), just a reminder that
a SIGTERM landing mid-teardown can truncate the very last trace lines --
don't read anything into a missing final `close`/`destroy` pair if a
`signal` line follows immediately after.

### Raw session log — Experiment 2 (redo)

**Session A:**
```
=== Experiment 2: Observing TCP Connection States ===
Started Exchange Server (PID 4956).
    78.908ms accept  sid=1 fd=5 peer='127.0.0.1:29538' nconn=1
    78.957ms eof_rst sid=1 fd=5 errno=54 ev_fflags=54
Experiment client: connected from 127.0.0.1:25236
    79.111ms accept  sid=2 fd=5 peer='127.0.0.1:25236' nconn=1
Phase 1: idle. Phase 2: closing the client connection.
The Exchange Server will remain running for 15 seconds.
 10081.099ms eof_fin  sid=2 fd=5 ev_eof=True
 10081.301ms close    sid=2 fd=5 why='FIN' ... orders_surviving=0
 10081.432ms destroy  sid=2 fd=5 nconn=0
Experiment finished. Stopping Exchange Server...
 25085.212ms signal   signo=15
```

**tcpdump readback, isolated to this run's cycle (probe 29538, real client
25236 -- matches session A's ports exactly):**
```
+0.102908s  SYN 29538->5000 -> SYN/ACK -> ACK -> RST from 29538   <- readiness probe
+0.000125s  SYN 25236->5000 -> SYN/ACK -> ACK                      <- real client, ESTABLISHED
+10.001687s [25236->5000] FIN,ACK                                   <- client closes (active closer)
+0.000080s  [5000->25236] ACK                                       <- server ACKs FIN, CLOSE_WAIT begins
+0.000529s  [5000->25236] FIN,ACK                                   <- server's own close -- CLOSE_WAIT 609us total
+0.000016s  [25236->5000] ACK                                       <- client -> TIME_WAIT
```

**T2 finding:** third independent measurement of this server's
CLOSE_WAIT-to-own-FIN latency, and it lands in the same sub-millisecond
band as before -- 609us here, vs 656us and 507us in the pre-redo runs.
Consistent and repeatable across three separate experiment invocations,
strong enough now to state in the report as a property of the
implementation, not a one-off. netstat's 0.5s polling never caught
CLOSE_WAIT or even TIME_WAIT this time (continuous ESTABLISHED samples
through ~12:29:35.30, then straight to LISTEN-only by ~12:29:35.82 --
a ~0.51s gap with no TIME_WAIT sample at all), which is a stronger version
of the same finding from the pre-redo Exp 2: the state genuinely exists
(tcpdump proves it, at the packet level) but is too brief for a coarse
netstat poll to land inside.

**Capture-hygiene finding (new, feeds forward into Exp 3+):** this
tcpdump also contains clearly leftover packets from the Exp 1 redo
(ports 36613/28907, matching that run's ports exactly), separated from
this run's cycle by multi-minute gaps -- tcpdump cannot retroactively
capture packets sent before it started, so this points to a **stale
tcpdump process from an earlier turn still running in the background on
the VM**, continuously capturing port 5000 traffic across every
experiment since. Before Exp 3's capture: run `pgrep tcpdump` (or
`ps aux | grep tcpdump`) and `pkill tcpdump` to clear any leftover
process, so future `.pcap` files contain only the run they're meant to.

### Raw session log — Experiment 3 (redo)

**Session A:**
```
=== Experiment 3: TCP as a Byte Stream ===
Started Exchange Server (PID 5337).
    79.848ms accept  sid=1 fd=5 peer='127.0.0.1:30505' nconn=1
    79.908ms eof_rst sid=1 fd=5 errno=54 ev_fflags=54
Experiment client: connected from 127.0.0.1:40700
    79.994ms accept  sid=2 fd=5 peer='127.0.0.1:40700' nconn=1
Sent 6 bytes.
    80.036ms recv  n=6  rbuf_before=0  rbuf_after=6   lines_out=0  hex=4c4f47494e20      -> "LOGIN "
Sent 10 bytes.
   282.252ms recv  n=10 rbuf_before=6  rbuf_after=16  lines_out=0  hex=6578706572696d656e74 -> "experiment"
Sent 7 bytes.
   486.481ms recv  n=7  rbuf_before=16 rbuf_after=23  lines_out=0  hex=5f747261646572    -> "_trader"
Sent 1 bytes.
   688.141ms recv  n=1  rbuf_before=23 rbuf_after=0   lines_out=1  hex=0a                -> "\n"
   688.325ms queue_out sid=2 fd=5 n=3 pending=3 hwm=3
Experiment finished. Stopping Exchange Server...
  8361.573ms eof_fin  sid=2 fd=5 ev_eof=True
  8362.182ms signal   signo=15
```
Post-teardown: `sockstat -4 | grep 5000` -> empty. Clean.

**tcpdump readback -- clean single-cycle capture this time (22 packets;
the earlier stale-tcpdump cleanup, `pkill tcpdump`, worked):**
```
+0s        SYN 46266->5000 -> immediate RST (no SYN/ACK)        <- connection-refused precheck, as always
+0.103834s SYN 30505->5000 -> SYN/ACK -> ACK -> RST from 30505  <- readiness probe
+0.000081s SYN 40700->5000 -> SYN/ACK -> ACK                     <- real client, ESTABLISHED
+0.000076s PSH 40700->5000 len=6  "LOGIN "        <- fragment 1
+0.201959s PSH 40700->5000 len=10 "experiment"    <- fragment 2 (202ms gap)
+0.204174s PSH 40700->5000 len=7  "_trader"       <- fragment 3 (204ms gap)
+0.201680s PSH 40700->5000 len=1  "\n"            <- fragment 4 (202ms gap)
+0.000565s PSH 5000->40700 len=3  "OK\n"          <- reply, 565us after final fragment
+7.630461s FIN 40700->5000                        <- client closes
+0.000026s ACK 5000->40700                        <- server ACKs FIN, CLOSE_WAIT begins
+0.000958s FIN 5000->40700                        <- server's own close -- CLOSE_WAIT 984us total
+0.000014s ACK 40700->5000                        <- client -> TIME_WAIT
```

**F1/T3 finding:** four PSH segments of exactly 6/10/7/1 bytes, gaps of
~202-204ms between each -- matches the handout's 0.2s spacing and this
run's own `recv()` byte counts exactly. Reassembled payload
`LOGIN experiment_trader\n`, byte-for-byte the handout's example.

**T2/CLOSE_WAIT running tally (4 independent measurements now, all
sub-millisecond):** 656us (Exp 2, pre-redo) / 507us (Exp 3, pre-redo) /
609us (Exp 2 redo) / **984us (Exp 3 redo)**. Slightly higher this time but
still well under 1ms and well within the same order of magnitude --
strengthens the report claim that this is a repeatable property of the
implementation (immediate close-on-EOF), not a single lucky measurement.

### Raw session log — Experiment 4 (redo, both halves -- T5 complete)

**Part 1, real server, session A:**
```
=== Experiment 4: One Client Should Not Stall the Others ===
Started Exchange Server (PID 5353).
    77.702ms accept  sid=2 fd=5 peer='127.0.0.1:27324' nconn=1
Client 1 will now remain silent.
    77.789ms recv    sid=2 fd=5 n=20 rbuf_before=0 rbuf_after=20 lines_out=0 hex=... -> "LOGIN blocked_client" (no \n)
Client 2: connected from 127.0.0.1:50811
  2082.357ms accept  sid=3 fd=6 peer='127.0.0.1:50811' nconn=2      <- ~2s after Client 1 stalled
  2082.700ms recv    sid=3 fd=6 n=20 rbuf_before=0 rbuf_after=0 lines_out=1 hex=... -> "LOGIN active_client\n"
  2082.853ms queue_out sid=3 fd=6 n=3 pending=3 hwm=3
Client 2 response: 'OK'
Elapsed time: 0.001 seconds
```
**Session B, live during the stall:**
```
$ pgrep -f server.py
5353
$ ps -o pid,tid,wchan,state -H -p 5353
 PID    LWP WCHAN  STAT
5353 100176 kqread Ss
```

**Part 2, naive control, session A:**
```
=== Experiment 4: One Client Should Not Stall the Others ===
Started Exchange Server (PID 5358).
Naive (blocking, single-threaded) server listening.
Client 1: connected from 127.0.0.1:47150
Client 1 will now remain silent.
Client 2: connected from 127.0.0.1:65437
Client 2 response: None
Elapsed time: 5.105 seconds
```
**Session B, live during the stall:**
```
$ pgrep -f naive_server.py
5358
$ ps -o pid,tid,wchan,state -H -p 5358
 PID    LWP WCHAN  STAT
5358 100176 sbwait Ss
```

**T5 finding, complete contrast:** real server -- `wchan=kqread`, Client 2
gets `OK` in **0.001s**. Naive server -- `wchan=sbwait`, Client 2 gets
**`None`** (no response at all) after the harness's own **5.105s** timeout.
One word (`kqread` vs `sbwait`) is the entire architectural proof: the
naive server is genuinely blocked inside a socket read syscall on Client
1's dead connection and structurally cannot reach `accept()` for Client 2,
while the real server's event loop is parked in `kevent()` and Client 1's
stall has zero effect on any other connection. This is now a real,
side-by-side measured comparison, not just an assertion.

### Raw session log — Experiment 5 (T6 complete)

**Session A — `python3 experiment.py 5`:**
```
=== Experiment 5: Multiple Clients and I/O Multiplexing (Optional) ===

Started Exchange Server (PID 5392).
     0.123ms listen           fd=3 host='127.0.0.1' port=5000 backlog=4096
Exchange Server is listening.
    75.806ms accept           sid=1 fd=5 peer='127.0.0.1:64321' nconn=1

Creating five simultaneous TCP connections...

    75.838ms eof_rst          sid=1 fd=5 errno=54 ev_fflags=54
    75.852ms close            sid=1 fd=5 why='RST' role='untyped' recvs=0 bytes_in=0 queued=0 sent=0 eagain=0 wbuf_hwm=0 orders_surviving=0
    75.868ms destroy          sid=1 fd=5 nconn=0
Client 1: connected from 127.0.0.1:35113
    75.895ms accept           sid=2 fd=5 peer='127.0.0.1:35113' nconn=1
Client 2: connected from 127.0.0.1:39310
    75.974ms accept           sid=3 fd=6 peer='127.0.0.1:39310' nconn=2
Client 3: connected from 127.0.0.1:25989
Client 4: connected from 127.0.0.1:39998
Client 5: connected from 127.0.0.1:42748

Clients 1, 3, and 5 will send application messages.
Clients 2 and 4 will remain idle.

    76.112ms accept           sid=4 fd=7 peer='127.0.0.1:25989' nconn=3
Client 1: sent application data.
    76.132ms accept           sid=5 fd=8 peer='127.0.0.1:39998' nconn=4
    76.147ms accept           sid=6 fd=9 peer='127.0.0.1:42748' nconn=5
    76.185ms recv             sid=2 fd=5 n=15 rbuf_before=0 rbuf_after=0 lines_out=1 hex='4c4f47494e20636c69656e745f310a'
    76.222ms queue_out        sid=2 fd=5 n=3 pending=3 hwm=3
Client 3: sent application data.
   576.578ms recv             sid=4 fd=7 n=15 rbuf_before=0 rbuf_after=0 lines_out=1 hex='4c4f47494e20636c69656e745f330a'
   576.707ms queue_out        sid=4 fd=7 n=3 pending=3 hwm=3
Client 5: sent application data.
  1076.953ms recv             sid=6 fd=9 n=15 rbuf_before=0 rbuf_after=0 lines_out=1 hex='4c4f47494e20636c69656e745f350a'
  1077.093ms queue_out        sid=6 fd=9 n=3 pending=3 hwm=3

The experiment is now in the observation phase.
Investigate which connections were active.
Press Ctrl-C when you are finished.
Stopping Exchange Server...
 16580.904ms signal           signo=15
```

No `$EXCH_TRACE` JSONL file was set for this run, so there's no aggregate
`kq_batch{ready_fds[]}`-style line to grep -- but the same conclusion falls
straight out of the per-event stderr trace: across the entire ~16.5s run,
`recv` is called exactly three times, on fds 5, 7, 9 (sid 2, 4, 6) --
never once on fds 6 or 8 (sid 3, 5). The event loop's readiness
notification simply never woke for the idle two, which *is* the
`ready_fds` filtering, just observed one event at a time instead of as a
single aggregated log line.

**sid ↔ Client mapping** (cross-checked two ways: by peer port against the
harness's own `Client N: connected from ...` lines, and independently by
decoding each `recv` line's `hex` payload):
- sid=2, fd=5, port 35113 = **Client 1** -- `hex` decodes to `LOGIN client_1\n` ✅
- sid=3, fd=6, port 39310 = **Client 2** -- never appears in any `recv` line
- sid=4, fd=7, port 25989 = **Client 3** -- `hex` decodes to `LOGIN client_3\n` ✅
- sid=5, fd=8, port 39998 = **Client 4** -- never appears in any `recv` line
- sid=6, fd=9, port 42748 = **Client 5** -- `hex` decodes to `LOGIN client_5\n` ✅

(Note: sid=1/fd=5 at the very top, closed by RST within 32us of accept, is
the harness's own readiness probe -- same pattern as Day 0's stub-server
verification, not one of the five numbered clients. Unrelated to T6.)

**Session B — `netstat -an -p tcp | grep '\.5000 '`, sampled 4x during the
observation window:**
```
tcp4           0      0 127.0.0.1.5000         127.0.0.1.42748        ESTABLISHED
tcp4           3      0 127.0.0.1.42748        127.0.0.1.5000         ESTABLISHED
tcp4           0      0 127.0.0.1.5000         127.0.0.1.39998        ESTABLISHED
tcp4           0      0 127.0.0.1.39998        127.0.0.1.5000         ESTABLISHED
tcp4           0      0 127.0.0.1.5000         127.0.0.1.25989        ESTABLISHED
tcp4           3      0 127.0.0.1.25989        127.0.0.1.5000         ESTABLISHED
tcp4           0      0 127.0.0.1.5000         127.0.0.1.39310        ESTABLISHED
tcp4           0      0 127.0.0.1.39310        127.0.0.1.5000         ESTABLISHED
tcp4           0      0 127.0.0.1.5000         127.0.0.1.35113        ESTABLISHED
tcp4           3      0 127.0.0.1.35113        127.0.0.1.5000         ESTABLISHED
tcp4           0      0 127.0.0.1.5000         *.*                    LISTEN
```
Identical across all 4 samples spaced through the ~16.5s observation
window -- a stable steady state, not a transient snapshot.

All five sockets sit at **ESTABLISHED** throughout, matching the log's
`nconn=5`. Reading `Recv-Q` (first numeric column) by port:
- Port 42748 (Client 5) client-side leg: `Recv-Q=3` -- the 3-byte `OK\n`
  reply the server queued at 1077.093ms, sitting unread in the *client's*
  kernel receive buffer (the harness never calls `recv()` on the reply).
- Port 25989 (Client 3) client-side leg: `Recv-Q=3` -- same story.
- Port 35113 (Client 1) client-side leg: `Recv-Q=3` -- same story.
- Port 39310 (Client 2) and port 39998 (Client 4), **both directions**:
  `Recv-Q=0`. No bytes were ever exchanged on these sockets in either
  direction -- this is the OS-level confirmation that idle really means
  idle, not "sent but unread by the app."

**T6 finding:** connected ≠ ready. All five sockets are `ESTABLISHED` for
the full run (`netstat`), but the server's event loop only ever calls
`recv()` on the three fds that actually have data (`kqread`-driven
readiness from the stderr trace), and the two idle sockets carry
`Recv-Q=0` in both directions the entire time. kqueue's per-socket,
event-driven readiness means the cost of servicing this batch is O(ready)
= O(3), not O(registered) = O(5) -- confirmed by the trace never once
touching fd 6 or fd 8.

### Raw session log — Experiment 6 (T7 complete)

**Session A — `python3 experiment.py 6`:**
```
=== Experiment 6: FIN vs. RST: Orderly and Abrupt Connection Termination ===

Started Exchange Server (PID 5457).
     0.118ms listen           fd=3 host='127.0.0.1' port=5000 backlog=4096
    75.437ms accept           sid=1 fd=5 peer='127.0.0.1:16693' nconn=1
Exchange Server is listening.

Part A: orderly connection termination (FIN).
    75.496ms eof_rst          sid=1 fd=5 errno=54 ev_fflags=54
Orderly client: connected from 127.0.0.1:63936
    75.596ms close            sid=1 fd=5 why='RST' role='untyped' recvs=0 bytes_in=0 queued=0 sent=0 eagain=0 wbuf_hwm=0 orders_surviving=0
The client will shut down its sending direction.
Investigate the TCP traffic and connection state.
    75.644ms destroy          sid=1 fd=5 nconn=0
    75.726ms accept           sid=2 fd=5 peer='127.0.0.1:63936' nconn=1
    75.759ms eof_fin          sid=2 fd=5 ev_eof=True
    75.770ms close            sid=2 fd=5 why='FIN' role='untyped' recvs=0 bytes_in=0 queued=0 sent=0 eagain=0 wbuf_hwm=0 orders_surviving=0
    75.818ms destroy          sid=2 fd=5 nconn=0

Part B: abortive connection termination (RST).
Abrupt client: connected from 127.0.0.1:50690
The client will now close abortively.
Investigate the TCP traffic and connection state.

The Exchange Server will remain running for 15 seconds.
 10076.670ms accept           sid=3 fd=5 peer='127.0.0.1:50690' nconn=1
 10076.878ms eof_rst          sid=3 fd=5 errno=54 ev_fflags=54
 10076.958ms close            sid=3 fd=5 why='RST' role='untyped' recvs=0 bytes_in=0 queued=0 sent=0 eagain=0 wbuf_hwm=0 orders_surviving=0
 10077.010ms destroy          sid=3 fd=5 nconn=0

Experiment finished.
Stopping Exchange Server...
 25078.557ms signal           signo=15
```

sid=1/port 16693 is the same standard readiness probe seen at the top of
every experiment so far (connect, then client-side RST within ~60us) --
not a Part A/B artifact.

**Session B — capture (`tcpdump -i lo0 -n -s0 -w exp6.pcap 'tcp port 5000'`)
started slightly late (after Part A had already reached `CLOSED` locally --
see the `netstat` sequence below), `netstat -an -p tcp | grep '\.5000 '`
sampled repeatedly across the ~25s run, then capture stopped and read
back:**
```
tcp4           0      0 127.0.0.1.63936        127.0.0.1.5000         CLOSED
tcp4           0      0 127.0.0.1.5000         *.*                    LISTEN
   (repeated -- 63936 entry drops out after this, LISTEN persists)
   ... (11 more identical LISTEN-only samples while waiting for Part B) ...
   (final sample: empty -- server had already received SIGTERM and exited
   by the time this call ran, ~25s into the experiment)
```
Two things worth calling out precisely:
- **Port 63936 (Part A, FIN) never showed `TIME_WAIT`** in any sample --
  it went straight from a state we caught as `CLOSED` to gone. This is the
  same fast-clearance pattern flagged in the Exp 2/3 redo notes and ties
  directly into the `net.inet.tcp.nolocaltimewait` question -- **now
  checked and refuted** (`sysctl` returns `0` on this VM, so that specific
  fast-clear mechanism cannot be the explanation; see the "Open questions"
  section below for the honest framing to use in the report).
- **Port 50690 (Part B, RST) never appeared in any of the ~13 samples at
  all**, despite polling repeatedly right around when Part B fires
  (t=10077ms). That absence *is* the finding: an RST tears down both
  ends' state immediately, with no `CLOSE_WAIT`/`TIME_WAIT` window wide
  enough to ever be caught by a polling `netstat`, unlike Part A's orderly
  close which at least left a momentarily-observable `CLOSED` entry.

**tcpdump readback (`tcpdump -r exp6.pcap -n -ttt -S`), annotated:**
```
127.0.0.1.10592 > 127.0.0.1.5000: Flags [S] ...
127.0.0.1.5000  > 127.0.0.1.10592: Flags [R.] ...win 0        <- pre-bind
                                                                  probe, refused
                                                                  (no listener
                                                                  yet -- no
                                                                  accept trace
                                                                  line exists
                                                                  for this one;
                                                                  OS-level
                                                                  ECONNREFUSED,
                                                                  not server code)

127.0.0.1.16693 > 127.0.0.1.5000: Flags [S] ...
127.0.0.1.5000  > 127.0.0.1.16693: Flags [S.] ...
127.0.0.1.16693 > 127.0.0.1.5000: Flags [.] ...
127.0.0.1.16693 > 127.0.0.1.5000: Flags [R.] ...              <- sid=1 readiness
                                                                  probe (client-
                                                                  initiated RST)

127.0.0.1.63936 > 127.0.0.1.5000: Flags [S] ...
127.0.0.1.5000  > 127.0.0.1.63936: Flags [S.] ...
127.0.0.1.63936 > 127.0.0.1.5000: Flags [.] ...
127.0.0.1.63936 > 127.0.0.1.5000: Flags [F.] ...               <- PART A: client
                                                                   half-closes
                                                                   (shutdown
                                                                   SHUT_WR)
127.0.0.1.5000  > 127.0.0.1.63936: Flags [.] ...                  server ACKs
127.0.0.1.5000  > 127.0.0.1.63936: Flags [F.] ...               <- server sends
                                                                   its OWN FIN
                                                                   right back --
                                                                   confirms the
                                                                   server treats
                                                                   EOF as FULL
                                                                   teardown, not
                                                                   a half-close
                                                                   (it does not
                                                                   keep trying to
                                                                   write/drain
                                                                   wbuf first)
127.0.0.1.63936 > 127.0.0.1.5000: Flags [.] ...                   client ACKs --
                                                                   full 4-way
                                                                   close, client
                                                                   sends the
                                                                   final ACK

  [10s gap]

127.0.0.1.50690 > 127.0.0.1.5000: Flags [S] ...
127.0.0.1.5000  > 127.0.0.1.50690: Flags [S.] ...
127.0.0.1.50690 > 127.0.0.1.5000: Flags [.] ...
127.0.0.1.50690 > 127.0.0.1.5000: Flags [R.] seq ... win 0     <- PART B: single
                                                                   RST segment,
                                                                   no FIN
                                                                   exchange at
                                                                   all, no ACK
                                                                   needed --
                                                                   connection is
                                                                   just gone
```

**T7 artifact -- FIN vs RST comparison matrix:**

| aspect | Part A (FIN, `shutdown(SHUT_WR)`) | Part B (RST, `SO_LINGER{1,0}`+`close()`) |
|---|---|---|
| wire flags | `[F.]` client, `[.]` ack, `[F.]` server, `[.]` ack -- full 4-way close | single `[R.]`, no FIN exchange, no ack needed |
| `recv()` result | returns `b''` (clean EOF) | raises `ConnectionResetError` |
| exception type | none -- EOF is not an exception path | `ConnectionResetError` |
| errno | n/a | 54 (`ECONNRESET`) |
| `ev.flags`/`ev.fflags` | `ev_eof=True`, no error in fflags | `ev_fflags=54` -- socket error already visible on `KQ_FILTER_READ`, *before* `recv()` is ever called |
| server trace event | `eof_fin{ev_eof=True}` -> `close(why='FIN')` | `eof_rst{errno=54, ev_fflags=54}` -> `close(why='RST')` |
| server state after | connection destroyed cleanly, `nconn` decremented, no other client affected | connection destroyed immediately via the same layered `except`, no other client affected (§4.4 holds) |
| `TIME_WAIT`? | not observed in polling (see nuance below); no data was in flight either way | never observed -- RST bypasses the whole close handshake, so there is no wait state for a poller to ever catch |
| in-flight data preserved? | n/a here -- both parts sent 0 bytes (`bytes_in=0`), pure lifecycle test; per the roadmap, RST *would* discard any queued/unacked data in both directions where FIN would not |

**Nuance worth keeping for the report (per roadmap §8.6):** in Part A the
client only closed its *write* side (`shutdown(SHUT_WR)`) and, per TCP
semantics, could still legally receive for as long as the server kept the
connection open. But the tcpdump readback shows the **server immediately
sends its own FIN back** rather than staying half-open -- i.e. this
server's EOF handling is "recv() returns empty -> tear the whole
connection down," not half-close-aware (it does not attempt to drain
`wbuf` and keep the read-closed/write-open socket alive). That is a
documented architectural choice, not a bug, and directly explains why a
full 4-way close is visible for Part A instead of a lingering half-open
socket.

**Precision-only-with-kqueue observation (the "genuinely sharp" one from
the roadmap):** both the sid=1 readiness-probe RST *and* Part B's RST show
the identical `ev_fflags=54` signature -- FreeBSD surfaces the socket
error directly in `ev.fflags` on `KQ_FILTER_READ`, so the RST-vs-FIN
distinction is available at the *readiness-notification* level, before a
single byte is ever read. `eof_fin` never carries a errno/fflags value in
this trace; `eof_rst` always does. That asymmetry, visible purely in the
stderr trace with no `recv()` call needed to explain it, is exactly the
kqueue-specific evidence T7 asks for.

### Raw session log — Experiment 8 (T10 in progress) + supplementary T11 test (complete)

**Part 1 -- harness `python3 experiment.py 8` (T10).** Full trace summarized
(the actual run had ~150 repeated `recv`/`queue_out` lines for the
buyer/seller trading loop -- omitted here except the boundary events):

- sid=1/port 53095: standard readiness probe, not a tracked client.
- sid=2/fd=5/port 48653: **Surviving Market-Data Client**, subscribes to
  JNST (`hex` decodes to `SUBSCRIBE JNST\n`).
- sid=3/fd=6/port 54619: **Buyer Trader** (`LOGIN experimenter_buyer`).
- sid=4/fd=7/port 59156: **Seller Trader** (`LOGIN experimenter_seller`).
- sid=5/fd=8/port 49686: **the Market-Data client that gets killed**, also
  subscribes to JNST.

Buyer/seller then trade JNST repeatedly (`BUY JNST 1 238` / `SELL JNST 1
238` on a loop), each cycle producing `BOUGHT` to sid=3, `SOLD` to sid=4,
and `TRADE` broadcast to both sid=2 and sid=5 (both MD subscribers).

At **3882.042ms**, the harness's own narration reads "The Market-Data
Client process will now disappear unexpectedly":
```
  3882.042ms eof_fin          sid=5 fd=8 ev_eof=True
  3882.304ms close            sid=5 fd=8 why='FIN' role='untyped' recvs=1 bytes_in=15 queued=343 sent=343 eagain=0 wbuf_hwm=17 orders_surviving=0
  3882.502ms destroy          sid=5 fd=8 nconn=3
```
`orders_surviving=0` confirms the killed connection is exactly the gap the
roadmap names -- a Market-Data client owns no orders, so this run alone
cannot exercise §2.6's order-survival clause (that's what the
supplementary test below is for).

**After the kill, trading continues** for another ~4.4s (up to
8314.289ms) with the same `recv`/`queue_out` pattern for sid=3/sid=4/sid=2
-- **sid=5 never appears again in the trace, at all, not even as a
`notify_dropped` target.** That is itself informative, and different from
what T11 shows below: the teardown path (`kill()`, per the roadmap's own
pseudocode) actively removes a departing connection from `subs[instr]` at
close time (`subs[i].discard(c.sid)`), so a later `TRADE` broadcast simply
never iterates over sid=5 again -- there is no dangling notify attempt to
log as dropped. This is the opposite of what happens to a Trader's resting
*order* (see T11): orders are deliberately left untouched in the book, so
a later match *does* still try to notify the departed owner and *does*
produce a logged `notify_dropped`. Worth stating explicitly in the report:
subscription cleanup is immediate and silent; order survival is
deliberate and produces an observable drop on the next match.

**Session B tcpdump (`exp8.pcap`, in `~`, not `~/col334_a2` -- note for
next time), read back with `tcpdump -r exp8.pcap -n -ttt -S`:** confirms
the accept/handshake and `SUBSCRIBE`/`LOGIN` pushes for all five
connections, and the steady stream of `TRADE`/`BOUGHT`/`SOLD` pushes each
trading cycle. Isolating port 49686 (`| grep 49686`) gives the killed
client's full wire history:
```
... (handshake, SUBSCRIBE JNST, then ACKs to each TRADE push, as expected) ...
127.0.0.1.49686 > 127.0.0.1.5000: Flags [.], ack 4046884455, win 320, ...   <- last ACK of a TRADE push
127.0.0.1.49686 > 127.0.0.1.5000: Flags [F.], seq 2006219560, ack 4046884455, ...   <- SIGKILL'd process's FIN
127.0.0.1.5000  > 127.0.0.1.49686: Flags [.], ack 2006219561, ...                    server ACKs
127.0.0.1.5000  > 127.0.0.1.49686: Flags [F.], seq 4046884455, ack 2006219561, ...   server sends its own FIN back
127.0.0.1.49686 > 127.0.0.1.5000: Flags [.], ack 4046884456, ...                     client ACKs -- full 4-way close
```
This is the exact same shape as Exp 6 Part A's close (client FIN -> server
ACK -> server's own FIN -> client ACK), and confirms the roadmap's
prediction word for word: **SIGKILL is indistinguishable from a clean
`close()` on the wire.** The kernel closes the dead process's file
descriptors on exit, which for a TCP socket means a normal FIN -- there is
no "the process died" signal at the protocol level, and the server's
`eof_fin{ev_eof=True}` trace (not `eof_rst`) is fully consistent with what
the capture shows. No `BrokenPipeError`/EPIPE(32) scenario appears in this
run either, because the server never attempted a `send()` to sid=5 again
after the FIN was detected -- detection (via `kevent()` readiness) beat
any further write attempt, so the "write-after-death produces RST/EPIPE"
branch the roadmap mentions as *possible* simply didn't get exercised
here; that would require a write to be in flight at the exact moment of
the kill, which is a timing race this run didn't happen to hit.

**T10 finding, complete:** the harness's `SIGKILL` on the Market-Data
client produces a normal FIN on the wire (confirmed above) and an
`eof_fin` in the server trace at 3882.042ms -- detection is purely
reactive, learned only on the next `kevent()` readiness notification for
that fd, exactly as the roadmap predicts. `orders_surviving=0` at close
time correctly reflects that a Market-Data client owns no orders.
Post-kill, the buyer and seller keep trading normally and the surviving
Market-Data client (sid=2) keeps receiving `TRADE` broadcasts without
interruption -- one client's death has zero effect on the others (§4.4
holds). No `notify_dropped` appears for sid=5 anywhere after the kill,
which is the correct behavior for a *subscriber* (see the `subs[instr]`
discard note above) and is the direct contrast to T11's Trader case, where
a resting *order* is deliberately left in the book and does produce a
`notify_dropped` on the next match.

**Part 2 -- supplementary test (T11), the gap the harness itself doesn't
cover.** Manual test against a freshly-started server
(`EXCH_TRACE=trace_t11.jsonl ./server/run-server 127.0.0.1 5000`), a
Market-Data subscriber (`./client/run-market-data 127.0.0.1 5000 JNST`),
and two `nc` clients (FreeBSD base `nc` needs `-N -w 2`, not GNU netcat's
`-q1`, to send FIN after stdin EOF and not hang waiting to read).

Trader A logs in, rests a BUY, disconnects cleanly (FIN via `nc`):
```sh
printf 'LOGIN trader_a\nBUY JNST 100 238\n' | nc -N -w 2 127.0.0.1 5000 > trader_a_out.log
```
Server confirms the resting order survives the disconnect:
```
162192.955ms close            sid=2 fd=7 why='FIN' role='untyped' recvs=1 bytes_in=32 queued=20 sent=20 eagain=0 wbuf_hwm=17 orders_surviving=1
```
`orders_surviving=1` -- exactly the T11 precondition the harness's own Exp
8 (killing a Market-Data client) cannot produce.

Trader B logs in, crosses the resting order:
```sh
printf 'LOGIN trader_b\nSELL JNST 60 238\n' | nc -N -w 2 127.0.0.1 5000 > trader_b_out.log
```
Server:
```
167024.123ms notify_dropped   target_sid=2 msg=b'BOUGHT JNST 60 238\n'
167024.306ms close            sid=3 fd=7 why='FIN' role='untyped' recvs=1 bytes_in=32 queued=37 sent=37 eagain=0 wbuf_hwm=17 orders_surviving=0
```

All three client-side observations line up exactly with the prediction:
```
trader_a_out.log:  OK / ORDER_ACCEPTED 0                       (no BOUGHT -- connection was already gone)
trader_b_out.log:  OK / ORDER_ACCEPTED 1 / SOLD JNST 60 238     (delivered normally, trader_b was connected throughout)
md_out.log:        [sent] SUBSCRIBE JNST / OK / TRADE JNST 60 238   (broadcast reached the still-connected subscriber)
```

**T11 finding, complete:** a Trader's resting order genuinely outlives its
TCP connection. Disconnecting via clean FIN leaves the order in the book
(`orders_surviving=1` at close time, order object untouched -- per the
roadmap, `Order.owner_sid` is an `int`, not a `Conn`). When a later order
crosses it, the match executes in full: the trade completes, `SOLD`
reaches the surviving counterparty, `TRADE` reaches the subscribed
Market-Data client, and only the departed buyer's own `BOUGHT`
notification is silently dropped (`notify_dropped{target_sid=2, ...}`)
because `by_sid` no longer has an entry for that sid. This is precisely
the graded behavior the roadmap flags as untested by the harness itself.

### Known pitfalls (still applicable — read before re-running)

- **Never pre-start the server before an `experiment.py N` invocation.**
  `experiment.py` manages the server's lifecycle itself (launches its own
  `run-server` subprocess). Manually starting one first causes a port
  collision (`OSError: [Errno 48] Address already in use`) on the harness's
  own subprocess, producing a contaminated run with a stray traceback.
- **FreeBSD's `pgrep -f` uses POSIX extended regex** — an escaped pipe like
  `'run-server\|server.py'` is read literally and matches nothing. Use an
  unescaped `'run-server|server.py'`, or just read the PID off `sockstat`'s
  own output.
- **`tcpdump` must be started before** the connection you want to capture —
  starting it while a previous run's connection is still open/idle will mix
  that leftover teardown into the new capture. Confirm any previous
  `experiment.py` session is fully finished (Ctrl-C'd, port released via
  `sockstat -4 | grep 5000` coming back empty) before starting a fresh
  capture.
- `EXCH_NAIVE=1 python3 experiment.py 4` runs the naive blocking-server
  control (`src/naive_server.py`, gated in `server/run-server`) — throwaway,
  non-submission code, written specifically for the T5 contrast. Verified
  locally (Mac + this session's sandbox) before ever touching the VM:
  Client 2 gets zero response within a 2s timeout when stalled behind
  Client 1, confirming the predicted head-of-line block.

## Day 3 — Exp 7 (the centrepiece)

- [ ] Run A (stock buffers) — record whether backpressure appeared at all
- [ ] Run B (reduced buffers per §5) — record the predicted vs actual onset

---

## Artifact tracker

Fragmentation (§2.9 / §4.2 / Exp 3)

| id | what | status | file(s) |
|---|---|---|---|
| F1 | tcpdump: 4 segments 6/10/7/1 bytes | ✅ | Exp 3 redo, clean single-cycle capture: 6/10/7/1 bytes, ~202-204ms gaps — see raw session log |
| F2 | stderr trace: 4 recv, 3 frame_partial, 1 emitted | ✅ | `frame_partial`/`frame_complete` don't fire post-Phase-3-rewiring (by design); `recv`'s own `rbuf_after` (0→6→16→23→0) + single `queue_out` after the 4th recv is the equivalent, cleaner evidence |
| T3 | recv-call ledger (table) | ✅ | Exp 3 redo: n=6,10,7,1, rbuf_after=6,16,23,0, lines_out=0,0,0,1 — exact match to predicted ledger |
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
| T1 | listening vs connected socket table | ✅ | Exp 1 redo, real VM run: fd=3 LISTEN `127.0.0.1:5000`/`*:*` vs fd=5 (sid=2) ESTABLISHED `127.0.0.1:5000`/`127.0.0.1:28907` — see raw session log |
| T2 | TCP state timeline, CLOSE_WAIT transient | ✅ | Exp 2 redo: CLOSE_WAIT 609us (3rd consistent sub-ms measurement across separate runs) — see raw session log |
| T5 | Exp 4 correct vs naive control | ✅ | Exp 4 redo, both halves: real server wchan=kqread/0.001s vs naive wchan=sbwait/None-after-5.105s — see raw session log |
| T6 | Exp 5 ready vs idle fds | ✅ | recv() called only on sid 2/4/6 (Clients 1/3/5) across the whole run; netstat confirms Recv-Q=0 both directions for Clients 2/4 -- see raw session log |
| T7 | FIN vs RST matrix | ✅ | Exp 6, both parts: FIN = 4-way close (server EOF triggers full teardown, not half-close), RST = single segment no handshake; `ev_fflags=54` present only on RST -- see raw session log + comparison table |
| T10 | Exp 8 timeline + TRADE counts | ✅ | SIGKILL'd MD client (sid=5): `eof_fin` at 3882.042ms, `orders_surviving=0`, confirmed clean 4-way FIN close on the wire (`tcpdump | grep 49686`); buyer/seller/surviving MD client unaffected -- see raw session log |
| T11 | §2.6 order-survival: close→orders_surviving>0→later notify_dropped | ✅ | supplementary manual test: `close{orders_surviving=1}` for trader_a's disconnect, later `notify_dropped{target_sid=2, msg=BOUGHT...}` on the crossing trade; SOLD/TRADE delivered normally to survivors -- see raw session log |
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
- **RESOLVED (hypothesis refuted) — fast/absent `TIME_WAIT` on loopback.**
  `sysctl net.inet.tcp.nolocaltimewait` on the VM returns **`0`**, i.e. the
  fast-clear optimization is NOT enabled (its default is commonly cited as
  `1`, but this VM's build/config has it off). That kills the leading
  hypothesis outright: this VM cannot be skipping `TIME_WAIT` for loopback
  connections via that sysctl, because the knob is off. Yet Exp 2/3's
  CLOSE_WAIT numbers and Exp 6 Part A's port 63936 both still show no
  observable `TIME_WAIT` window in polled `netstat` output, and `msl=30000`
  would imply a 60s hold if a real TIME_WAIT were occurring. **Do not state
  a specific mechanism in the report** — the honest, defensible framing is:
  "we observed no `TIME_WAIT` entry for the loopback connections we polled,
  despite confirming `nolocaltimewait=0`; the mechanism is not fully
  explained by our measurements, and a `netstat`/`sockstat` sampling
  interval on the order of seconds may simply be too coarse relative to
  the actual state transition to rule out a very brief real TIME_WAIT vs.
  none at all." This is a case where reporting the honest, partially-
  unexplained result is stronger than force-fitting a wrong sysctl
  explanation -- and it's a legitimate thing to raise proactively in the
  viva as a limitation you identified yourself rather than one that would
  be caught.

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
- Git history: a baseline commit exists capturing Phase 1-3 + the naive
  control, taken specifically so a bad step during robustness testing never
  costs more than one experiment's redo — worth mentioning as evidence of
  disciplined process if the viva asks about workflow/tooling.
