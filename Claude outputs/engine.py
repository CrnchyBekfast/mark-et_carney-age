"""
Matching engine + session/role state machine -- COL334 A2 (roadmap §4,
PHASE_1_2_SPEC.md).

Invariant: no `socket` import. Every public method returns
list[tuple[int, bytes]], including error cases -- an ERROR ... reply is just
another outbound message addressed to the sender, never a special-cased
return shape or an exception.

Boundary invariant: on_disconnect(sid) is the sole authority for session and
subscription lifecycle cleanup. server.kill() delegates to it and does not
itself mutate role/subs/username state.
"""

from collections import deque

from protocol import (
    JNST, IMCT, BUY, SELL, INSTRUMENT_NAME,
    REASON_NOT_LOGGED_IN, REASON_DUPLICATE_USERNAME, REASON_WRONG_ROLE,
    REASON_UNKNOWN_ORDER, REASON_ORDER_NOT_CANCELLABLE, REASON_NOT_OWNER,
)

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
        self.orders = {}                          # oid -> Order
        self.level = {}                            # (instr, side, price) -> deque[oid]
        self.subs = {JNST: set(), IMCT: set()}
        self.usernames = set()
        self.sessions = {}                          # sid -> Session, lazy
        self.next_id = 0

    # No explicit on_connect -- a Session is created lazily via
    # self.sessions.setdefault(sid, Session()) on the first handle() call
    # for that sid.

    def handle(self, sid: int, parsed) -> list:
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
                return [(sid, b"ERROR %s\n" % REASON_WRONG_ROLE)]
            if session.role == UNTYPED:
                return [(sid, b"ERROR %s\n" % REASON_NOT_LOGGED_IN)]
            side = BUY if verb == b"BUY" else SELL
            _, instr, qty, price = parsed
            return self._on_buy_sell(sid, side, instr, qty, price)

        if verb == b"CANCEL":
            if session.role not in (UNTYPED, TRADER):
                return [(sid, b"ERROR %s\n" % REASON_WRONG_ROLE)]
            if session.role == UNTYPED:
                return [(sid, b"ERROR %s\n" % REASON_NOT_LOGGED_IN)]
            return self.on_cancel(sid, parsed[1])

        if verb in (b"SUBSCRIBE", b"UNSUBSCRIBE"):
            if session.role not in (UNTYPED, MARKETDATA):
                return [(sid, b"ERROR %s\n" % REASON_WRONG_ROLE)]
            return self._on_sub_unsub(sid, session, verb, parsed[1])

        raise AssertionError("unreachable: protocol.py's 7-verb set must match this dispatch")

    # By the time _on_buy_sell/on_cancel run, session.role == TRADER is
    # guaranteed.

    def _on_login(self, sid, session, username: bytes):
        if session.role != UNTYPED:
            return [(sid, b"ERROR %s\n" % REASON_WRONG_ROLE)]
        if username in self.usernames:
            return [(sid, b"ERROR %s\n" % REASON_DUPLICATE_USERNAME)]
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
            out.append((buyer_sid, b"BOUGHT %s %d %d\n" % (INSTRUMENT_NAME[instr], q, price)))
            out.append((seller_sid, b"SOLD %s %d %d\n" % (INSTRUMENT_NAME[instr], q, price)))
            for sub_sid in self.subs[instr]:
                out.append((sub_sid, b"TRADE %s %d %d\n" % (INSTRUMENT_NAME[instr], q, price)))
            if r.qty_left == 0:
                dq.popleft()
        if o.qty_left > 0:
            self.level.setdefault((instr, side, price), deque()).append(oid)
        return out

    def on_cancel(self, sid, order_id):
        o = self.orders.get(order_id)
        if o is None:
            return [(sid, b"ERROR %s\n" % REASON_UNKNOWN_ORDER)]
        if o.owner_sid != sid:
            return [(sid, b"ERROR %s\n" % REASON_NOT_OWNER)]           # existence before ownership
        if o.cancelled or o.qty_left == 0:
            return [(sid, b"ERROR %s\n" % REASON_ORDER_NOT_CANCELLABLE)]
        o.cancelled = True
        o.qty_left = 0
        return [(sid, b"ORDER_CANCELLED %d\n" % order_id)]

    def on_disconnect(self, sid: int) -> int:
        session = self.sessions.pop(sid, None)
        if session is not None and session.role == TRADER and session.username:
            self.usernames.discard(session.username)
        for instr_set in self.subs.values():
            instr_set.discard(sid)
        # orders / level ARE NOT TOUCHED -- resting orders survive (§2.6)
        return sum(1 for o in self.orders.values()
                   if o.owner_sid == sid and o.qty_left > 0 and not o.cancelled)
