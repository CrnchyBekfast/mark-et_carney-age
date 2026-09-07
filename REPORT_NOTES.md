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
- [x] Exp 8 (+ supplementary disconnect-then-match test) — T10 and T11 both
      done (see raw session log below)

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

### Pre-Exp-7 prep (code + environment, not experiment findings)

- **Added `engine{sid, n_msgs, engine_ns}` instrumentation to
  `handle_readable_bytes()`** (`src/server.py`), timing the
  `self.engine.handle(c.sid, payload)` call itself with
  `time.monotonic_ns()` before/after, separate from the write path. This
  did not exist before Exp 7 -- `queue_out{pending, hwm}` and
  `eagain`/`wbuf_hwm` were already instrumented from Day 1, but nothing
  measured engine latency directly, and **P2 (flat engine latency during
  the backpressure ramp) is the single most important figure in the whole
  experiment** -- it's the evidence that TCP backpressure structurally
  cannot reach the matching engine. Verified firing correctly via a
  smoke-test rerun of Exp 3 (`engine{sid=2, n_msgs=1, engine_ns=27042}`
  appeared exactly where expected) before starting Exp 7 proper, so both
  Run A and Run B have this instrumentation present from their first
  message, keeping the two runs comparable.
- **Fixed a `pytest` import-path issue**, unrelated to the above but
  surfaced by the same verification pass: `tests/test_all.py` does
  `from framing import Framer` with no `sys.path` manipulation anywhere,
  and `pytest.ini` had no `pythonpath` setting either, so
  `python3 -m pytest tests/test_all.py` failed at collection with
  `ModuleNotFoundError: No module named 'framing'` when invoked from the
  repo root. Added `pythonpath = src` to `pytest.ini` (supported natively
  since pytest 7.0; confirmed both the Mac's pytest 8.4.2 and the VM's
  9.1.1 handle it). Re-ran after the fix: **29 passed** on both machines.
  Purely a test-runner plumbing fix -- no test content changed, and this
  was never a regression in the actual server code (`experiment.py 3` ran
  clean throughout, proving the code itself was fine the whole time).

- [x] Run A (stock buffers) — complete, no backpressure observed (see raw
      session log below) -- this is the predicted, reportable outcome
- [x] Run B (reduced buffers per §5) — **null, and not for the predicted
      reason**; see Day 4
- [x] Run C — aborted, `kern.ipc.maxsockbuf` incident; see Day 4 (kept as a
      methodology finding, not discarded)
- [x] Run D (TCP-specific ceilings capped + `EXCH_SNDBUF=1024`) — still
      null; this is the run that falsified the capacity model
- [x] Run E (volume-forced, `exp7_load.py`) — **positive: real onset,
      P1/P2/P3 evidence**
- [x] Standalone isolation (`sb_probe.py`) — the buffer-enforcement finding,
      with a Linux control

### Raw session log — Experiment 7, Run A (stock buffers)

**Setup:** `EXCH_TRACE=exp7a_trace.jsonl python3 experiment.py 7 2>&1 | tee
exp7a_console.log`, `tcpdump -i lo0 -n -s0 -w exp7a.pcap 'tcp port 5000'`
running throughout. Server PID found via `sockstat -4 | grep 5000` (no
`Started Exchange Server (PID ...)` line for this experiment, unlike
Exps 1-6/8) -- PID 6121 owns the listening socket + all four server-side
connection legs; PID 6119 is the harness's own driver process holding the
client-side legs.

**Connections** (sid=1/port 14729 is the usual readiness probe, RST'd in
~60us -- not a tracked client):
- sid=2, fd=7, port 20370 -- Market-Data client, **actively reading**
- sid=3, fd=6, port 37573 -- Market-Data client, **the slow one, never reads**
- sid=4, fd=8, port 21368 -- Buyer Trader
- sid=5, fd=9, port 52518 -- Seller Trader

**How slow vs normal was actually identified:** not from anything the
server did differently (it can't tell -- both MD clients received
identical `TRADE` broadcasts) but from four `netstat -an` samples taken
across the run (~30%, ~50%, ~70%, ~end):

