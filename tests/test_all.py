"""
Offline protocol/engine test suite -- COL334 A2 (roadmap §3, §9 artifact T4).

Runs with NO sockets and NO event loop: everything here exercises framing.py /
protocol.py / engine.py directly.

    pytest

Contracts are locked in docs/PHASE_1_2_SPEC.md -- this file tests exactly
that spec, including the six amendments made to the original draft (see that
doc's "Amendments" section for why VALID_CASES differs from an earlier
version of this stub).
"""

import random

import pytest

from framing import Framer
from protocol import parse_line
import engine


def submit(eng, sid, line: bytes):
    """
    Mirrors server.py's real Phase-3 wiring exactly: parse the line, and on
    a syntax error, synthesize the ERROR reply the same way queue_out()
    would -- without ever calling engine.handle(). This makes these
    "engine tests" true end-to-end tests of the whole non-transport stack,
    exactly as it will run in production.
    """
    ok, payload = parse_line(line)
    if not ok:
        return [(sid, b"ERROR %s\n" % payload)]
    return eng.handle(sid, payload)


def submit_all(eng, sid, lines):
    """Submit a sequence of raw protocol lines, returning only the LAST
    line's reply list. Used to establish preconditions (e.g. LOGIN) before
    the line actually under test."""
    out = []
    for ln in lines:
        out = submit(eng, sid, ln)
    return out


# ---------------------------------------------------------------------------
# Framing  (roadmap §3, artifact T4)
#
# These prove fragmentation handling across every possible split, not just
# the one case experiment.py's own Experiment 3 happens to exercise.
# ---------------------------------------------------------------------------

def test_frag_all_split_positions():
    """Every one of the N-1 possible two-way split positions of a
    multi-message stream must reassemble identically.
    Report line this earns: "N-1/N-1 split positions parse identically."
    """
    msg = b"LOGIN alice\nBUY JNST 100 238\nSELL JNST 50 238\n"
    expected = [b"LOGIN alice", b"BUY JNST 100 238", b"SELL JNST 50 238"]
    for i in range(1, len(msg)):
        fr = Framer()
        out = list(fr.feed(msg[:i])) + list(fr.feed(msg[i:]))
        assert out == expected, "split at byte %d produced %r" % (i, out)


def test_frag_random_multiway_splits():
    """1000 random fragmentations into 1-8 chunks, seeded for reproducibility.
    Report line this earns: "1000/1000 random fragmentations identical."
    """
    msg = b"LOGIN alice\nBUY JNST 100 238\nCANCEL 7\nQUIT\n"
    expected = [b"LOGIN alice", b"BUY JNST 100 238", b"CANCEL 7", b"QUIT"]
    rng = random.Random(0)
    for trial in range(1000):
        n_cuts = rng.randint(0, min(7, len(msg) - 1))
        cuts = sorted(rng.sample(range(1, len(msg)), k=n_cuts))
        bounds = [0] + cuts + [len(msg)]
        chunks = [msg[bounds[i]:bounds[i + 1]] for i in range(len(bounds) - 1)]
        fr = Framer()
        out = []
        for chunk in chunks:
            out.extend(fr.feed(chunk))
        assert out == expected, "trial %d chunks=%r -> %r" % (trial, chunks, out)


def test_coalesce_many_messages_one_feed():
    """The REVERSE direction of §2.9: many complete messages in a single
    feed() call must all be emitted, not just the first."""
    msg = b"LOGIN alice\nBUY JNST 100 238\nSELL JNST 50 238\nQUIT\n"
    fr = Framer()
    out = list(fr.feed(msg))
    assert out == [b"LOGIN alice", b"BUY JNST 100 238", b"SELL JNST 50 238", b"QUIT"]


def test_boundary_split_at_newline():
    """Three boundary cases: split immediately before '\\n', immediately
    after, and '\\n' alone in its own chunk (experiment.py's Exp 3 pattern)."""
    msg = b"BUY JNST 100 238\n"

    fr = Framer()
    out = list(fr.feed(msg[:-1])) + list(fr.feed(msg[-1:]))
    assert out == [b"BUY JNST 100 238"]

    fr = Framer()
    out = list(fr.feed(msg)) + list(fr.feed(b""))
    assert out == [b"BUY JNST 100 238"]

    fr = Framer()
    out = (list(fr.feed(b"BUY ")) + list(fr.feed(b"JNST 100")) +
           list(fr.feed(b" 238")) + list(fr.feed(b"\n")))
    assert out == [b"BUY JNST 100 238"]


