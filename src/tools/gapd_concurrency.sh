#!/bin/sh
# Gap D -- Handout Sec.4.3: "at least 10 simultaneously connected clients,
# including at least 2 Trader Clients and at least 4 Market-Data Clients."
# NOT part of the submission (same status as the other src/tools/* helpers).
#
# Launches 4 Trader Clients and 6 Market-Data Clients (10 total, comfortably
# over both minimums) against a server that must already be running, keeps
# them all connected indefinitely, and prints the commands to use for the
# screenshot before exiting on Ctrl-C.
#
# Each client's stdin is fed from `tail -f /dev/null`: an always-open pipe
# that never produces a line and never closes, so trader.py/market_data.py
# block forever on kqueue's stdin registration instead of hitting EOF and
# exiting. This keeps every connection open with no manual typing.
#
# Usage:  sh src/tools/gapd_concurrency.sh [host] [port]

HOST=${1:-127.0.0.1}
PORT=${2:-5000}
LOGDIR=$(mktemp -d)
PIDS=""

cleanup() {
    echo ""
    echo "shutting down all 10 clients..."
    for p in $PIDS; do kill "$p" 2>/dev/null; done
    wait 2>/dev/null
    echo "logs were in $LOGDIR"
    exit 0
}
trap cleanup INT TERM

echo "starting 4 Trader Clients..."
for i in 1 2 3 4; do
    tail -f /dev/null | python3 -u src/trader.py "$HOST" "$PORT" "gapd_trader$i" \
        > "$LOGDIR/trader$i.log" 2>&1 &
    PIDS="$PIDS $!"
done

echo "starting 6 Market-Data Clients..."
for i in 1 2 3 4 5 6; do
    INSTR=JNST
    if [ $((i % 2)) -eq 0 ]; then INSTR=IMCT; fi
    tail -f /dev/null | python3 -u src/market_data.py "$HOST" "$PORT" "$INSTR" \
        > "$LOGDIR/md$i.log" 2>&1 &
    PIDS="$PIDS $!"
done

sleep 1
echo ""
echo "10 clients launched (4 traders + 6 market-data), all connected to $HOST:$PORT."
echo "logs: $LOGDIR"
echo ""
echo "Now, in another terminal, capture the Gap-D screenshot with ONE of:"
echo "    sockstat -4 | grep :$PORT"
echo "    netstat -an | grep $PORT | grep ESTABLISHED | wc -l"
echo "  (expect 10 ESTABLISHED lines on the server side, or 10 sockstat rows)"
echo ""
echo "Press Ctrl-C here when the screenshot is captured to close all 10 clients cleanly."
wait