| sample | sid=2 (port 20370) client-side Recv-Q | sid=3 (port 37573) client-side Recv-Q |
|---|---|---|
| ~30% | 17 | 29206 |
| ~50% | 0 | 45390 |
| ~70% | 0 | 65008 |
| ~end | 36 | 82535 |

sid=3's Recv-Q climbs steadily and ends within ~2.5KB of its entire
cumulative feed (85003 B, below) -- it is never being drained by the
client. sid=2 stays near zero the whole run -- it's reading promptly. All
four samples: `ps -o pid,tid,wchan,state -H -p 6121` -> **`kqread`**,
every single time, including while sid=3's backlog was near its maximum
(**W1**).

**Cumulative bytes queued per connection** (reconstructed by summing every
`queue_out{n=...}` for that sid, since this experiment's clients never
disconnect -- the server just gets `SIGTERM`'d at the end like every other
experiment, so there is no per-connection `close` line to read a final
tally from):

| sid | role | cumulative queued (B) | final hwm (B) |
|---|---|---|---|
| 2 | MD, normal | 85003 | 17 |
| 3 | MD, slow | 85003 | 17 |
| 4 | Buyer | 189448 | 20 |
| 5 | Seller | 179448 | 20 |

Both MD clients: **85003 B total, matching B1's pre-flight 85000 B
estimate almost exactly** -- direct confirmation the prediction arithmetic
was right. `grep -c 'engine '` -> **10004** engine calls total, consistent
with ~5002 completed trades × 2 (`BUY` rests, then `SELL` matches it -- two
`engine.handle()` calls per completed trade), again matching the roadmap's
5000-trade estimate.

**T9 counters:** `send_would_block` count = **0** for the entire run. Both
MD clients' server-side `wbuf` high-water mark never exceeded **17
bytes** -- i.e. the server's userspace write buffer essentially never held
anything beyond a single in-flight message, for either the slow or the
normal client. Since `eagain=0` throughout, **queued == sent** for every
connection -- nothing ever got stuck in `wbuf` waiting for a socket to
become writable.

**F3 (tcpdump `win 0` search) -- negative result, as predicted:**
```sh
tcpdump -r exp7a.pcap -n -ttt -S | grep -i 'win 0'
```
returned exactly two hits, both `Flags [R.]` RST packets from readiness
probes (`win 0` there is just an RST artifact, not a flow-control
advertisement) -- **no genuine zero-window advertisement or persist-timer
probe occurred anywhere in this run.**

**P1/P3 (pending bytes / cumulative queued vs sent):** effectively flat
and near-identical for both MD clients at the server-side `wbuf` level
(hwm=17 for both) -- the divergence between "slow" and "normal" shows up
*only* in the client-side kernel `Recv-Q` (table above), never in
anything the server's userspace buffering has to deal with.

**P2 (engine latency):** 10004 calls, **mean 49.4us, min 3.4us**. Ten
calls reached millisecond scale (5.08ms down to 1.6ms, strictly
decreasing when sorted) -- consistent with one-time interpreter/OS
warm-up cost on the earliest calls (first dict/deque growth for the order
book, scheduler noise), not with sustained load: none of these coincide
with any backpressure event, since none occurred in this run. The
overwhelming majority of calls sit in the low tens of microseconds
regardless of what sid=3's growing backlog was doing at the kernel level.

**Run A finding, complete:** at stock socket-buffer settings
(`sendspace=32768`/`recvspace=65536` with auto-tuning), **no backpressure
reaches the server at all.** The server's `send()` never returns
`EWOULDBLOCK`, `wbuf` never accumulates, and the engine's latency
distribution is indistinguishable from an unloaded system. This is the
outcome B1's pre-flight arithmetic predicted (85000 B feed vs ~96-98 KB
effective stock capacity).

> **CORRECTION (added Day 4 -- do not delete this note, it is the honest
> version).** This section originally went on to assert a mechanism: that
> `recvbuf_auto=1` let the slow client's receive buffer grow past its
> nominal default rather than ever closing its window. **That claim was
> subsequently refuted by direct measurement** and has been removed. Run E
> shows the slow socket's `SO_RCVBUF` pinned at 81,720 for the entire
> 11.35 MiB it absorbed -- autotuning never fired at all -- and
> `sb_probe.py` shows a receiver with `SO_RCVBUF` *explicitly* pinned to
> 8192 absorbing 67 MB, 8,192x its own buffer. The capacity model that
> generated the correct *prediction* for Run A turns out not to be the
> operative *mechanism* on this path. Run A alone cannot distinguish the
> two explanations, because both predict the same null result; Runs B and
> D could, and falsified the capacity model. See "Day 4" below.