def test_overflow_bounded_rbuf():
    """A stream with no '\\n' at all, larger than max_line, must be flagged
    as overflowed rather than growing the buffer without bound."""
    fr = Framer(max_line=4096)
    out = list(fr.feed(b"X" * 8192))
    assert out == []
    assert fr.overflowed() is True
    assert len(fr.buf) == 8192


# ---------------------------------------------------------------------------
# Validation  (roadmap §3, §2.1 range gates)
#
# Each row: (setup_lines, line, expected_reply). setup_lines establishes
# preconditions (e.g. LOGIN) against a FRESH engine so every row is fully
# independent -- no row depends on another row having run first.
#
# Corrected vs. an earlier draft of this table (see docs/PHASE_1_2_SPEC.md
# amendment 4): the SELL row no longer hardcodes an id that only made sense
# if run after the BUY row, and the double-space row is now a POSITIVE test
# (bytes.split() already tolerates it -- it was never actually an error).
# ---------------------------------------------------------------------------

VALID_CASES = [
    ([], b"LOGIN alice", b"OK"),
    ([b"LOGIN alice"], b"BUY JNST 100 238", b"ORDER_ACCEPTED 0"),
    ([b"LOGIN alice"], b"SELL JNST 50 238", b"ORDER_ACCEPTED 0"),   # own fresh engine -> id 0
    ([], b"HELLO alice", b"ERROR unknown_command"),
    ([], b"buy JNST 100 238", b"ERROR unknown_command"),             # exact-case only
    ([b"LOGIN alice"], b"BUY JNST 100", b"ERROR bad_arity"),
    ([], b"LOGIN", b"ERROR bad_arity"),
    ([b"LOGIN alice"], b"BUY FAKE 100 238", b"ERROR bad_instrument"),
    ([b"LOGIN alice"], b"BUY JNST 0 238", b"ERROR bad_quantity"),
    ([b"LOGIN alice"], b"BUY JNST 2147483648 238", b"ERROR bad_quantity"),
    ([b"LOGIN alice"], b"BUY JNST 2147483647 238", b"ORDER_ACCEPTED 0"),
    ([b"LOGIN alice"], b"BUY JNST 100 0", b"ERROR bad_price"),
    ([b"LOGIN alice"], b"CANCEL 2147483648", b"ERROR bad_order_id"),
    ([b"LOGIN alice"], b"BUY JNST +100 238", b"ERROR bad_quantity"),
    ([b"LOGIN alice"], b"BUY JNST 100_0 238", b"ERROR bad_quantity"),
    ([b"LOGIN alice"], b"BUY JNST 100.0 238", b"ERROR bad_quantity"),
    ([b"LOGIN alice"], b"BUY  JNST   100    238", b"ORDER_ACCEPTED 0"),  # whitespace tolerated
    ([b"LOGIN alice"], b"SUBSCRIBE JNST", b"ERROR wrong_role"),          # TRADER can't subscribe
]


@pytest.mark.parametrize("setup,line,expected", VALID_CASES)
def test_valid_table(setup, line, expected):
    eng = engine.Engine()
    sid = 1
    submit_all(eng, sid, setup)
    out = submit(eng, sid, line)
    assert len(out) >= 1
    got = out[0][1].rstrip(b"\n")
    assert got == expected, "line=%r setup=%r -> got %r, expected %r" % (
        line, setup, got, expected)


# ---------------------------------------------------------------------------
# Engine  (roadmap §4, §2.6 matching + order survival)
# ---------------------------------------------------------------------------

def test_engine_handout_example():
    """The handout's own worked example (§2.6): BUY 100 vs SELL 60 must
    produce a trade of 60 @ 238, with 40 remaining on the buy side."""
    eng = engine.Engine()
    A, B = 1, 2
    submit(eng, A, b"LOGIN alice")
    submit(eng, B, b"LOGIN bob")
    out1 = submit(eng, A, b"BUY JNST 100 238")
    assert out1[0] == (A, b"ORDER_ACCEPTED 0\n")
    out2 = submit(eng, B, b"SELL JNST 60 238")
    assert out2[0] == (B, b"ORDER_ACCEPTED 1\n")
    assert (A, b"BOUGHT JNST 60 238\n") in out2
    assert (B, b"SOLD JNST 60 238\n") in out2
    remaining = eng.orders[0]
    assert remaining.qty_left == 40
    assert remaining.cancelled is False


