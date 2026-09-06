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

import re

_U32_RE = re.compile(rb'[0-9]+')

def parse_u32(tok: bytes, lo: int, hi: int) -> int | None:
    if not _U32_RE.fullmatch(tok):
        return None
    val = int(tok)
    return val if lo <= val <= hi else None

def parse_line(line: bytes) -> tuple[bool, tuple | bytes]:
    tokens = line.split()
    if not tokens:
        return (False, REASON_UNKNOWN_COMMAND)
    
    verb = tokens[0]
    
    if verb == b"LOGIN":
        if len(tokens) != 2:
            return (False, REASON_BAD_ARITY)
        return (True, (b"LOGIN", tokens[1]))
        
    elif verb == b"BUY":
        if len(tokens) != 4:
            return (False, REASON_BAD_ARITY)
        if tokens[1] not in INSTRUMENT_CODE:
            return (False, REASON_BAD_INSTRUMENT)
        qty = parse_u32(tokens[2], QTY_MIN, QTY_MAX)
        if qty is None:
            return (False, REASON_BAD_QUANTITY)
        price = parse_u32(tokens[3], PRICE_MIN, PRICE_MAX)
        if price is None:
            return (False, REASON_BAD_PRICE)
        return (True, (b"BUY", INSTRUMENT_CODE[tokens[1]], qty, price))
        
    elif verb == b"SELL":
        if len(tokens) != 4:
            return (False, REASON_BAD_ARITY)
        if tokens[1] not in INSTRUMENT_CODE:
            return (False, REASON_BAD_INSTRUMENT)
        qty = parse_u32(tokens[2], QTY_MIN, QTY_MAX)
        if qty is None:
            return (False, REASON_BAD_QUANTITY)
        price = parse_u32(tokens[3], PRICE_MIN, PRICE_MAX)
        if price is None:
            return (False, REASON_BAD_PRICE)
        return (True, (b"SELL", INSTRUMENT_CODE[tokens[1]], qty, price))
        
    elif verb == b"CANCEL":
        if len(tokens) != 2:
            return (False, REASON_BAD_ARITY)
        order_id = parse_u32(tokens[1], OID_MIN, OID_MAX)
        if order_id is None:
            return (False, REASON_BAD_ORDER_ID)
        return (True, (b"CANCEL", order_id))
        
    elif verb == b"SUBSCRIBE":
        if len(tokens) != 2:
            return (False, REASON_BAD_ARITY)
        if tokens[1] not in INSTRUMENT_CODE:
            return (False, REASON_BAD_INSTRUMENT)
        return (True, (b"SUBSCRIBE", INSTRUMENT_CODE[tokens[1]]))
        
    elif verb == b"UNSUBSCRIBE":
        if len(tokens) != 2:
            return (False, REASON_BAD_ARITY)
        if tokens[1] not in INSTRUMENT_CODE:
            return (False, REASON_BAD_INSTRUMENT)
        return (True, (b"UNSUBSCRIBE", INSTRUMENT_CODE[tokens[1]]))
        
    elif verb == b"QUIT":
        if len(tokens) != 1:
            return (False, REASON_BAD_ARITY)
        return (True, (b"QUIT",))
        
    else:
        return (False, REASON_UNKNOWN_COMMAND)
