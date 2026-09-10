# The Socket Exchange — System Implementation and Experimental Analysis

**COL334: Computer Networks (Assignment 2)**  
**Authors:** Amar Sinha (2024CS10388), Ritwik Sehrawat (2024AM10224)  
**Implementation:** Python 3 (`socket` and `select.kqueue` system interfaces)  
**Environment:** FreeBSD 14.4 / 15.1-RELEASE VM, loopback interface (`lo0`)  

---

## 1. System Architecture & Implementation Decisions

### 1.1 Concurrency and I/O Multiplexing Architecture
The Exchange Server is implemented as a single-threaded event loop built directly on FreeBSD's kernel event notification facility, `select.kqueue()`. High-level abstractions such as `asyncio` (prohibited by §4.1.2) and the Python `selectors` module (which obscures underlying system calls) were strictly avoided. 

Every socket operation—`socket()`, `bind()`, `listen()`, `accept()`, `send()`, `recv()`, `close()`, and `shutdown()`—is invoked directly through standard low-level system interfaces.

The architecture enforces strict separation between transport management and application state:
* `server.py`: Owns all file descriptors, network buffers, and event loop scheduling.
* `framing.py`, `protocol.py`, `engine.py`: Handle stream framing, message parsing, and order matching without importing or referencing `socket`.

### 1.2 Architectural Rationale
1. **Structural Decoupling from Transport Backpressure:**  
   The matching engine has no direct reference to client sockets. `engine.handle(sid, parsed_msg)` takes a session identifier and returns an in-memory list of `(target_sid, message_bytes)` tuples. The only function issuing network writes is `server.flush()`. Consequently, socket write stalls physically cannot block the order matching pipeline.
2. **CPython Concurrency Profile:**  
   Under CPython's Global Interpreter Lock (GIL), multi-threaded network architectures incur context switching and synchronization overhead without achieving genuine multicore parallelism for CPU-bound tasks. The order book operations (exact-price dictionary lookups and FIFO deques) execute in microseconds, making a non-blocking $O(\text{ready})$ event loop optimal.
3. **Deterministic State Management:**  
   A single-threaded reactor guarantees serial execution of matching logic, eliminating race conditions, mutex contention, and synchronization deadlocks across concurrent connections.

### 1.3 Key Design Details
* **Userspace Write Buffering with Lazy Write Filters:**  
  Each connection maintains a dedicated userspace buffer (`wbuf`) implemented as a `bytearray` with an offset pointer. Outbound messages are appended to `wbuf` and flushed opportunistically. If `send()` returns `EWOULDBLOCK` (`BlockingIOError`), the unsent tail is retained, and `KQ_FILTER_WRITE` is registered for that specific descriptor. Because FreeBSD write filters are level-triggered, the filter is immediately unregistered once the buffer drains, preventing CPU spin loops.
* **Deferred Socket Reclamation (Batch-Safe Close):**  
  Sockets are never closed mid-batch inside the `kevent()` dispatch loop. Closing descriptor $N$ within an active batch would allow the kernel to reassign descriptor $N$ to a subsequent `accept()` call in the same iteration, routing stale events to the new client. Descriptors marked for termination are queued in a reap list and closed only after batch processing completes.
* **Session-Tied Identity vs. Persistent Orders:**  
  Order ownership is keyed by a monotonic integer session identifier (`sid`). When a client disconnects, its session is removed from active notification routing tables (`by_sid`), but its resting orders remain active in the order book. When matched, trades execute, public `TRADE` messages broadcast to subscribers, and the private notification to the disconnected session is safely dropped (§2.6 compliance).
* **Strict Byte-Level Protocol Validation:**  
  All protocol parsing operates on raw bytes. Numerical fields (prices, quantities, order IDs) are validated against `^[0-9]+$` prior to range verification ($1 \le q, p \le 2^{31}-1$). This strictly excludes malformed inputs accepted by Python's `int()` (e.g., `+100`, `1_000`, floats, and Unicode digits).