## Day 4 — Exp 7 continued: Runs B–E and the buffer-enforcement finding

**In one paragraph.** Run B (reduce the buffers, per roadmap §5) produced
results indistinguishable from Run A. Three further runs plus a standalone
reproducer established why: **on this FreeBSD loopback path, a non-reading
TCP receiver's socket-buffer limits are not enforced against the sender at
all.** Backpressure cannot be produced by shrinking buffers, at any setting.
It can be produced by volume. Run E did so, and yielded the real P1/P2/P3
evidence.

### Run B (reduced buffers) — null, and not for the predicted reason

`recvspace=8192 sendspace=4096 recvbuf_auto=0 sendbuf_auto=0`. Pre-flight
predicted capacity ~12 KB, onset at message ~723 of 5000.

Observed: `send_would_block` = **0**. `wbuf_hwm` = 17 B for both MD clients.
Cumulative queued 85,003 B each. No genuine `win 0`. **Every headline number
identical to Run A**, despite an 8x buffer reduction. The slow client's
`Recv-Q` climbed to ~85,000 while `netstat -x` reported `R-HIWA=8192` for
that same socket.

### Run C — aborted (keep this; it is a real methodology finding)

Attempted with `kern.ipc.maxsockbuf=65536` added to the sysctl block. That
knob is the **global** ceiling for every socket on the machine, Unix-domain
sockets included. Lowering it starved `libcasper` (`sockstat` began failing
with `Unable to contact Casper: No buffer space available`) and then sshd
itself, which stopped completing handshakes
(`kex_exchange_identification: Connection reset by peer`). Recovered from the
VM's own console with `sysctl kern.ipc.maxsockbuf=8388608`; the run was
discarded.

**Report this in the methodology section.** `net.inet.tcp.*` ceilings are
TCP-specific and safe to tune for an experiment; `kern.ipc.maxsockbuf` is
global and is not. It is also a good illustration of why the baseline
capture (B1) matters: recovery meant knowing the original value.

### Run D — TCP ceilings capped properly, per-socket `SO_SNDBUF` — still null

Only TCP-specific sysctls this time: `recvbuf_max=16384 sendbuf_max=16384
recvspace=8192 sendspace=4096 recvbuf_auto=0 sendbuf_auto=0`, plus
`EXCH_SNDBUF=1024` (a per-socket `setsockopt` at accept, which additionally
clears `SB_AUTOSIZE` for that sockbuf -- something no sysctl does).
Predicted capacity 1024 + 16384 = 17,408 B, i.e. onset at message ~1024 of
5000.

**The knobs demonstrably took effect.** The accept trace (new this run --
see "Code and tooling" below) records `getsockopt` values per connection:
every socket shows `SO_SNDBUF=1024`, `SO_RCVBUF=8192`. `netstat -x` confirms
`S-HIWA=1024` on every server-side leg.

And still: `send_would_block` = **0**, `wbuf_hwm` 17/17/20/20, no genuine
`win 0`. The slow client's `Recv-Q` climbed monotonically across seven
samples -- 12,223 → 23,409 → 40,188 → 52,921 → 66,487 → 79,492 → **83,266**
-- against its own `R-HIWA` of 8192. A ~10x overshoot, measured on a freshly
created connection whose provenance is unambiguous.

**W1:** `wchan` = `kqread` on every sample.

### The `netstat -x` column question, settled

`netstat -x -p tcp | head -3` finally produced the header row (the first line
is only a banner, which is why earlier attempts with `head -1` missed it):

```
Proto  Recv-Q Send-Q Local Address  Foreign Address  R-HIWA S-HIWA R-LOWA S-LOWA R-BCNT S-BCNT R-BMAX S-BMAX  rexmt persist keep 2msl delack rcvtime
```

- `Recv-Q`/`Send-Q` = `sb_cc`, the actual queued **data** bytes. **This is
  the queue-depth number to quote.**
