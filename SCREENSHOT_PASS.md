# Screenshot Pass — Commands Per Experiment

Run on the FreeBSD VM (`root@192.168.65.4`, `~/col334_a2`) unless noted.
Each block is copy-paste ready. Suggested filenames match the report's
`[SCREENSHOT Na]` placeholder tags so dropping them into `report_draft.md`
later is mechanical.

Before starting, sync the fixed clients/server if you haven't already:
```sh
scp src/server.py src/trader.py src/market_data.py root@192.168.65.4:~/col334_a2/src/
scp src/tools/verify_quit.py src/tools/gapd_concurrency.sh root@192.168.65.4:~/col334_a2/src/tools/
```

---

## Experiment 1 — Listening and Connected Sockets

```sh
python3 experiment.py 1
```
In a second terminal, **while the experiment is mid-run** (it holds the
connection open briefly — watch for the prompt/pause):
```sh
sockstat -4 | grep 5000
netstat -an -p tcp | grep 5000
```
**Screenshot 1a** → both command outputs together (one terminal, or two
panes): the `LISTEN` row plus the two `ESTABLISHED` rows (server side and
client side).

---

## Experiment 2 — Observing TCP Connection States

Terminal A:
```sh
tcpdump -i lo0 -n -s0 -w exp2.pcap 'tcp port 5000' &
python3 experiment.py 2
```
Terminal B, sampled repeatedly across the ~10 s run (run this in a tight
loop or manually every ~1s once the client is up):
```sh
netstat -an -p tcp | grep 5000
```
**Screenshot 2a** → several samples of Terminal B showing the state
progression (or as many distinct states as you catch — `ESTABLISHED` is
enough if `CLOSE_WAIT`/`TIME_WAIT` are too fast to poll, which the report
already discusses as an honest limitation).

Once `experiment.py 2` finishes:
```sh
kill %1   # stop the backgrounded tcpdump
tcpdump -r exp2.pcap -n -ttt -S
```
**Screenshot 2b** → the four-way close: `FIN,ACK` → `ACK` → `FIN,ACK` → `ACK`.

---

## Experiment 3 — TCP as a Byte Stream

```sh
tcpdump -i lo0 -n -s0 -w exp3.pcap 'tcp port 5000' &
python3 experiment.py 3
```
**Screenshot 3a** → the server's own stdout/trace: four `recv` lines
(`n=6`, `n=10`, `n=7`, `n=1`), `lines_out=1` only on the last one.
(If tracing to a file: `EXCH_TRACE=exp3_trace.jsonl python3 experiment.py 3`,
then `cat exp3_trace.jsonl | grep recv`.)

```sh
kill %1
tcpdump -r exp3.pcap -n -ttt -S
```
**Screenshot 3b** → four separate `PSH` segments of length 6/10/7/1.

---

## Experiment 4 — One Client Should Not Stall the Others

```sh
python3 experiment.py 4
pgrep -f server.py
ps -o pid,tid,wchan,state -H -p <pid>
```
**Screenshot 4a** → server's `Client 2 response: OK` / elapsed ≈0.001s,
plus `ps` showing `wchan=kqread`.

```sh
EXCH_NAIVE=1 python3 experiment.py 4
pgrep -f server.py
ps -o pid,tid,wchan,state -H -p <pid>
```
**Screenshot 4b** → `Client 2 response: None` / elapsed ≈5.1s (harness
timeout), plus `ps` showing `wchan=sbwait`.

---

## Experiment 5 — Multiple Clients and I/O Multiplexing *(optional)*

```sh
python3 experiment.py 5
```
**Screenshot 5a** → server trace: `recv` fires only for sid 2, 4, 6
(clients 1, 3, 5), never for sid 3 or 5.

In a second terminal, sampled ~4x during the ~16.5s run:
```sh
netstat -an -p tcp | grep '\.5000 '
```
**Screenshot 5b** → all five connections `ESTABLISHED`, `Recv-Q`/`Send-Q`
near 0 for the two idle ones.

---

## Experiment 6 — FIN vs. RST

```sh
tcpdump -i lo0 -n -s0 -w exp6.pcap 'tcp port 5000' &
python3 experiment.py 6
netstat -an -p tcp | grep '\.5000 '
kill %1
tcpdump -r exp6.pcap -n -ttt -S
```
**Screenshot 6a** → tcpdump readback: Part A's four-way `[F.]/[.]/[F.]/[.]`
close vs. Part B's single `[R.]`.
**Screenshot 6b** → server trace: `eof_fin{ev_eof=True}` for Part A vs.
`eof_rst{errno=54, ev_fflags=54}` for Part B.

---

## Experiment 7 — Backpressure and the Slow Receiver

This is the long one — four separate sub-runs. All use stock sysctls
(nothing to tune) since the story now rests on Run E (volume), not on
shrinking buffers.