* **`TCP_NODELAY` Socket Configuration:**  
  Accepted sockets disable Nagle's algorithm via `TCP_NODELAY` to ensure immediate dispatch of market-data updates and order execution notifications.
* **Graceful `QUIT` and Half-Close Handling:**  
  Client termination uses `s.shutdown(socket.SHUT_WR)` to issue a clean TCP FIN while preserving the inbound stream. The server defers closing the client descriptor until any pending outbound messages in `wbuf` are fully transmitted to the kernel.

---

## 2. Experiment 1 — Listening and Connected Sockets

### 2.1 Technical Analysis
A listening TCP socket functions as an incoming connection acceptor: it binds to a local endpoint (`127.0.0.1:5000`), specifies a foreign wildcard (`*:*`), remains in the `LISTEN` state, and never handles application data payloads. 

Each accepted client connection is allocated an independent, dedicated file descriptor with a fully qualified 4-tuple (`local_ip:5000`, `foreign_ip:client_port`) residing in the `ESTABLISHED` state. Both the listening socket and all active connections share the identical local port number; incoming packets are demultiplexed by the kernel TCP stack based on the complete 4-tuple.

### 2.2 Investigation Methodology & Tools
The experiment was launched using the harness, and socket allocations were inspected via FreeBSD system utilities:
```sh
python3 experiment.py 1
sockstat -4 | grep 5000
netstat -an -p tcp | grep 5000
```

### 2.3 Empirical Evidence

![Screenshot 1a](screenshots/1a.png)  
*Figure 1: `sockstat` and `netstat` outputs during Experiment 1, displaying the listening descriptor alongside the established client connection.*

**Kernel Socket Table Inspection:**
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

**Table 1: Exchange Server Socket Descriptors**

| File Descriptor | Socket Role | Local Address | Foreign Address | TCP State |
| :--- | :--- | :--- | :--- | :--- |
| `fd=3` | Listening Acceptor | `127.0.0.1:5000` | `*:*` | `LISTEN` |
| `fd=5` (`sid=2`) | Active Connection | `127.0.0.1:5000` | `127.0.0.1:28907` | `ESTABLISHED` |

---

## 3. Experiment 2 — Observing TCP Connection States

### 3.1 Technical Analysis
TCP connection lifecycles follow a strict state progression:
1. **Connection Setup:** The three-way handshake transitions endpoints from `LISTEN` / `SYN_SENT` $\to$ `SYN_RECEIVED` $\to$ `ESTABLISHED`.
2. **Orderly Teardown:** The client acts as the active closer by invoking `close()`, transmitting a `FIN` control segment. The client enters `FIN_WAIT_1`, while the server transitions to `CLOSE_WAIT` upon acknowledging the `FIN`.
3. **Server Completion:** The server reads EOF (`recv()` returning `b''`), closes its local descriptor, and sends its own `FIN`, transitioning to `LAST_ACK`.
4. **Final Closure:** Upon receiving the server's `FIN`, the client sends a final `ACK` and enters `TIME_WAIT` (retained for $2\times\text{MSL}$ to absorb delayed duplicate segments), while the server transitions directly to `CLOSED`.

Because our event loop promptly detects EOF on read readiness, the server's duration in `CLOSE_WAIT` is minimal (measured between $504\,\mu\text{s}$ and $984\,\mu\text{s}$), completely preventing file descriptor exhaustion.

### 3.2 Investigation Methodology & Tools
Full packet capture on `lo0` combined with high-frequency `netstat` sampling:
```sh
tcpdump -i lo0 -n -s0 -w exp2.pcap 'tcp port 5000' &
python3 experiment.py 2
netstat -an -p tcp | grep 5000
tcpdump -r exp2.pcap -n -ttt -S
```

### 3.3 Empirical Evidence

![Screenshot 2a](screenshots/2a.png)  
*Figure 2a: Repeated `netstat` sampling across connection and disconnection phases.*