- `R-BCNT`/`S-BCNT` = `sb_mbcnt`, mbuf **memory** accounting, inflated ~3x
  because `TCP_NODELAY` gives every 17-byte TRADE its own segment and mbuf.
- `R-BMAX` = `sb_mbmax` = **8 x `R-HIWA` exactly** -- verified on three
  independent rows: 65,700x8 = 525,600 (the ssh session), 8,192x8 = 65,536,
  4,096x8 = 32,768. The 8 is `kern.ipc.sockbuf_waste_factor`.

Earlier working notes that read `R-BCNT` as occupancy were wrong. `Recv-Q`
is the correct field.

Also settled: `lo0` MTU is 16384, so MSS = 16,344, and the observed default
`R-HIWA` of **81,720 = exactly 5 x MSS**. FreeBSD rounds the receive buffer
to a whole number of maximum segments.

### Run E — volume-forced; the run that produced the real evidence

Design change: stop trying to shrink the target, and overwhelm it instead.
Any absorption capacity has *some* ceiling; offered volume has none.
`src/tools/exp7_load.py` pipelines matched pairs with batched writes and no
per-trade drain (the harness's own ~850 B/s pacing is what made Runs A–D
incapable of reaching any ceiling), draining the trader sockets continuously
so they cannot become a second slow consumer, and never reading the
subscriber.

700,000 pairs, `EXCH_SNDBUF=2048 EXCH_WBUF_MAX=8388608`, **stock sysctls
throughout -- no tuning at all**. Offered to the non-reading subscriber:
700,000 x 17 = 11,900,000 B (trace records 11,900,003).

`src/tools/exp7_report.py exp7e_trace.jsonl`:

```
event counts:  queue_out 3,500,003 · engine 1,400,003 · send_would_block 66
               accept 3 · close 3 · write_enable 1 · write_disable 1
buffers in force at accept (getsockopt): all three SO_SNDBUF=2048, SO_RCVBUF=81,720
P1 onset:      first send_would_block at t = 93,102.525 ms, sid=3
               (534,524 engine calls had already completed)
```

| sid | role | queued (B) | TRADEs | `wbuf_hwm` (B) | eagain |
|---|---|---|---|---|---|
| 1 | MD subscriber, **never reads** | 11,900,003 | 700,000 | 17 | 0 |
| 2 | buyer | 28,151,893 | 10 | 23 | 0 |
| 3 | seller | 26,737,003 | 0 | **1474** | **66** |

All three closed with `why=FIN` and **`queued == sent` exactly**.

**Which connection backed up, and why it was not the subscriber.** sid=1 is
the non-reading subscriber; sid=2/3 are the traders. Backpressure landed on
sid=3, the seller. `exp7_load.py` writes in batches of 500 order lines and
drains the traders only after each full batch, so the server's replies
(`ORDER_ACCEPTED` per order, then a burst of `SOLD`/`BOUGHT` as the SELLs
sweep) can briefly outrun the generator's read loop. 66 events across
700,000 pairs (~0.01%) is a rare, self-correcting stall. **It is genuine
backpressure, correctly detected and correctly handled -- but it is a
property of the load generator's write/drain interleaving, not of a
deliberately slow consumer. Say so plainly in the report; do not dress it up
as the textbook slow-subscriber case.**

**P1 / P3 / T9, stated honestly.** `wbuf_hwm` 1474 B on sid=3 against 17 and
23 on the others *is* the per-connection isolation result: the backlog is
charged to the connection that caused it and to no one else. But
`queued == sent` at close for all three, so the backlog was transient and
fully drained -- **there is no standing `queued > sent` gap**. P3 therefore
plots the difference series, not two cumulative lines four orders of
magnitude apart (1.5 KB of backlog against 26.7 MB of traffic), which is why
`plot.py` renders it as two stacked panels sharing one x-axis.

**P2 — the strongest single result in the experiment.** `engine_ns` split at
the onset:

| window | n | p50 | p90 | p99 | p99.9 | max |
|---|---|---|---|---|---|---|
| pre-onset | 534,524 | 4.3 µs | 8.0 | 62.6 | 148.0 | 47,054 |
| post-onset | 865,479 | **3.9 µs** | 6.6 | **55.0** | 121.0 | 64,416 |