### 7a — Run A (stock buffers, confirms the null prediction)
```sh
EXCH_TRACE=exp7a_trace.jsonl python3 experiment.py 7
```
While it runs, in a second terminal, sample repeatedly:
```sh
netstat -an -p tcp | grep 5000
ps -o pid,tid,wchan,state -H -p $(pgrep -f server.py)
```
**Screenshot 7a** → the slow subscriber's `Recv-Q` climbing across samples
while the reading client stays near 0, plus `wchan=kqread`.

### 7b — sb_probe.py (the standalone isolation proof, no exchange code)
```sh
python3 src/tools/sb_probe.py
```
**Screenshot 7b** → its own printed table: `FIONREAD` climbing past the
buffer size (default and pinned-8192 cases), and the drain-and-count
confirmation.

### 7c — wire evidence (win 0 direction breakdown)
Reuses the Run E capture (see 7d below) — do 7d first, then:
```sh
tcpdump -r exp7e.pcap -n 'tcp[13] & 0x04 == 0' | grep 'win 0' | \
  awk '{print $3}' | cut -d. -f1-4 | sort | uniq -c
tcpdump -r exp7e.pcap -n 'src port <subscriber-port>' | head -3
tcpdump -r exp7e.pcap -n 'src port <subscriber-port>' | tail -3
```
**Screenshot 7c** → the `win 0` count-by-source breakdown (all server→trader,
zero from the subscriber) and the subscriber's first/last `win 320` lines.

### 7d — Run E (volume-forced onset, the real evidence)
```sh
tcpdump -i lo0 -n -s0 -w exp7e.pcap 'tcp port 5000' &
EXCH_TRACE=exp7e_trace.jsonl python3 src/server.py 127.0.0.1 5000 &
sleep 1
python3 src/tools/exp7_load.py 127.0.0.1 5000 700000
wait %2          # wait for server to be killed/stopped after the run
kill %1          # stop tcpdump
python3 src/tools/exp7_report.py exp7e_trace.jsonl
```
**Screenshot 7d** → `exp7_report.py`'s output: the onset line
(`first send_would_block at t=...`), the per-sid table
(`wbuf_hwm`, `eagain`), and the pre/post engine-latency split.

**Then regenerate the figures from this same trace** (do this locally, not
part of the screenshot pass, but needed before the report is final):
```sh
python3 src/tools/exp7_csv.py exp7e_trace.jsonl exp7e --bucket-ms 200
# scp exp7e_conn.csv/exp7e_engine.csv/exp7e_events.csv back to the Mac, then:
python3 src/tools/plot.py exp7e --outdir figs
```
Check `figs/P1_pending.png` shows a real spike (not a flat 16–23B wobble)
near the onset time, sid 3, before treating **Figures P1/P2/P3** as done.

---

## Experiment 8 — Unexpected Client Disconnection

```sh
tcpdump -i lo0 -n -s0 -w exp8.pcap 'tcp port 5000' &
EXCH_TRACE=exp8_trace.jsonl python3 experiment.py 8
kill %1
tcpdump -r exp8.pcap -n -ttt -S | grep <killed-client-port>
```
**Screenshot 8a** → server trace: `eof_fin` for the killed client's sid,
followed by `close{why='FIN', orders_surviving=...}`, and continued
`TRADE` pushes to the surviving client afterward.
**Screenshot 8b** → tcpdump for that port: a normal four-way FIN close
(not a RST) — proves `SIGKILL` looks identical to a clean disconnect on
the wire.

---

## Administrative / gap-closing screenshots (not tied to a numbered
## experiment, but needed for completeness)

### QUIT — live end-to-end proof
```sh
printf 'LOGIN q_test\nBUY JNST 5 100\nQUIT\n' | nc -N -w 3 127.0.0.1 5000
sockstat -4 | grep 5000
```
**Screenshot (QUIT)** → the `nc` output (`OK` / `ORDER_ACCEPTED 0`) and the
`sockstat` line showing the port released — proves the graceful-close path
works live, not just in `verify_quit.py`.

### Gap D — §4.3's ≥10 simultaneous clients (≥2 traders, ≥4 MD)
```sh
sh src/tools/gapd_concurrency.sh 127.0.0.1 5000
```
In a second terminal, once it prints "10 clients launched":
```sh
sockstat -4 | grep :5000
```
**Screenshot (Gap D)** → 10 `ESTABLISHED` rows (4 traders + 6 MD +
server-side counterparts as applicable). Then Ctrl-C the first terminal to
close everything cleanly.

---

## Suggested order

Given you're doing 1/2/3 first: run them in numeric order top to bottom —
each section is self-contained and does not depend on state from another
experiment (server restarts cleanly between sections). Experiment 7 is the
only multi-step one; do 7a → 7b → 7d → 7c in that order since 7c reuses 7d's
capture. Do QUIT and Gap D last, since they're quick and order-independent.

Save screenshots as `<Na>.png` (e.g. `7d.png`) in a `screenshots/` folder —
that matches the placeholder tags in `report_draft.md` and makes the final
embedding pass mechanical.