![Screenshot 2b](screenshots/2b.png)  
*Figure 2b: Wire-level packet capture confirming the complete four-way handshake and teardown.*

**Packet Trace Chronology (`exp2.pcap`):**
```
+0.000098s  SYN 62724->5000 -> SYN/ACK -> ACK       (Handshake established)
+10.003715s [62724->5000] FIN,ACK                  (Client initiates close)
+0.000030s  [5000->62724] ACK                      (Server ACKs; enters CLOSE_WAIT)
+0.000504s  [5000->62724] FIN,ACK                  (Server closes; enters LAST_ACK)
+0.000014s  [62724->5000] ACK                      (Client ACKs; enters TIME_WAIT)
```

**Table 2: State Machine Transition Timeline**

| Elapsed | Wire Event | Client State | Server State |
| :--- | :--- | :--- | :--- |
| $t_0$ | Handshake complete (`SYN` $\to$ `SYN/ACK` $\to$ `ACK`) | `ESTABLISHED` | `ESTABLISHED` |
| $t_0 + 10.0\,\text{s}$ | Client issues `close()`, sends `FIN` | `FIN_WAIT_1` $\to$ `FIN_WAIT_2` | `CLOSE_WAIT` |
| $+30\,\mu\text{s}$ | Server acknowledges client `FIN` | `FIN_WAIT_2` | `CLOSE_WAIT` |
| $+504\,\mu\text{s}$ | Server issues `close()`, sends `FIN` | `TIME_WAIT` | `LAST_ACK` |
| $+14\,\mu\text{s}$ | Client acknowledges server `FIN` | `TIME_WAIT` | `CLOSED` |

---

## 4. Experiment 3 — TCP as a Byte Stream

### 4.1 Technical Analysis
TCP provides a structured byte stream service without preserving application-level message boundaries. Network boundaries are determined by sender buffering, MTU limits, TCP segment sizing, and transmission timing. Application protocols running over TCP must implement explicit framing.

In this experiment, the client transmits `LOGIN experiment_trader\n` across four separate `send()` operations spaced by 200 ms. The server performs four discrete `recv()` invocations. The application framer buffers incoming bytes until the `\n` delimiter is encountered, emitting the parsed command only upon receiving the fourth segment.

### 4.2 Investigation Methodology & Tools
```sh
tcpdump -i lo0 -n -s0 -w exp3.pcap 'tcp port 5000' &
python3 experiment.py 3
tcpdump -r exp3.pcap -n -ttt -S
```

### 4.3 Empirical Evidence

![Screenshot 3a](screenshots/3a.png)  
*Figure 3a: Server execution trace detailing partial reads, buffer accumulation, and final dispatch.*

![Screenshot 3b](screenshots/3b.png)  
*Figure 3b: Packet readback confirming four distinct PSH segments arriving over loopback.*

**Server Trace Analysis:**
```
Time      Event sid fd n  rbuf_pre rbuf_post lines Payload
80.220ms  recv  2   6  6  0        6         0     "LOGIN "
281.372ms recv  2   6  10 6        16        0     "experiment"
483.431ms recv  2   6  7  16       23        0     "_trader"
684.034ms recv  2   6  1  23       0         1     "\n"
684.199ms engine dispatch: sid=2, messages=1, latency=25.4 us
```

**Table 3: Stream Reassembly Ledger**

| Chunk | Interval | Length | Cumulative Buffer | Parser Status |
| :---: | :---: | :---: | :---: | :---: |
| 1 | 80 ms | 6 B | `LOGIN ` (6 B) | Incomplete (0 emitted) |
| 2 | 281 ms | 10 B | `LOGIN experiment` (16 B) | Incomplete (0 emitted) |
| 3 | 483 ms | 7 B | `LOGIN experiment_trader` (23 B) | Incomplete (0 emitted) |
| 4 | 684 ms | 1 B | Frame complete on `\n` | Emitted 1 command (`OK` returned) |

