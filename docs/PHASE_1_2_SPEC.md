# Phase 1 + 2 Technical Specification — `framing.py`, `protocol.py`, `engine.py`

**Status:** locked reference for Codex (implementation) and Antigravity (test execution, local shell). This document is the source of truth for Phase 1/2; if code and this doc disagree, this doc wins unless a change is explicitly re-agreed and this file is updated.

**Boundary invariant:** `engine.on_disconnect(sid)` is the sole authority for session and subscription lifecycle cleanup. `server.kill()` delegates to it and does not itself mutate role/subs/username state (already applied in `src/server.py`).

---

## Amendments to the original draft spec

Six places where the literal wording was tightened or corrected — deliberate, not typos:

1. **Regex:** use `re.fullmatch(rb'[0-9]+', tok)`, not `re.match(rb'^[0-9]+$', tok)`. Python's `$` (without `re.MULTILINE`) matches before a trailing `\n` as well as at true end-of-string — `re.match(rb'^[0-9]+$', b'123\n')` actually succeeds. `fullmatch` has no such trap and needs no anchors.

2. **`Framer.feed()`'s buffer compaction must happen eagerly, before the first `yield`, not per-line inside the scan loop.** A naive scan-yield-delete-per-iteration generator leaves the buffer only partially compacted if the caller `break`s out of the consuming `for` loop early — which is a real code path in `server.py` (`if c.dead: break` after a command triggers `kill()` mid-batch), not a hypothetical. The mandated pattern does the entire scan and the single compaction synchronously, then `yield from lines`, so correctness does not depend on the caller consuming every yielded item.

3. **Full role state-transition table added** (below) — the original draft under-specified several `(role, verb)` cells. This is exactly the "protocol state machine logic" scope; ambiguity here is precisely what would make Codex's implementation and Antigravity's tests drift apart.

4. **Two bugs fixed in the original `VALID_CASES` stub:** the `SELL` row asserted a hardcoded `ORDER_ACCEPTED 1`, which is only true if run after the `BUY` row against the same engine instance — wrong for an isolated parametrized case (fresh engine ⇒ first order ⇒ id `0`). And the double-space row (`b"BUY JNST  100 238"`) was marked as an error case, but `bytes.split()` already collapses whitespace runs, so it is in fact a *valid* command — repurposed as a positive whitespace-tolerance test. Both fixed in `tests/test_all.py` as written to disk.

5. **Three-way `ERROR <reason>` ownership boundary made explicit:**
   - `server.py` owns `line_too_long` (transport-level, from `Framer.overflowed()`)
   - `protocol.py` owns the 6 **syntax** reasons and is fully stateless: `unknown_command`, `bad_arity`, `bad_instrument`, `bad_quantity`, `bad_price`, `bad_order_id`
   - `engine.py` owns the 6 **semantic/state** reasons: `not_logged_in`, `duplicate_username`, `wrong_role`, `unknown_order`, `order_not_cancellable`, `not_owner`

   This matters concretely: `protocol.py` has no access to the `usernames` set, so `duplicate_username` structurally cannot be checked there.

