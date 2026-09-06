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
- [ ] Exp 2
- [ ] Exp 3
- [ ] Exp 4 (real server)
- [ ] Exp 4 (naive control, `EXCH_NAIVE=1`) — T5 contrast
- [ ] Exp 5
- [ ] Exp 6
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
| T1 | listening vs connected socket table | ✅ | Exp 1 redo, real VM run: fd=3 LISTEN `127.0.0.1:5000`/`*:*` vs fd=5 (sid=2) ESTABLISHED `127.0.0.1:5000`/`127.0.0.1:28907` — see raw session log |
| T2 | TCP state timeline, CLOSE_WAIT transient | ⬜ | |
| T5 | Exp 4 correct vs naive control | ⬜ | naive control code written + locally verified (`src/naive_server.py`, `src/tools/verify_naive_stall.py`); VM run pending |
| T6 | Exp 5 ready vs idle fds | ⬜ | |
| T7 | FIN vs RST matrix | ⬜ | |
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
- Watch for Exp 2's `TIME_WAIT` behavior on the redo: previously it cleared
  in <1s despite `msl=30000` implying a 60s hold. Leading hypothesis is
  FreeBSD's `net.inet.tcp.nolocaltimewait` (defaults to `1`, skips full
  `TIME_WAIT` for loopback-only connections) — check
  `sysctl net.inet.tcp.nolocaltimewait` on the VM and confirm on this redo
  before stating it as fact in the report.

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