Offline unit tests in `tests/test_all.py` further verify stream reassembly invariance across all 47 possible 2-way split permutations and 1,000 randomized multi-fragment cuts.

---

## 5. Experiment 4 — One Client Should Not Stall the Others

### 5.1 Technical Analysis
In an unmultiplexed, blocking architecture, a client that connects and halts mid-message causes the server process or thread to block inside `recv()`. The server becomes incapable of accepting new connections or servicing other clients.

Using an event-driven kqueue architecture, the server waits exclusively inside `kevent()`. A descriptor is only returned when the operating system indicates read readiness. While Client 1 sits idle without terminating its line, the server services Client 2 instantaneously (elapsed time: 0.003 s).

### 5.2 Comparative Investigation (A/B Control)
To empirically verify the blocking mechanism, a baseline blocking server (`src/naive_server.py`) was evaluated under identical conditions:
```sh
python3 experiment.py 4                       # Production kqueue server
EXCH_NAIVE=1 python3 experiment.py 4          # Blocking baseline control
ps -o pid,tid,wchan,state -p <pid>
```

### 5.3 Empirical Evidence

![Screenshot 4a](screenshots/4a.png)  
*Figure 4a: Production server execution: Client 2 completes in 0.003 s with kernel wait channel `wchan=kqread`.*

![Screenshot 4b](screenshots/4b.png)  
*Figure 4b: Blocking baseline execution: Client 2 times out after 5.07 s with kernel wait channel `wchan=sbwait`.*

**Table 4: Concurrency Performance & Wait Channel Comparison**

| Metric / Parameter | Production Server (`server.py`) | Blocking Baseline (`naive_server.py`) |
| :--- | :--- | :--- |
| **I/O Mechanism** | Non-blocking `select.kqueue()` | Blocking POSIX sockets |
| **Kernel Wait Channel (`wchan`)** | **`kqread`** (waiting in `kevent`) | **`sbwait`** (blocked on socket buffer read) |
| **Client 2 Response** | `OK` | `None` (Timed out) |
| **Client 2 Elapsed Time** | **0.003 s** | **5.072 s** |
| **Client 1 Socket Queue (`Recv-Q`)** | `0` (Buffered in userspace `rbuf`) | `0` (Held blocked in kernel `recv`) |

The wait channel confirms the root behavior: `sbwait` reflects thread suspension on an individual socket buffer, whereas `kqread` indicates scalable multiplexing over all registered descriptors.

---

## 6. Experiment 5 — Multiple Clients and I/O Multiplexing

### 6.1 Technical Analysis
I/O multiplexing decouples connection presence from CPU servicing cost. Across five concurrently established connections where only three submit data (Clients 1, 3, 5), the event loop is dispatched exclusively for descriptors possessing unread socket buffers. Idle connections consume zero CPU time, exhibiting $O(\text{ready})$ rather than $O(\text{registered})$ scaling.

### 6.2 Investigation Methodology & Tools
```sh
python3 experiment.py 5
netstat -an -p tcp | grep '\.5000 '
```

### 6.3 Empirical Evidence

![Screenshot 5a](screenshots/5a.png)  
*Figure 5a: Event loop execution trace confirming read events trigger solely for active clients.*

![Screenshot 5b](screenshots/5b.png)  
*Figure 5b: System connection table showing all five connections concurrently established.*

**Table 5: Connection Readiness vs. Service Activity**

| Session ID | File Descriptor | Foreign Port | Client Role | `recv()` Activity | `Recv-Q` Status |
| :---: | :---: | :---: | :---: | :---: | :---: |
| `sid=2` | `fd=5` | 11117 | Client 1 | Active (1 read, `OK` sent) | 3 B (Client unread reply) |
| `sid=3` | `fd=6` | 48451 | Client 2 | **None (Idle)** | **0 B / 0 B** |
| `sid=4` | `fd=7` | 61204 | Client 3 | Active (1 read, `OK` sent) | 3 B (Client unread reply) |
| `sid=5` | `fd=8` | 20623 | Client 4 | **None (Idle)** | **0 B / 0 B** |
| `sid=6` | `fd=9` | 46858 | Client 5 | Active (1 read, `OK` sent) | 3 B (Client unread reply) |

