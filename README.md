# The Socket Exchange — COL334 Assignment 2

Team: 2024CS10388, `<partner roll>`

> **Status: Day-0 scaffold.** The connection layer (sockets, event loop,
> framing buffers, teardown, tracing) is in place. The application protocol
> — framing rules, command validation, order book and matching — is not yet
> implemented. Sections marked *TODO* below must be completed before submission.

---

## 1. Language and runtime

- **Python 3** (3.8 or later). No compilation step, so no Makefile is required.
- On FreeBSD: `pkg install python3`, then confirm `python3 --version` resolves.
  (The evaluator necessarily has `python3` available, since `experiment.py`
  itself is a Python script.)
- No third-party packages are required. `matplotlib` is used only by the
  optional plotting tool in `src/tools/` and is **not** needed to build or run
  the server or clients.

## 2. Build / prepare

None. The launchers invoke the interpreter directly.

Ensure the launchers are executable after checkout or unzip:

```sh
chmod +x server/run-server client/run-trader client/run-market-data
```

## 3. Running the Exchange Server

```sh
./server/run-server <host> <port>
# e.g.
./server/run-server 127.0.0.1 5000
```

The server listens on the given TCP address and port and runs until it is
signalled. It handles `SIGTERM` and `SIGINT` cleanly so that the experiment
harness's `killpg` does not truncate the trace file.

## 4. Running the clients

```sh
./client/run-trader      <host> <port> <username>
./client/run-market-data <host> <port> <instrument> [instrument ...]

# e.g.
./client/run-trader      127.0.0.1 5000 alice
./client/run-market-data 127.0.0.1 5000 JNST
```

The Trader Client sends `LOGIN <username>` on connect, then reads protocol
commands from stdin (`BUY`, `SELL`, `CANCEL`, `QUIT`) while concurrently
displaying anything the server pushes. The Market-Data Client sends
`SUBSCRIBE <instrument>` for each instrument given and then prints updates as
they arrive.

Supported instruments: `JNST`, `IMCT`.

## 5. Concurrency and I/O design

**Single-threaded event loop over `select.kqueue()`, called directly.**

- Not `selectors` — that module abstracts over kqueue/epoll/poll/select and
  hides which system call is actually in use, which is precisely what §4.1.2
  restricts.
- Not `asyncio` — banned by name in §4.1.2.
- No third-party networking libraries of any kind.

Every socket operation required by §4.1 is issued directly through Python's
`socket` module: `socket`, `bind`, `listen`, `accept`, `connect`, `send`,
`recv`, `close`, and `shutdown`.

Each connection carries its own inbound framing buffer (`rbuf`) and its own
userspace outbound buffer (`wbuf`). `KQ_FILTER_WRITE` is registered only while
`wbuf` is non-empty. The matching engine returns messages rather than writing
to sockets, so a slow reader cannot block it.

Rationale, alternatives considered, and measurements are in `report.pdf`.

## 6. Configuration

Command-line arguments are fixed by the harness, so optional behaviour is
controlled by environment variables (the launchers pass the environment
through):

| Variable | Effect |
|---|---|
| `EXCH_TRACE=<path>` | Write a structured JSONL trace to `<path>` (used to generate the report's figures). Human-readable tracing always goes to stderr. |
| `EXCH_SNDBUF=<bytes>` | Set `SO_SNDBUF` on accepted sockets. Used as an experimental control in Experiment 7. |
| `EXCH_WBUF_MAX=<bytes>` | Cap on a connection's userspace output backlog before it is disconnected as a slow consumer. Default 4 MiB. |

Example:

```sh
EXCH_TRACE=/tmp/exp7.jsonl EXCH_SNDBUF=4096 python3 experiment.py 7
```

No configuration is required for normal operation; defaults are used when the
variables are unset.

## 7. Repository layout

```
server/run-server            launcher (mandatory, §7.2)
client/run-trader            launcher (mandatory, §7.2)
client/run-market-data       launcher (mandatory, §7.2)
src/server.py                exchange server: event loop + connection layer
src/tracelog.py              dual-channel trace logger (stderr + JSONL)
src/trader.py                trader client
src/market_data.py           market-data client
src/tools/baseline.sh        capture OS/sysctl baseline for the report
src/tools/check_structure.sh pre-submission structure validation
src/tools/make_zip.sh        build the correctly-named submission archive
README.md
report.pdf                   experiment report (TODO)
```

*TODO (Phase 1–2):* `src/framing.py`, `src/protocol.py`, `src/engine.py`,
`tests/`.

## 8. Development helpers

```sh
sh src/tools/baseline.sh > baseline.txt      # run inside the FreeBSD VM
sh src/tools/check_structure.sh              # pre-submission validation
sh src/tools/make_zip.sh 2024CS10388 <partner-roll>
```

## 9. Protocol error vocabulary

`<reason>` in `ERROR <reason>` is free-form per the handout — no fixed
vocabulary is specified. This implementation uses one fixed set everywhere
(defined once in `src/protocol.py`), so replies are greppable in test output
and in the report rather than ad hoc per call site:

| reason | when |
|---|---|
| `unknown_command` | verb is not one of LOGIN/BUY/SELL/CANCEL/SUBSCRIBE/UNSUBSCRIBE/QUIT |
| `bad_arity` | wrong number of tokens for the verb |
| `bad_instrument` | instrument is not `JNST` or `IMCT` |
| `bad_quantity` | quantity fails strict validation or is outside [1, 2147483647] |
| `bad_price` | price fails strict validation or is outside [1, 2147483647] |
| `bad_order_id` | order id fails strict validation or is outside [0, 2147483647] |
| `not_logged_in` | BUY/SELL/CANCEL attempted before a successful LOGIN |
| `duplicate_username` | LOGIN name is already in use by a currently-connected Trader |
| `wrong_role` | a command not permitted for this connection's role (e.g. SUBSCRIBE from a Trader) |
| `unknown_order` | CANCEL references an order id that never existed |
| `order_not_cancellable` | CANCEL references an order that is already fully filled or cancelled |
| `not_owner` | CANCEL references another trader's order |
| `line_too_long` | inbound buffer exceeded the line cap with no `\n` seen |

"Strict validation" means the token must match `^[0-9]+$` before range-checking
— `int()` alone is not used, because it accepts input the protocol does not
intend to allow (a leading `+`, PEP 515 underscores such as `100_0`, and
leading/trailing whitespace all parse successfully with plain `int()`).

## 10. Design decisions where the spec is silent

The handout does not specify these cases; the following choices are used
consistently throughout the implementation and are noted here so a reader
does not have to infer them from behaviour:

- **A command requiring login, sent before LOGIN succeeds, is rejected**
  with `ERROR not_logged_in` rather than silently allowed.
- **Verb matching is exact-case.** Only uppercase `LOGIN`, `BUY`, etc. are
  recognised; anything else is `unknown_command`.
- **`SUBSCRIBE`/`UNSUBSCRIBE` are idempotent.** Subscribing twice, or
  unsubscribing from an instrument not currently subscribed to, both return
  `OK` rather than an error.
- **Self-matching is allowed.** If the same trader's BUY and SELL cross
  (same instrument, opposite sides, equal price), the trade executes and
  that trader receives both `BOUGHT` and `SOLD`. The protocol specifies no
  self-trade prevention, so none is implemented.