def test_engine_multi_fill_sweep():
    """One incoming order sweeping two resting orders at the same price
    must produce two independent fill notifications, not one aggregated
    pair."""
    eng = engine.Engine()
    A, B, C = 1, 2, 3
    submit(eng, A, b"LOGIN alice")
    submit(eng, B, b"LOGIN bob")
    submit(eng, C, b"LOGIN carol")
    submit(eng, B, b"SELL JNST 60 238")        # oid 0, resting
    submit(eng, C, b"SELL JNST 40 238")        # oid 1, resting
    out = submit(eng, A, b"BUY JNST 100 238")   # oid 2, sweeps both
    assert out[0] == (A, b"ORDER_ACCEPTED 2\n")
    assert out.count((A, b"BOUGHT JNST 60 238\n")) == 1
    assert out.count((B, b"SOLD JNST 60 238\n")) == 1
    assert out.count((A, b"BOUGHT JNST 40 238\n")) == 1
    assert out.count((C, b"SOLD JNST 40 238\n")) == 1
    assert eng.orders[2].qty_left == 0


def test_engine_cancel_partial():
    """CANCEL on an order with unfulfilled remaining quantity succeeds."""
    eng = engine.Engine()
    A = 1
    submit(eng, A, b"LOGIN alice")
    submit(eng, A, b"BUY JNST 100 238")          # oid 0, fully resting
    out = submit(eng, A, b"CANCEL 0")
    assert out == [(A, b"ORDER_CANCELLED 0\n")]
    assert eng.orders[0].cancelled is True
    assert eng.orders[0].qty_left == 0


def test_engine_cancel_already_filled():
    """CANCEL on a fully-filled order -> ERROR order_not_cancellable."""
    eng = engine.Engine()
    A, B = 1, 2
    submit(eng, A, b"LOGIN alice")
    submit(eng, B, b"LOGIN bob")
    submit(eng, A, b"BUY JNST 60 238")           # oid 0
    submit(eng, B, b"SELL JNST 60 238")          # oid 1, fully fills oid 0
    out = submit(eng, A, b"CANCEL 0")
    assert out == [(A, b"ERROR order_not_cancellable\n")]


def test_engine_cancel_not_owner():
    """CANCEL of another trader's order -> ERROR not_owner. Also checks
    precedence: an unknown order id is reported as unknown_order even for a
    non-owner requester -- existence is checked before ownership."""
    eng = engine.Engine()
    A, B = 1, 2
    submit(eng, A, b"LOGIN alice")
    submit(eng, B, b"LOGIN bob")
    submit(eng, A, b"BUY JNST 100 238")          # oid 0, owned by A
    out = submit(eng, B, b"CANCEL 0")
    assert out == [(B, b"ERROR not_owner\n")]
    assert eng.orders[0].cancelled is False       # untouched

    out2 = submit(eng, B, b"CANCEL 999")          # never existed
    assert out2 == [(B, b"ERROR unknown_order\n")]


def test_engine_disconnect_then_match():
    """
    THE §2.6 proof, and the one experiment.py's own harness never exercises
    (its Experiment 8 kills a Market-Data client, which owns no orders).

    Trader A: BUY JNST 100 238, then disconnects.
    Trader B: SELL JNST 60 238.
    Assert:
      - the trade executes (A's resting order is untouched by the disconnect)
      - a subscribed Market-Data client receives TRADE JNST 60 238
      - the engine still ADDRESSES A's BOUGHT normally -- it has no notion
        that A is gone. Dropping it is server.queue_out()'s job (its by_sid
        lookup misses), one layer above the engine. This test deliberately
        checks the engine's OUTPUT, not the wire.
    """
    eng = engine.Engine()
    A, B, MD = 1, 2, 3
    submit(eng, A, b"LOGIN alice")
    submit(eng, B, b"LOGIN bob")
    submit(eng, MD, b"SUBSCRIBE JNST")

    out_a = submit(eng, A, b"BUY JNST 100 238")   # oid 0
    assert out_a == [(A, b"ORDER_ACCEPTED 0\n")]

    surviving = eng.on_disconnect(A)
    assert surviving == 1
    assert eng.orders[0].qty_left == 100
    assert eng.orders[0].cancelled is False

    out_b = submit(eng, B, b"SELL JNST 60 238")
    assert (A, b"BOUGHT JNST 60 238\n") in out_b
    assert (B, b"SOLD JNST 60 238\n") in out_b
    assert (MD, b"TRADE JNST 60 238\n") in out_b
    assert eng.orders[0].qty_left == 40