---

## 7. Experiment 6 — FIN vs. RST: Orderly and Abrupt Termination

### 7.1 Technical Analysis
* **Orderly Termination (`FIN`):** A synchronized protocol event initiated via `shutdown(SHUT_WR)` or `close()`. The `FIN` control segment carries an incremented sequence number, guarantees in-order delivery of preceding data, allows graceful half-close, and places the active closer in `TIME_WAIT`. The peer application detects closure via EOF (`recv()` returns `b''`).
* **Abrupt Termination (`RST`):** An immediate connection abort initiated via `SO_LINGER{on=1, timeout=0}` or process termination errors. The `RST` segment is unsequenced, discards queued network buffers in both directions, generates no `TIME_WAIT` state, and immediately surfaces as `ECONNRESET` (errno 54).

Under FreeBSD `kqueue`, socket errors are delivered directly within the event structure (`ev.fflags`), allowing discrimination between normal EOF and an abortive reset before issuing `recv()`.

### 7.2 Investigation Methodology & Tools
```sh
tcpdump -i lo0 -n -s0 -w exp6.pcap 'tcp port 5000' &
python3 experiment.py 6
tcpdump -r exp6.pcap -n -ttt -S
```

### 7.3 Empirical Evidence

![Screenshot 6a](screenshots/6a.png)  
*Figure 6a: Packet capture distinguishing four-way FIN handshake (Part A) from single RST segment (Part B).*

![Screenshot 6b](screenshots/6b.png)  
*Figure 6b: Server logs detailing `eof_fin` (Part A) versus `eof_rst` with errno 54 (Part B).*

**Wire Event Comparison:**
```
Part A (Orderly FIN - Port 16177):
  16177 > 5000: Flags [F.] seq 1 ack 1 (Client SHUT_WR)
  5000 > 16177: Flags [.]  ack 2        (Server ACK)
  5000 > 16177: Flags [F.] seq 1 ack 2 (Server FIN)
  16177 > 5000: Flags [.]  ack 2        (Client ACK)

Part B (Abrupt RST - Port 50346):
  50346 > 5000: Flags [R.] seq 1 win 0  (Immediate abortive reset)
```

**Table 6: Teardown Mechanism Characteristics**

| Dimension | Orderly Termination (`FIN`) | Abrupt Termination (`RST`) |
| :--- | :--- | :--- |
| **Packet Sequence** | 4-way exchange (`[F.]`, `[.]`, `[F.]`, `[.]`) | Single abortive packet (`[R.]`) |
| **`recv()` Application Signal** | Returns empty bytes (`b''`) | Raises `ConnectionResetError` |
| **FreeBSD Kernel Errno** | None ($0$) | **$54$ (`ECONNRESET`)** |
| **Kqueue Event Notification** | `ev.flags & KQ_EV_EOF` | `ev.fflags == 54` |
| **`TIME_WAIT` State** | Enforced on active closer | Completely bypassed |
| **Server State Effect** | Closed cleanly; surviving peers unaffected | Closed cleanly; surviving peers unaffected |

---

## 8. Experiment 7 — Backpressure and the Slow Receiver

### 8.1 Technical Analysis & Theoretical Capacity
When a subscriber ceases reading from its socket, incoming bytes accumulate in its receive buffer. Once full, the receiver advertises a zero-window (`win 0`), halting TCP transmissions. Continued application write attempts then saturate the server's send buffer, causing non-blocking `send()` calls to fail with `EWOULDBLOCK` / `EAGAIN` (errno 35).

A properly decoupled server buffers excess messages in userspace and registers write notification filters (`KQ_FILTER_WRITE`) for the backlogged socket, ensuring other active clients and the core matching engine continue operating uninhibited.

