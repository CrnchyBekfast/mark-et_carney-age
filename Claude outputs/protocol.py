"""
Protocol constants -- COL334 A2, "The Socket Exchange"

Scaffolding only: the fixed ERROR <reason> vocabulary and instrument names.
The actual line parser (LOGIN/BUY/SELL/CANCEL/SUBSCRIBE/UNSUBSCRIBE/QUIT
validation, parse_u32, etc. -- roadmap §3) is Phase 1 work and belongs here,
written by hand rather than scaffolded, since it's exactly what the viva will
probe.

<reason> is free-form per §2.1/§2.2 of the handout -- the spec does not fix a
vocabulary. Defining one FIXED set and using it everywhere (never an ad hoc
string at the call site) is what makes ERROR replies greppable in test output
and in the report, and it is what README §9 documents to the grader.
"""

# ---------------------------------------------------------------------------
# ERROR <reason> vocabulary  (roadmap §3)
# ---------------------------------------------------------------------------
REASON_UNKNOWN_COMMAND      = b"unknown_command"        # verb not one of the 7
REASON_BAD_ARITY            = b"bad_arity"              # wrong token count
REASON_BAD_INSTRUMENT       = b"bad_instrument"         # not JNST or IMCT
REASON_BAD_QUANTITY         = b"bad_quantity"           # fails parse_u32(1, 2**31-1)
REASON_BAD_PRICE            = b"bad_price"              # fails parse_u32(1, 2**31-1)
REASON_BAD_ORDER_ID         = b"bad_order_id"           # fails parse_u32(0, 2**31-1)
REASON_NOT_LOGGED_IN        = b"not_logged_in"          # BUY/SELL/CANCEL before LOGIN
REASON_DUPLICATE_USERNAME   = b"duplicate_username"     # LOGIN name already connected
REASON_WRONG_ROLE           = b"wrong_role"             # e.g. SUBSCRIBE from a Trader
REASON_UNKNOWN_ORDER        = b"unknown_order"          # CANCEL of an id that never existed
REASON_ORDER_NOT_CANCELLABLE = b"order_not_cancellable" # already fully filled/cancelled
REASON_NOT_OWNER            = b"not_owner"              # CANCEL of someone else's order
REASON_LINE_TOO_LONG        = b"line_too_long"          # rbuf exceeded MAX_LINE, no '\n' seen

# ---------------------------------------------------------------------------
# Instruments  (§2.1: exactly two, nothing else is valid)
# ---------------------------------------------------------------------------
JNST, IMCT = 0, 1
INSTRUMENT_NAME = {JNST: b"JNST", IMCT: b"IMCT"}
INSTRUMENT_CODE = {v: k for k, v in INSTRUMENT_NAME.items()}   # b"JNST" -> JNST, etc.

# ---------------------------------------------------------------------------
# Order sides
# ---------------------------------------------------------------------------
BUY, SELL = 0, 1

# ---------------------------------------------------------------------------
# Value ranges  (§2.1, verbatim)
# ---------------------------------------------------------------------------
QTY_MIN, QTY_MAX     = 1, 2_147_483_647
PRICE_MIN, PRICE_MAX = 1, 2_147_483_647
OID_MIN, OID_MAX     = 0, 2_147_483_647

# ---------------------------------------------------------------------------
# parse_u32  (PHASE_1_2_SPEC.md amendment 1: re.fullmatch, not anchored re.match --
# Python's unanchored-by-MULTILINE '$' matches before a trailing '\n' too,
# e.g. re.match(rb'^[0-9]+$', b'123\n') incorrectly succeeds. fullmatch has
# no such trap and needs no anchors.)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# parse_line  (roadmap §3)
#
# 100% STATELESS: no side effects, no knowledge of role, login status,
# usernames, or the order book. Can NEVER return not_logged_in /
# duplicate_username / wrong_role / unknown_order / order_not_cancellable /
# not_owner -- those six are engine.py's exclusive territory
# (PHASE_1_2_SPEC.md amendment 5).
# ---------------------------------------------------------------------------

_ARITY = {
    b"LOGIN": 2,
    b"BUY": 4,
    b"SELL": 4,
    b"CANCEL": 2,
    b"SUBSCRIBE": 2,
    b"UNSUBSCRIBE": 2,
    b"QUIT": 1,
}


def parse_line(line: bytes):
    """
    Returns exactly one of:
        (True,  (VERB, *validated_args))
        (False, REASON_BYTES)   -- one of the 6 SYNTAX reasons only

    Validation precedence, checked in this order, short-circuit on first
    failure:
        1. tokens = line.split()
        2. if not tokens: (False, REASON_UNKNOWN_COMMAND)
        3. verb = tokens[0]; exact-uppercase match against the 7 known
           verbs, else (False, REASON_UNKNOWN_COMMAND)
        4. if len(tokens) != expected_arity[verb]: (False, REASON_BAD_ARITY)
        5. field validation, LEFT TO RIGHT as tokens appear on the wire

    `instr` in the success payload is always the mapped int code
    (JNST=0/IMCT=1), never raw bytes -- engine.py never sees b"JNST".
    """
    tokens = line.split()
    if not tokens:
        return False, REASON_UNKNOWN_COMMAND

    verb = tokens[0]
    if verb not in _ARITY:
        return False, REASON_UNKNOWN_COMMAND

    if len(tokens) != _ARITY[verb]:
        return False, REASON_BAD_ARITY

    if verb == b"LOGIN":
        return True, (verb, tokens[1])

    if verb in (b"BUY", b"SELL"):
        instr_tok, qty_tok, price_tok = tokens[1], tokens[2], tokens[3]
        if instr_tok not in INSTRUMENT_CODE:
            return False, REASON_BAD_INSTRUMENT
        qty = parse_u32(qty_tok, QTY_MIN, QTY_MAX)
        if qty is None:
            return False, REASON_BAD_QUANTITY
        price = parse_u32(price_tok, PRICE_MIN, PRICE_MAX)
        if price is None:
            return False, REASON_BAD_PRICE
        return True, (verb, INSTRUMENT_CODE[instr_tok], qty, price)

    if verb == b"CANCEL":
        oid = parse_u32(tokens[1], OID_MIN, OID_MAX)
        if oid is None:
            return False, REASON_BAD_ORDER_ID
        return True, (verb, oid)

    if verb in (b"SUBSCRIBE", b"UNSUBSCRIBE"):
        instr_tok = tokens[1]
        if instr_tok not in INSTRUMENT_CODE:
            return False, REASON_BAD_INSTRUMENT
        return True, (verb, INSTRUMENT_CODE[instr_tok])

    if verb == b"QUIT":
        return True, (verb,)

    raise AssertionError("unreachable: verb already validated against _ARITY")