6. **`Conn.role` vs. `engine.Session.role` duplication flagged, not resolved.** Nothing currently mutates `Conn.role` (that wiring is Phase 3's `handle_readable_bytes()`), so this doesn't affect Phase 1/2 correctness. A code comment in `server.kill()` flags the decision point for when Phase 3 lands: prefer reading role from `engine.sessions` (captured before `on_disconnect()` pops it) over maintaining a second, parallel field.

---

## `src/framing.py`

**Invariant: no `socket` import. Bytes in, bytes out — no decoding to `str` anywhere.**

```python
class Framer:
    def __init__(self, max_line: int = 4096):
        self.buf = bytearray()
        self.max_line = max_line

    def feed(self, data: bytes):
        """
        Yield each complete line (bytes, without the trailing '\n', and
        without a trailing '\r' if one was present) found after appending
        `data` to the internal buffer.

        MANDATORY shape:

            self.buf += data
            lines = []
            start = 0
            while True:
                nl = self.buf.find(b'\n', start)
                if nl < 0:
                    break
                line = bytes(self.buf[start:nl])
                if line.endswith(b'\r'):
                    line = line[:-1]
                lines.append(line)
                start = nl + 1
            if start:
                del self.buf[:start]        # ONE compaction per feed() call
            yield from lines

        Why exactly this shape: everything before `yield from lines` runs
        synchronously on the caller's first next() -- i.e. as soon as
        iteration begins, even if zero lines are found -- so self.buf is
        always fully appended-to and compacted after feed() is called,
        regardless of how many yielded items the caller actually consumes
        (including an early `break`).
        """

    def overflowed(self) -> bool:
        """
        True if the current unterminated residual exceeds max_line. Pure
        function of current state -- safe to call any time. Caller must
        check this immediately after consuming each feed() call and, if
        True, reply ERROR line_too_long and kill() the connection before
        further data can arrive.
        """
        return len(self.buf) > self.max_line
```

`framing.py` performs zero semantic validation. It does not know what a valid command is, does not reject empty lines. That is entirely `protocol.py`'s job.

---

## `src/protocol.py`

Additions to the existing constants file (`REASON_*`, `JNST`/`IMCT`, ranges — already on disk).

### `parse_u32`

```python
import re

_U32_RE = re.compile(rb'[0-9]+')

def parse_u32(tok: bytes, lo: int, hi: int) -> int | None:
    if not _U32_RE.fullmatch(tok):
        return None
    val = int(tok)   # safe: the regex already restricted tok to pure ASCII
                       # digits -- no sign, no PEP 515 underscore, no decimal
                       # point can match [0-9]+, and there is no "unicode
                       # digit" byte sequence that matches ASCII 0x30-0x39 in
                       # a bytes pattern. Staying in bytes end-to-end removes
                       # that whole trap category structurally.
    return val if lo <= val <= hi else None
```

Truth table (verbatim — do not second-guess any row):

| input | result | why |
|---|---|---|
| `b"5"` | `5` (if in range) | — |
| `b"05"` | `5` (if in range) | leading zeros accepted; §2.1 doesn't forbid them |
| `b""` | `None` | regex requires 1+ digits |
| `b"+5"` | `None` | `+` not in `[0-9]` |
| `b"-5"` | `None` | same |
| `b"5.0"` | `None` | `.` not in `[0-9]` |
| `b"1_000"` | `None` | `_` not in `[0-9]` (PEP 515) |
| `b" 5"` / `b"5 "` | **unreachable** | tokens come from `line.split()`, which can never produce a token with internal/boundary whitespace — this trap is eliminated upstream, not by this function |
| non-ASCII digit byte sequences | `None` | impossible by construction in a `bytes` pattern |
| `b"2147483647"`, `hi=2147483647` | `2147483647` | exactly at max |
| `b"2147483648"`, `hi=2147483647` | `None` | one past max |
| `b"0"`, `lo=1` | `None` | below min |

### `parse_line`

```python
def parse_line(line: bytes) -> tuple[bool, tuple | bytes]:
    """
    Returns exactly one of:
        (True,  (VERB, *validated_args))
        (False, REASON_BYTES)   -- one of the 6 SYNTAX reasons only

    100% STATELESS: no side effects, no knowledge of role, login status,
    usernames, or the order book. Can NEVER return not_logged_in /
    duplicate_username / wrong_role / unknown_order / order_not_cancellable /
    not_owner -- those belong to engine.py.

    Validation precedence, checked in this order, short-circuit on first
    failure (documented DECISION where the handout is silent):
        1. tokens = line.split()
        2. if not tokens: return (False, REASON_UNKNOWN_COMMAND)
        3. verb = tokens[0]; exact-uppercase match against the 7 known
           verbs, else (False, REASON_UNKNOWN_COMMAND)
        4. if len(tokens) != expected_arity[verb]: (False, REASON_BAD_ARITY)
        5. field validation, LEFT TO RIGHT as tokens appear on the wire
    """
```

| verb | arity | success payload |
|---|---|---|
| `LOGIN` | 2 | `(b"LOGIN", username: bytes)` |
| `BUY` | 4 | `(b"BUY", instr: int, qty: int, price: int)` |
| `SELL` | 4 | `(b"SELL", instr: int, qty: int, price: int)` |
| `CANCEL` | 2 | `(b"CANCEL", order_id: int)` |
| `SUBSCRIBE` | 2 | `(b"SUBSCRIBE", instr: int)` |
| `UNSUBSCRIBE` | 2 | `(b"UNSUBSCRIBE", instr: int)` |
| `QUIT` | 1 | `(b"QUIT",)` |

`instr` in the success payload is always the mapped `int` code (`JNST=0`/`IMCT=1`), never raw bytes — `engine.py` never sees `b"JNST"`.

---

## `src/engine.py`

**Invariant: no `socket` import. Every public method returns `list[tuple[int, bytes]]`, including error cases — an `ERROR ...` reply is just another outbound message addressed to the sender, never a special-cased return shape or an exception.**

### Role state machine (exhaustive — closes every ambiguity)

| current role | verb | outcome |
|---|---|---|
| `UNTYPED` | `LOGIN` | username taken → `ERROR duplicate_username` (role stays `UNTYPED`); else commit role→`TRADER`, add to `usernames`, reply `OK` |
| `UNTYPED` | `BUY`/`SELL`/`CANCEL` | `ERROR not_logged_in` |
| `UNTYPED` | `SUBSCRIBE` | commit role→`MARKETDATA`, add to `subs[instr]`, reply `OK` |
| `UNTYPED` | `UNSUBSCRIBE` | `OK` (idempotent no-op; does **not** commit a role) |
| `UNTYPED` | `QUIT` | `[]`; transport closes |
| `TRADER` | `LOGIN` | `ERROR wrong_role` (no re-login; reuses existing vocabulary) |
| `TRADER` | `BUY`/`SELL` | matched per algorithm below |
| `TRADER` | `CANCEL` | per `on_cancel` |
| `TRADER` | `SUBSCRIBE`/`UNSUBSCRIBE` | `ERROR wrong_role` |
| `TRADER` | `QUIT` | `[]`; resting orders survive (§2.6) |
| `MARKETDATA` | `LOGIN`/`BUY`/`SELL`/`CANCEL` | `ERROR wrong_role` |
| `MARKETDATA` | `SUBSCRIBE` | add to `subs[instr]` (idempotent), reply `OK` |
| `MARKETDATA` | `UNSUBSCRIBE` | discard from `subs[instr]` (idempotent), reply `OK` |
| `MARKETDATA` | `QUIT` | `[]` |

### Data structures

```python
JNST, IMCT = 0, 1
BUY, SELL  = 0, 1
UNTYPED, TRADER, MARKETDATA = 0, 1, 2

class Order:
    __slots__ = ('id', 'owner_sid', 'owner_user', 'instr', 'side',
                 'price', 'qty_left', 'cancelled')
    # owner_user is denormalised onto Order (not looked up via sessions) so
    # it survives session removal on disconnect. Internal tracing ONLY --
    # wire messages (BOUGHT/SOLD/TRADE) never include a username.

class Session:
    __slots__ = ('role', 'username')
    def __init__(self):
        self.role = UNTYPED
        self.username = None

class Engine:
    def __init__(self):
        self.orders    = {}                       # oid -> Order
        self.level     = {}                        # (instr, side, price) -> deque[oid]
        self.subs      = {JNST: set(), IMCT: set()}
        self.usernames = set()
        self.sessions  = {}                         # sid -> Session, lazy
        self.next_id   = 0
```

No explicit `on_connect` — a `Session` is created lazily via `self.sessions.setdefault(sid, Session())` on the first `handle()` call for that `sid`.

### Public dispatcher

```python
def handle(self, sid: int, parsed) -> list[tuple[int, bytes]]:
    """`parsed` is protocol.parse_line()'s SUCCESS payload only. Callers
    check the ok flag themselves; on failure they queue_out the REASON_*
    directly and never call handle() -- syntax errors never reach the
    engine."""
    session = self.sessions.setdefault(sid, Session())
    verb = parsed[0]

    if verb == b"QUIT":
        return []

    if verb == b"LOGIN":
        return self._on_login(sid, session, parsed[1])

    if verb in (b"BUY", b"SELL"):
        if session.role not in (UNTYPED, TRADER):
            return [(sid, b"ERROR wrong_role\n")]
        if session.role == UNTYPED:
            return [(sid, b"ERROR not_logged_in\n")]
        side = BUY if verb == b"BUY" else SELL
        _, instr, qty, price = parsed
        return self._on_buy_sell(sid, side, instr, qty, price)

    if verb == b"CANCEL":
        if session.role not in (UNTYPED, TRADER):
            return [(sid, b"ERROR wrong_role\n")]
        if session.role == UNTYPED:
            return [(sid, b"ERROR not_logged_in\n")]
        return self.on_cancel(sid, parsed[1])

    if verb in (b"SUBSCRIBE", b"UNSUBSCRIBE"):
        if session.role not in (UNTYPED, MARKETDATA):
            return [(sid, b"ERROR wrong_role\n")]
        return self._on_sub_unsub(sid, session, verb, parsed[1])

    raise AssertionError("unreachable: protocol.py's 7-verb set must match this dispatch")
```

By the time `_on_buy_sell`/`on_cancel` run, `session.role == TRADER` is guaranteed.

```python
def _on_login(self, sid, session, username: bytes):
    if session.role != UNTYPED:
        return [(sid, b"ERROR wrong_role\n")]
    if username in self.usernames:
        return [(sid, b"ERROR duplicate_username\n")]
    session.role = TRADER
    session.username = username
    self.usernames.add(username)
    return [(sid, b"OK\n")]

def _on_sub_unsub(self, sid, session, verb, instr):
    if session.role == UNTYPED and verb == b"SUBSCRIBE":
        session.role = MARKETDATA
    if verb == b"SUBSCRIBE":
        self.subs[instr].add(sid)
    else:
        self.subs[instr].discard(sid)
    return [(sid, b"OK\n")]
```

### Matching algorithm

```python
def _on_buy_sell(self, sid, side, instr, qty, price):
    out = []
    oid = self.next_id
    self.next_id += 1
    session = self.sessions[sid]
    o = Order()
    o.id, o.owner_sid, o.owner_user = oid, sid, session.username
    o.instr, o.side, o.price, o.qty_left, o.cancelled = instr, side, price, qty, False

    out.append((sid, b"ORDER_ACCEPTED %d\n" % oid))   # FIRST, before matching (§2.6)
    self.orders[oid] = o

    opp_side = SELL if side == BUY else BUY
    dq = self.level.get((instr, opp_side, price))
    while o.qty_left > 0 and dq:
        rid = dq[0]
        r = self.orders.get(rid)
        if r is None or r.cancelled or r.qty_left == 0:
            dq.popleft()
            continue
        q = min(o.qty_left, r.qty_left)
        o.qty_left -= q
        r.qty_left -= q
        buyer_sid, seller_sid = (sid, r.owner_sid) if side == BUY else (r.owner_sid, sid)
        # Deterministic emission order for a self-match: BOUGHT always
        # precedes SOLD, regardless of which side is the incoming order.
        out.append((buyer_sid,  b"BOUGHT %s %d %d\n" % (INSTRUMENT_NAME[instr], q, price)))
        out.append((seller_sid, b"SOLD %s %d %d\n"   % (INSTRUMENT_NAME[instr], q, price)))
        for sub_sid in self.subs[instr]:
            out.append((sub_sid, b"TRADE %s %d %d\n" % (INSTRUMENT_NAME[instr], q, price)))
        if r.qty_left == 0:
            dq.popleft()
    if o.qty_left > 0:
        self.level.setdefault((instr, side, price), deque()).append(oid)
    return out
```

One `(BOUGHT, SOLD, TRADE×N)` triple per fill, in fill order, never aggregated. A self-match needs no special-casing — `buyer_sid`/`seller_sid` happen to be equal and both notifications land on the same `sid` correctly. The order of tuples in the returned list is the order `server.py`'s Phase 3 wiring calls `queue_out()` in — this is what artifact F4 (one BUY sweeping 3 resting SELLs, all arriving in one segment) depends on.

### Cancellation

```python
def on_cancel(self, sid, order_id):
    o = self.orders.get(order_id)
    if o is None:
        return [(sid, b"ERROR unknown_order\n")]
    if o.owner_sid != sid:
        return [(sid, b"ERROR not_owner\n")]           # existence before ownership
    if o.cancelled or o.qty_left == 0:
        return [(sid, b"ERROR order_not_cancellable\n")]
    o.cancelled = True
    o.qty_left = 0
    return [(sid, b"ORDER_CANCELLED %d\n" % order_id)]
```

Precedence fixed: `unknown_order` → `not_owner` → `order_not_cancellable`. No deque scan on cancel; the tombstone is reaped lazily on the next sweep of that price level.

### Disconnect

```python
def on_disconnect(self, sid: int) -> int:
    session = self.sessions.pop(sid, None)
    if session is not None and session.role == TRADER and session.username:
        self.usernames.discard(session.username)
    for instr_set in self.subs.values():
        instr_set.discard(sid)
    # orders / level ARE NOT TOUCHED -- resting orders survive (§2.6)
    return sum(1 for o in self.orders.values()
               if o.owner_sid == sid and o.qty_left > 0 and not o.cancelled)
```

Safe for a `sid` the engine has never seen (idle connection killed before completing a framed line — Exp 1, Exp 4's Client 1): `.pop`/`.discard` are no-ops, `sum()` correctly evaluates to `0`. O(total orders) scan is acceptable at this assignment's scale; a per-`sid` index would be the production fix, not required here.

**Disconnect-then-match contract:** when a disconnected trader's resting order later fills, `_on_buy_sell` emits `(that_sid, b"BOUGHT...")` completely normally — the engine has no notion the trader is gone. `server.py`'s `queue_out()`, via its `by_sid.get(sid)` lookup, is what silently drops it. Market-Data subscribers, looked up independently via `self.subs[instr]`, are unaffected.

---

## Integration preview (Phase 3, informative only — not a task here)

For reference, showing how Sections above compose once wired:

```python
for line in c.framer.feed(data):
    ok, payload = protocol.parse_line(line)
    if not ok:
        self.queue_out(c.sid, b"ERROR %s\n" % payload)
        continue
    for sid, msg in self.engine.handle(c.sid, payload):
        self.queue_out(sid, msg)
    if c.dead:
        break
if c.framer.overflowed():
    self.queue_out(c.sid, b"ERROR line_too_long\n")
    self.kill(c, "overflow")
    return
```

---

## Exit gate

`pytest` all-green on `tests/test_all.py`, plus:

```sh
grep -ln 'import socket' src/*.py   # must print ONLY server.py, trader.py, market_data.py
```