Ratios post/pre: **p50 x0.91, p99 x0.88** -- the engine ran marginally
*faster* after backpressure engaged. Throughput **6,550/s → 7,364/s**.
`write_enable` and `write_disable` each fired exactly once: the kqueue write
filter was armed when the backlog appeared and correctly disarmed when it
drained (the level-triggered spin trap the roadmap warns about did not
occur). `wchan` = `kqread` throughout (W1).

**The caveat to state rather than hide:** `engine_ns` brackets only
`engine.handle()`, so by construction it *cannot* include write-path cost.
It proves the matching engine is unaffected; it does not by itself prove the
event loop never stalled in the write path. Throughput (loop iterations per
second) and `wchan` are what cover that. The claim needs all three, and the
report should say so -- it is a stronger argument for being explicit about
what each instrument can and cannot show.

### The buffer-enforcement finding (standalone, no exchange code)

Run E's remaining oddity: sid=1 never called `recv()` and still absorbed all
11,900,003 B with `eagain=0`, while its `SO_RCVBUF` read 81,720 at connect
and 81,720 at the end -- autotuning never fired. `src/tools/sb_probe.py`
isolates this with a blasting sender and a never-reading receiver and **no
project code whatsoever**.

| | lo0, default buffer | lo0, `SO_RCVBUF` pinned 8192 |
|---|---|---|
| `SO_RCVBUF` start → end | 81,720 → 122,580 | 8,192 → **8,192** |
| bytes absorbed | 67,108,877 | 67,108,877 |
| overshoot vs buffer | 821x | **8,192x** |
| sustained stall | never | never |
| `FIONREAD` before drain | 67,108,877 | 67,108,877 |
| **actually drained** | 67,108,877 | 67,108,877 |

Both runs stopped at the tool's own `--bytes` cap, **not at any kernel
limit**. The receive buffer varied 15x between them and the absorbed byte
count is identical to the byte. `FIONREAD` -- read from inside the receiving
process, never through `netstat` -- agreed exactly with a drain-and-count, so
the data genuinely was buffered. This is not a reporting artifact.

The explicit pin *did* work for its stated purpose: `SO_RCVBUF` held at 8192
(vs 81,720 → 122,580 unpinned), confirming `setsockopt` cleared
`SB_AUTOSIZE`. It simply had no effect on what the path would accept.

Transient `EWOULDBLOCK`s were 7 and 13 in the two runs, first at 3,052,758 B
and 6,038,060 B respectively -- different counts at unrelated offsets, i.e.
scheduling noise, not flow control. Had a buffer limit been participating,
onset would be reproducible and proportional to buffer size.

**Linux control, same tool:** capacity **17,081 B** against `SO_SNDBUF` 4,608
+ peer `SO_RCVBUF` 16,384, `FIONREAD` flat at 12,985. Textbook flow control
-- and the behaviour FreeBSD is not showing on this path.

**What does bound it.** `kern.ipc.nmbclusters` 254,663 (~497 MiB),
`kern.ipc.maxmbufmem` 2,086,203,392 (~1.94 GiB), `kern.ipc.nmbufs`
1,629,846. The 67 MiB run allocated **~208 MB** of network memory
(`netstat -m`, "bytes allocated to network", total column) -- a **3.1x**
amplification, matching the `sb_mbcnt`/`sb_cc` = 3.01 ratio measured
independently in Run D. That is ~10% of the budget, with
`requests for mbufs denied 0/0/0`. Nothing was ever going to push back at
these volumes.

Proving the mbuf pool is the *operative* bound would require driving it to
exhaustion (~1.94 GiB ÷ 3.1 ≈ 640 MiB of payload). Given Run C already
demonstrated what a starved network stack does to this VM, that test was
deliberately not run: the arithmetic plus the zero-denial counters carry the
claim, and the report should say the ceiling was computed rather than
observed.

### F3, and what the wire actually shows

The `win 0` search on `exp7e.pcap` returns **1,630,569** non-RST hits, which
looks at first like overwhelming evidence of receiver flow control. It is the
opposite. Broken down by direction:

| count | direction | interpretation |
|---|---|---|
| 930,144 | `5000 > 35156` (server → buyer) | server's own receive window |
| 700,425 | `5000 > 54139` (server → seller) | server's own receive window |
| **0** | **`12398 > 5000`** (subscriber → server) | **never once** |

