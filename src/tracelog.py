#!/usr/bin/env python3
"""
Dual-channel trace logger  (COL334 A2 roadmap §7)

Two channels, one call site:

  stderr   human-readable, one line per event.  THIS IS YOUR SCREENSHOT
           CHANNEL -- experiment.py does not pipe the server's stdio, so
           whatever you write here lands directly in the terminal you are
           photographing.  The launcher uses `python3 -u` so it appears
           immediately without per-event flushing.

  $EXCH_TRACE   structured JSONL, one object per event.  THIS IS YOUR
           PLOTTING CHANNEL (figures P1/P2/P3).  Block-buffered on purpose:
           flushing per event would make the instrumentation the bottleneck
           you are trying to measure.  main() must call close() -- including
           from the SIGTERM handler, because experiment.py kills the server
           with SIGTERM and Python's default action would truncate the file.

Usage:
    import tracelog
    tracelog.emit("recv", sid=3, fd=9, n=17, rbuf_after=17)
    tracelog.close()

Timestamps are milliseconds since process start, from time.monotonic_ns()
(never wall clock -- NTP steps would corrupt latency measurements).
"""

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
    except OSError as _e:                                  # never fatal
        sys.stderr.write("tracelog: cannot open %s: %s\n" % (_PATH, _e))
        _FH = None


def _ms() -> float:
    return (time.monotonic_ns() - _T0) / 1e6


def _jsonable(v):
    """bytes are not JSON-serialisable; latin-1 is a lossless byte<->str map."""
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).decode("latin-1")
    return v


def emit(ev: str, sid=None, fd=None, **kw) -> None:
    t = _ms()

    # ---- human-readable channel (screenshots) ----
    parts = ["%10.3fms" % t, "%-16s" % ev]
    if sid is not None:
        parts.append("sid=%s" % sid)
    if fd is not None:
        parts.append("fd=%s" % fd)
    for k, v in kw.items():
        parts.append("%s=%r" % (k, v) if isinstance(v, (bytes, bytearray, str))
                     else "%s=%s" % (k, v))
    sys.stderr.write(" ".join(parts) + "\n")

    # ---- structured channel (plots) ----
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
