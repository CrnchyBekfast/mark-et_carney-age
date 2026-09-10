#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time

_T0 = time.monotonic_ns()
_PATH = os.environ.get("EXCH_TRACE")
_FH = None

if _PATH:
    try:
        _FH = open(_PATH, "w", buffering=1 << 16)
    except OSError as _e:
        sys.stderr.write("tracelog: cannot open %s: %s\n" % (_PATH, _e))
        _FH = None


def _ms() -> float:
    return (time.monotonic_ns() - _T0) / 1e6


def _jsonable(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).decode("latin-1")
    return v


def emit(ev: str, sid=None, fd=None, **kw) -> None:
    t = _ms()

    parts = ["%10.3fms" % t, "%-16s" % ev]
    if sid is not None:
        parts.append("sid=%s" % sid)
    if fd is not None:
        parts.append("fd=%s" % fd)
    for k, v in kw.items():
        parts.append("%s=%r" % (k, v) if isinstance(v, (bytes, bytearray, str))
                     else "%s=%s" % (k, v))
    sys.stderr.write(" ".join(parts) + "\n")

    if _FH is not None:
        rec = {"t_ms": round(t, 4), "ev": ev}
        if sid is not None:
            rec["sid"] = sid
        if fd is not None:
            rec["fd"] = fd
        for k, v in kw.items():
            rec[k] = _jsonable(v)
        try:
            _FH.write(json.dumps(rec) + "\n")
        except (OSError, ValueError):
            pass


def close() -> None:
    global _FH
    if _FH is not None:
        try:
            _FH.flush()
            _FH.close()
        except OSError:
            pass
        _FH = None
    try:
        sys.stderr.flush()
    except OSError:
        pass
