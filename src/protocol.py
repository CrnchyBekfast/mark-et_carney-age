import re

REASON_UNKNOWN_COMMAND = b"unknown_command"
REASON_BAD_ARITY = b"bad_arity"
REASON_BAD_INSTRUMENT = b"bad_instrument"
REASON_BAD_QUANTITY = b"bad_quantity"
REASON_BAD_PRICE = b"bad_price"
REASON_BAD_ORDER_ID = b"bad_order_id"
REASON_NOT_LOGGED_IN = b"not_logged_in"
REASON_DUPLICATE_USERNAME = b"duplicate_username"
REASON_WRONG_ROLE = b"wrong_role"
REASON_UNKNOWN_ORDER = b"unknown_order"
REASON_ORDER_NOT_CANCELLABLE = b"order_not_cancellable"
REASON_NOT_OWNER = b"not_owner"
REASON_LINE_TOO_LONG = b"line_too_long"

JNST, IMCT = 0, 1
INSTRUMENT_NAME = {JNST: b"JNST", IMCT: b"IMCT"}
INSTRUMENT_CODE = {v: k for k, v in INSTRUMENT_NAME.items()}

BUY, SELL = 0, 1

QTY_MIN, QTY_MAX = 1, 2_147_483_647
PRICE_MIN, PRICE_MAX = 1, 2_147_483_647
OID_MIN, OID_MAX = 0, 2_147_483_647

_U32_RE = re.compile(rb'^[0-9]+$')

def parse_u32(tok: bytes, lo: int, hi: int) -> int | None:
    if not _U32_RE.match(tok):
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

    elif verb in (b"BUY", b"SELL"):
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
        return (True, (verb, INSTRUMENT_CODE[tokens[1]], qty, price))

    elif verb == b"CANCEL":
        if len(tokens) != 2:
            return (False, REASON_BAD_ARITY)
        order_id = parse_u32(tokens[1], OID_MIN, OID_MAX)
        if order_id is None:
            return (False, REASON_BAD_ORDER_ID)
        return (True, (b"CANCEL", order_id))

    elif verb in (b"SUBSCRIBE", b"UNSUBSCRIBE"):
        if len(tokens) != 2:
            return (False, REASON_BAD_ARITY)
        if tokens[1] not in INSTRUMENT_CODE:
            return (False, REASON_BAD_INSTRUMENT)
        return (True, (verb, INSTRUMENT_CODE[tokens[1]]))

    elif verb == b"QUIT":
        if len(tokens) != 1:
            return (False, REASON_BAD_ARITY)
        return (True, (b"QUIT",))

    return (False, REASON_UNKNOWN_COMMAND)