### 8.2 Initial Arithmetic vs. FreeBSD Loopback Behavior
In standard `experiment.py 7`:
* Message size: `TRADE JNST 1 238\n` = 17 bytes.
* Order volume: 5,000 trade iterations = 85,003 bytes per subscriber (including 3 B `OK\n`).
* FreeBSD loopback socket buffers: default `sendspace = 32,768 B`, `recvspace = 65,536 B` (aggregate capacity $\approx 98,304\text{ B}$).

Because $85,003 < 98,304$, the data fits entirely within kernel buffers. Run A confirms this: `send_would_block = 0`, with the slow subscriber's kernel `Recv-Q` absorbing all 83,232 bytes while the active subscriber maintained `Recv-Q` near zero.

![Screenshot 7a](screenshots/7a.png)  
*Figure 7a: `netstat` and `ps` sampling showing slow subscriber `Recv-Q` scaling up to 83,232 B while server remains in `kqread`.*

**Table 7: Run A Kernel Buffer Accumulation**

| Sample | Fast Subscriber `Recv-Q` | Slow Subscriber `Recv-Q` | Server `wchan` |
| :---: | :---: | :---: | :---: |
| 1 | 17 B | 629 B | `kqread` |
| 2 | 17 B | 3,757 B | `kqread` |
| 3 | 0 B | 22,083 B | `kqread` |
| 4 | 0 B | 34,816 B | `kqread` |
| 5 | 17 B | 54,740 B | `kqread` |
| 6 | 0 B | 74,834 B | `kqread` |
| 7 | 17 B | **83,232 B** | `kqread` |

### 8.3 Platform Investigation: FreeBSD Loopback Flow Control
To induce backpressure within the default 5,000-message quota, buffer sizes were restricted via `SO_RCVBUF=8192` and `SO_SNDBUF=1024`. However, `Recv-Q` continued to climb past limits without triggering backpressure.

An isolated standalone diagnostic (`src/tools/sb_probe.py`) revealed that on FreeBSD's loopback interface (`lo0`), when a receiving process never reads, the TCP stack allocates kernel mbuf clusters rather than enforcing strict window clamping:

![Screenshot 7b](screenshots/7b.png)  
*Figure 7b: `sb_probe.py` diagnostic on FreeBSD: 67 MB absorbed unread without stall (in contrast to Linux, which stalls at 17 KB).*

![Screenshot 7c](screenshots/7c.png)  
*Figure 7c: Wire inspection on `exp7e.pcap` showing subscriber maintaining a constant advertised window (`win 320` = 81,920 B) across 11.9 MB.*

### 8.4 High-Volume Verification: Run E
To evaluate genuine backpressure handling under production-scale loads, `exp7_load.py` was executed with 700,000 matched order pairs (stock sysctl settings):

![Screenshot 7d](screenshots/7d.png)  
*Figure 7d: `exp7_report.py` output displaying backpressure onset, per-session statistics, and engine latency.*

**Table 8: High-Volume Session Summary (Run E)**

| Session | Role | Total Queued | Peak Userspace Backlog (`wbuf_hwm`) | `EAGAIN` Events |
| :---: | :---: | :---: | :---: | :---: |
| `sid=1` | Subscriber (Never reads) | 11,900,003 B | 17 B | 0 |
| `sid=2` | Buyer (Trader) | 28,151,893 B | 23 B | 0 |
| `sid=3` | Seller (Trader) | 26,737,003 B | **1,474 B** | **66** |

The 66 `EAGAIN` events occurred on `sid=3` due to batch-write bursts. The server correctly responded by arming `KQ_FILTER_WRITE`, buffering the excess in userspace, and resuming writes without blocking.

![Figure P1](figs/P1_pending.png)  
*Figure 7e: Per-connection userspace backlog over time, showing transient buffering on `sid=3` with zero impact on other sessions.*