(930,144 + 700,425 = 1,630,569 exactly — there are no other sources.)

**The `win` field is always the sender's own receive window**, so every one of
these is the *server* advertising that *it* has no room for more inbound
data. They are `[P.]` packets carrying replies (`length 18`, `length 20`), so
the server is pushing output while simultaneously telling the traders to stop
sending orders.

**Bonus finding, worth a paragraph in the report.** This is genuine,
textbook receiver flow control — just on the inbound path rather than the one
Experiment 7 asks about. `exp7_load.py` writes 500-line batches, the server's
receive buffer for the trader sockets fills faster than the event loop drains
it, and TCP correctly closes the window to throttle the generator. It is the
mirror image of the outbound backpressure story, and it is the reason the
generator settles to a stable ~3,600 pairs/s instead of running away.

**F3 proper — the non-reading subscriber — is a negative, and a much
sharper one than "no zero window."** Its full window history:

```
SYN   12398 > 5000: Flags [S], win 65535, options [mss 16344, wscale 8, sackOK, ...]
SYN.  5000 > 12398: Flags [S.], win 65535, options [mss 16344, wscale 8, sackOK, ...]
first 12398 > 5000: Flags [.],  ack ..., win 320      <- t = 18:24:54
last  12398 > 5000: Flags [.],  ack ..., win 320      <- t = 18:28:13, 11,900,003 B later
FIN   12398 > 5000: Flags [F.], ack ..., win 320
```

With `wscale 8`, `win 320` = **320 x 256 = 81,920 bytes**, which matches the
measured `R-HIWA` of 81,720 to within one scale unit (81,720/256 = 319.2,
rounded up to 320).

**The receiver did not merely fail to send a zero window — it advertised a
constant, wide-open 81,920-byte window for the entire run.** A receiver whose
buffer is filling should shrink progressively (81,920 → 60,000 → 30,000 → 0).
This one held the same value from its first ACK to its last, across 11.9 MB
it never read. The sender was never asked to slow down, which is precisely
why `send()` never returned `EWOULDBLOCK` and why `wbuf` never accumulated
for sid=1.

**Why this is the strongest form of the K1 evidence.** The control is inside
the same capture, on the same kernel, the same loopback interface, the same
run: the server's TCP shrank its window to zero 1.6 million times when *its*
receive buffer filled. So this stack unambiguously does perform receiver flow
control. The failure is specific to the socket whose application never calls
`recv()`. That rules out "FreeBSD loopback doesn't do flow control" as an
explanation, and it makes the wire capture a **fourth independent
instrument** — alongside `netstat` `Recv-Q`, `getsockopt(SO_RCVBUF)`, and
`FIONREAD` + drain-and-count — all agreeing.

Also confirmed here: `mss 16344` in the handshake, which is what makes the
default `R-HIWA` of 81,720 exactly 5 x MSS.

### Methodology note: live instruments vs frozen artifacts

Worth stating explicitly, because "when did you take that measurement?" is a
fair viva question and the answer differs by instrument.

| instrument | kind | must be contemporaneous with the run? |
|---|---|---|
| `netstat -an` / `netstat -x`, `sockstat`, `ps -o wchan` | live kernel state | **yes** |
| `getsockopt(SO_RCVBUF)`, `FIONREAD` | live, from inside the process | **yes** |
| `.pcap`, `$EXCH_TRACE` JSONL, `tee`d console logs | frozen artifacts | **no** |

All live readings were taken during their runs: T8 is 4 samples in Run A,
7 in Run D, 5 in Run E; `wchan` alongside each; the `getsockopt`/`FIONREAD`
values are printed by `exp7_load.py` / `exp7_probe.py` / `sb_probe.py` while
the run is in progress.

The F3 analysis and every number from `exp7_report.py` come from frozen
artifacts, and were produced in a later session. **This does not weaken
them.** `tcpdump -r` reads a file: the `win` values, packet directions and
SYN options were serialised at capture time and re-reading is a pure
function of the file. It is in fact a point in their favour -- the analysis
is reproducible by anyone holding the same `.pcap`.

Two conditions that *would* have invalidated the post-hoc read, both checked:

- **Capture completeness.** Run E's capture stopped with
  `5252898 packets captured / 5252898 packets received by filter /
  0 packets dropped by kernel`. Captured equals received-by-filter with zero
  kernel drops, so nothing was missed. (Contrast the aborted Run C, where a
  `.pcap` was read *while tcpdump still had it open* and produced
  `invalid packet capture length ... bigger than snaplen` -- always stop the
  capture before reading it.)
- **File integrity.** The F3 pass walked the whole file with no truncation
  error and produced coherent head *and* tail output.

**Honest limitation:** being post-hoc, the wire analysis cannot be
cross-checked against live kernel state at the same instants. It does not
need to be -- the live `netstat` snapshots taken during Run E already agree
with it (subscriber `Recv-Q` climbing monotonically, server-side `Send-Q`
at 0), and that cross-check *was* contemporaneous.

**Snaplen caveat.** `exp7e.pcap` was captured with `-s 96`: headers only
(4 B loopback + 20 B IP + up to 60 B TCP with options = 84 <= 96), which is
why the SYN's `mss 16344, wscale 8` were readable. Payloads are truncated, so
message *contents* cannot be decoded from that file. F3 does not need them.
F4's capture uses `-s0` precisely because it does.

### How to frame all of this in the report

The pre-flight arithmetic (85,000 B feed vs 98,304 B stock capacity)
predicted Run A's null outcome **correctly**, but Runs B and D show it
predicted the right answer for the wrong reason: the capacity model is not
the operative mechanism on this path. Run A alone could not distinguish the
two explanations, since both predict a null. Runs B and D could, and
falsified the capacity model.

That is the honest account and it is a better story than "we shrank the
buffers and it worked": a stated quantitative prediction, an experiment
capable of refuting it, an actual refutation, a minimal standalone
reproducer with a cross-platform control, and a corrected model -- plus a
volume-based redesign that then produced the decoupling evidence the
experiment was for.

### Code and tooling added during Day 4

- `src/server.py` — the `accept` trace now records
  `getsockopt(SO_SNDBUF/SO_RCVBUF)` per connection, so the buffer actually in
  force is **measured**, not inferred from `netstat`. This is what made Run D
  interpretable.
- `src/server.py` — corrected three stale artefacts a grader would see: a
  module docstring still describing the file as a "Day-0 connection-layer
  stub" that "does not parse the application protocol"; a `TODO` claiming
  subscription/username cleanup was outstanding (`engine.on_disconnect` has
  always done it); and the `close` trace's `role` field, which read
  `Conn.role` -- never mutated -- so every close logged `role='untyped'`,
  including logged-in traders. **Note: the `role='untyped'` values in the
  T11 and Exp 8 raw logs above are from before this fix.**
- `src/tools/exp7_load.py` — pipelined volume generator (Run E).
- `src/tools/exp7_report.py` — trace reducer; splits every metric at the
  onset, which is what makes the P2 pre/post comparison possible.
