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
- [ ] `baseline.txt` captured **inside the FreeBSD VM** — B1. NOT valid from
      macOS; the Exp 7 arithmetic in `src/tools/baseline.sh` needs the real
      FreeBSD `sysctl` values.

## Day 1 — Phase 1 + 2 (offline, no sockets)

- [ ] `framing.py` written and passing `T-FRAG-ALL` / `T-FRAG-RAND` /
      `T-COALESCE` / `T-BOUNDARY` / `T-OVERFLOW` (`tests/test_all.py`)
- [ ] `protocol.py` parser written (`parse_u32`, `parse_line`) passing
      `T-VALID`
- [ ] `engine.py` written passing `T-ENGINE`, including
      `test_engine_disconnect_then_match` — the §2.6 case the harness itself
      never exercises
- [ ] `grep -ln 'import socket' src/*.py` → only `server.py`, `trader.py`,
      `market_data.py`

## Day 2–3 — Phase 3 + robustness (needs the VM)

- [ ] Exps 1–5 clean in the VM
- [ ] Naive blocking-server control built and run against Exp 4 (§8.4) —
      T5
- [ ] Exps 6, 8 clean; supplementary disconnect-then-match test passing
      against the real event loop too

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
| T4 | offline framer pass counts (N-1 splits + 1000 random) | ⬜ | |
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
| T1 | listening vs connected socket table | ✅ (draft) | real data already captured — see session log: fd4=LISTEN no peer, fd6=ESTABLISHED 127.0.0.1:50387; redo cleanly in the VM with sockstat/procstat for the final version |
| T2 | TCP state timeline, CLOSE_WAIT transient | ⬜ | |
| T5 | Exp 4 correct vs naive control | ⬜ | |
| T6 | Exp 5 ready vs idle fds | ⬜ | |
| T7 | FIN vs RST matrix | ⬜ | (already have one real RST sample: errno=54, ev_fflags=54 — reuse for the RST column, still need the FIN-side capture) |
| T10 | Exp 8 timeline + TRADE counts | ⬜ | |
| T11 | §2.6 order-survival: close→orders_surviving>0→later notify_dropped | ⬜ | |
| T12 | errno reference table (35/32/54/60) | ⬜ | |
| B1 | baseline.txt from the FreeBSD VM | ⬜ | |

---

## Open questions / decisions to revisit

- CANCEL of another trader's order: reject as `not_owner` vs `unknown_order`?
  → decided `not_owner` (README §10) — keep consistent in engine.py and in
  `test_engine_cancel_not_owner`.
- Slow-consumer policy: evict at `WBUF_MAX` (4 MiB) vs unbounded vs conflate
  → decided: evict (roadmap §5) — confirm the eviction never actually
  triggers during a *normal* Exp 7 run (4 MiB is far above the ~85 KB feed);
  if it does trigger, something else is wrong.

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