![Figure P3](figs/P3_queued_sent.png)  
*Figure 7f: Cumulative transmission volume vs. transient backlog delta for `sid=3`.*

### 8.5 Matching Engine Decoupling Proof

![Figure P2](figs/P2_engine.png)  
*Figure 7g: Matching engine execution latency distribution across pre-onset and post-onset phases.*

**Table 9: Matching Engine Latency Distribution Across Backpressure Onset**

| Window | Order Operations | p50 Latency | p90 Latency | p99 Latency | Max Latency | Throughput |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Pre-Onset** | 534,524 | $4.3\,\mu\text{s}$ | $8.0\,\mu\text{s}$ | $62.6\,\mu\text{s}$ | $47.0\,\text{ms}$ | 6,550 ops/s |
| **Post-Onset** | 865,479 | **$3.9\,\mu\text{s}$** | **$6.6\,\mu\text{s}$** | **$55.0\,\mu\text{s}$** | $64.4\,\text{ms}$ | **7,364 ops/s** |

Engine latency remained constant across the backpressure boundary (p50 ratio: 0.91, p99 ratio: 0.88), providing empirical proof that network write stalls do not impede order processing.

---

## 9. Experiment 8 — Unexpected Client Disconnection

### 9.1 Technical Analysis
When a client process is terminated abruptly via `SIGKILL`, the process is unable to execute user-space shutdown logic. However, the operating system kernel reclaims all open file descriptors on process death, transmitting an orderly `FIN` segment.

The Exchange Server detects client loss reactively upon the next event loop iteration. Disconnection of one client must not interrupt active trading between surviving participants. Furthermore, resting orders submitted prior to termination must persist in the book and remain eligible for execution (§2.6).

### 9.2 Investigation Methodology & Tools
```sh
tcpdump -i lo0 -n -s0 -w exp8.pcap 'tcp port 5000' &
python3 experiment.py 8
tcpdump -r exp8.pcap -n -ttt -S | grep <killed_port>
```

### 9.3 Empirical Evidence

![Screenshot 8a](screenshots/8a.png)  
*Figure 8a: Server logs verifying client termination detection, session cleanup, and continued trade execution.*

![Screenshot 8b](screenshots/8b.png)  
*Figure 8b: Packet capture confirming kernel-issued FIN handshake following `SIGKILL`.*

**Wire Event Chronology:**
```
15960 > 5000: Flags [.]  ack 1                (Last subscriber ACK)
15960 > 5000: Flags [F.] seq 1 ack 1          (Kernel-generated FIN on SIGKILL)
5000 > 15960: Flags [.]  ack 2                (Server ACK)
5000 > 15960: Flags [F.] seq 1 ack 2          (Server FIN teardown)
15960 > 5000: Flags [.]  ack 2                (Final ACK, teardown complete)
```

**Table 10: Process Termination vs. Order Survival Event Log**

| Time (ms) | Component | Action / Observation | Architectural Significance |
| :---: | :--- | :--- | :--- |
| 3933.26 | Kernel / TCP | `SIGKILL` sent to client; kernel emits `FIN` | Process death surfaces as normal EOF |
| 3933.49 | Server Reactor | `eof_fin` detected; `close()` triggered | Descriptor closed cleanly, removed from `by_sid` |
| 3934.05 | Server Reactor | Descriptor destroyed (`nconn=3`) | Surviving connections continue uninterrupted |
| 4012.00 | Trading Engine | Subsequent BUY/SELL pairs submitted | Matching engine executes subsequent trades normally |
| 4012.10 | Market Data | Surviving subscriber receives `TRADE` updates | Broadcast pipeline remains fully functional |