- `src/tools/exp7_probe.py` — `FIONREAD` + drain-and-count diagnostic.
- `src/tools/sb_probe.py` — standalone blaster/victim, no exchange code.
- `src/tools/exp7_csv.py` + `src/tools/plot.py` — the P1/P2/P3 figure
  pipeline (`plot.py` did not previously exist; roadmap §9 assumed it).


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
| P1 | pending bytes vs time, per connection | ✅ data / ⬜ figure | Run E: `wbuf_hwm` 1474 B on sid=3 (backpressured) vs 17 B / 23 B on the other two — the backlog is charged to the connection that caused it. Run A adds the kernel-side contrast (slow client's `Recv-Q` → 82,535 B while the reading client stays ~0). Figure: `plot.py` → `P1_pending.png`. Note the "slow vs normal *subscriber*" framing is only available from Run A; Run E had a single subscriber. |
| P2 | engine latency flat during P1's ramp | ✅ data / ⬜ figure | **Run E, the strongest result.** Split at the onset: pre 534,524 calls p50 4.3 µs / p99 62.6 µs; post 865,479 calls p50 3.9 µs / p99 55.0 µs → ratios ×0.91 / ×0.88, i.e. marginally *faster* under backpressure. Throughput 6,550/s → 7,364/s. Figure: `P2_engine.png`. Caveat to state: `engine_ns` brackets only `engine.handle()`, so throughput + `wchan` are what cover the write path. |
| P3 | cumulative queued vs sent, backpressured sid | ✅ data / ⬜ figure | Run E sid=3: `queued == sent` at close, so the backlog is **transient, not standing** — 1474 B peak against 26.7 MB cumulative. Plotted as the difference series in a second panel (`P3_queued_sent.png`); two cumulative lines four orders of magnitude apart show nothing. |
| F3 | tcpdump win 0 + zero-window probes | ✅ | **Closed with a decisive result.** Run E: 1,630,569 `win 0` packets, but **all of them server→trader** (930,144 to sid=2, 700,425 to sid=3) — the server throttling *inbound* order flow. **Zero from the non-reading subscriber (port 12398).** Better than a bare negative: that subscriber advertised a constant `win 320` × `wscale 8` = **81,920 B, unchanged from its first ACK to its last, across 11,900,003 B absorbed unread**. It never shrank its window at all. See "F3, and what the wire actually shows" in Day 4. |
| T8 | netstat -an/-x snapshots | ✅ | Run A: 4 samples (`Recv-Q` 29,206→45,390→65,008→82,535 vs ~0 for the reading client). Run D: 7 samples (12,223→83,266 against `R-HIWA`=8192). Run E: 5 samples. Column semantics settled — see "The `netstat -x` column question" in Day 4. |
| T9 | counters, null run vs positive run | ✅ | **Axis reframed**: the original "Run A vs Run B" contrast is void, because buffer-shrinking changes nothing on this path (Runs B and D both null). The real contrast is harness-scale/null (A, B, D: `send_would_block`=0, hwm 17 B, 85,003 B offered) vs volume-forced/positive (E: `send_would_block`=66, hwm 1474 B, 11.9 MB offered to one subscriber). |
| W1 | ps -o wchan during the flood: kqread | ✅ | `kqread` on every sample in Runs A, D and E, including at peak backlog. One Run E sample caught the loop in `Rs` (running, mid-iteration) rather than blocked — which is the same conclusion: never `sbwait`. |
| K1 | **kernel buffer-enforcement finding** (not in the roadmap manifest; added because Exp 7 required explaining it) | ✅ | `sb_probe.py`, no exchange code: a never-reading receiver absorbed 67,108,877 B with `SO_RCVBUF` pinned at 8192 — **8,192× its own buffer** — verified by `FIONREAD` *and* a drain-and-count. Linux control on the same tool: 17,081 B, textbook flow control. Bound is global mbuf budget (`maxmbufmem` 1.94 GiB; the run used ~208 MB, 0 denials), computed rather than observed. |

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

- **PARTIALLY RESOLVED — why buffer tuning could not produce backpressure.**
  Established by measurement (Day 4): on this FreeBSD loopback path a
  non-reading receiver's socket-buffer limits are not enforced against the
  sender, whether the buffer was sized by sysctl or pinned explicitly with
  `setsockopt`. Demonstrated with no project code by `sb_probe.py`
  (8,192x overshoot), cross-checked against a Linux control that behaves
  correctly, and `FIONREAD` + drain-and-count rule out a reporting artifact.
  **What is established:** the effect is real, reproducible, and independent
  of the exchange. **What is not:** the precise kernel mechanism. The
  operative bound is almost certainly global mbuf availability
  (`maxmbufmem` 1.94 GiB vs ~208 MB used, 0 denials), but that is an
  arithmetic inference -- driving the pool to exhaustion was deliberately
  not attempted after Run C showed what a starved network stack does to this
  VM. **Report it at exactly this confidence level:** the observation and its
  isolation are solid; the mechanism is stated as the most consistent
  explanation, not as a demonstrated one.
- **RESOLVED — F3.** The `win 0` search on `exp7e.pcap` is complete and the
  result sharpens K1 rather than complicating it: zero zero-window packets
  from the non-reading subscriber, which instead advertised a *constant*
  81,920-byte window across all 11.9 MB it absorbed. All 1,630,569 hits are
  the server throttling inbound order flow, which doubles as an in-capture
  control proving this kernel does perform receiver flow control when the
  application actually reads. See Day 4.

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