### 9.4 Supplementary Proof: Resting Order Survival Post-Disconnect
Because the harness in Experiment 8 terminates a Market-Data subscriber (which holds no orders), a dedicated test verified the survival of resting trader orders:
1. Trader A connects (`sid=2`), places `BUY JNST 100 238` (order 0), and disconnects.
2. Trader B connects (`sid=3`), places `SELL JNST 60 238`.
3. Order 0 matches for 60 units. Trader B receives `SOLD JNST 60 238`, active subscribers receive `TRADE JNST 60 238`, and Trader A's private notification is safely dropped:
```
close sid=2 why='FIN' role='trader' orders_surviving=1
engine trade match: BUY 0 vs SELL 1 -> 60 JNST @ 238
notify_dropped: target_sid=2 msg=b'BOUGHT JNST 60 238\n'
```

---

## 10. System Concurrency & Protocol Verification

### 10.1 Handout §4.3 Concurrency Verification ($\ge 10$ Clients)
Handout Section 4.3 mandates simultaneous support for at least 10 active clients, including at least 2 Trader Clients and at least 4 Market-Data Clients.

This requirement was validated using `src/tools/gapd_concurrency.sh`, which launches 4 concurrent Trader Clients and 6 concurrent Market-Data Clients (10 total) against an active server. All 10 connections established and operated simultaneously without degradation:
```
$ sockstat -4 | grep :5000 | grep ESTABLISHED | wc -l
20    # 10 client endpoints + 10 server-side peer endpoints
```

### 10.2 Outbound and Inbound Message Coalescing
TCP stream framing was verified in both directions:
* **Inbound Coalescing:** Multiple complete commands transmitted in a single TCP segment (`LOGIN client_1\nBUY JNST 100 238\n`) are sequentially parsed and dispatched by `Framer.feed()`.
* **Outbound Latency Optimization:** `queue_out()` issues opportunistic flushes with `TCP_NODELAY`. Under normal conditions, replies are dispatched immediately; when peer backpressure occurs, messages coalesce automatically in userspace `wbuf`, optimizing throughput.

### 10.3 Protocol Error Vocabulary & Errno Matrix
The server handles errors strictly and uniformly across all interaction paths:

**Table 11: Operating System Errno Mapping**

| Errno | Symbol | System Event | Implementation Response |
| :---: | :--- | :--- | :--- |
| **35** | `EAGAIN` / `EWOULDBLOCK` | Socket send buffer full | Catch `BlockingIOError`, buffer in `wbuf`, arm `KQ_FILTER_WRITE`. |
| **54** | `ECONNRESET` | Peer abortive reset (`RST`) | Detected via `ev.fflags=54`, mark dead, purge session. |
| **32** | `EPIPE` | Write to closed peer | Intercepted via `BrokenPipeError`, close descriptor cleanly. |
| **60** | `ETIMEDOUT` | Peer dropped silently | Handled by OS TCP keepalive / retransmit exhaustion. |

---

## 11. Codebase Structure & Reproduction Commands

### 11.1 Source Code Architecture
```
server/run-server           Executable launcher for Exchange Server
client/run-trader           Executable launcher for Trader Client
client/run-market-data      Executable launcher for Market-Data Client
src/server.py               Main kqueue reactor, buffer manager, and network I/O
src/engine.py               Order book, matching engine, and session management
src/protocol.py             Byte-level parsing, numerical validation, and error strings
src/framing.py              Stream framing and newline delimiter extraction
src/trader.py               Interactive/automated trader client (multiplexed stdin)
src/market_data.py          Interactive/automated market data client
tests/test_all.py           Offline verification suite (framing, engine, validation)
```

### 11.2 Reproduction Commands
To run the automated experiment suite:
```sh
# Ensure launchers have execute permissions
chmod +x server/run-server client/run-trader client/run-market-data

# Execute experiments 1 through 8
python3 experiment.py 1
python3 experiment.py 2
python3 experiment.py 3
python3 experiment.py 4
python3 experiment.py 5
python3 experiment.py 6
python3 experiment.py 7
python3 experiment.py 8

# Run the 10-client concurrency test (§4.3)
sh src/tools/gapd_concurrency.sh 127.0.0.1 5000
```
