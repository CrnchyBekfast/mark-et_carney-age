#!/bin/sh
# Capture every OS parameter this assignment's analysis depends on.
# This is report artifact B1 (roadmap §9) -- run it INSIDE the FreeBSD VM
# before you measure anything, and again if you change any sysctl.
#
#   sh src/tools/baseline.sh > baseline.txt 2>&1
#
# Why it matters: the Experiment 7 conclusion depends on socket buffer sizes,
# and the bonus depends on fd/thread/port ceilings.  A report that states these
# numbers is quantitative; one that doesn't is anecdote.

echo "=============================================================="
echo " BASELINE  $(date)"
echo "=============================================================="
echo
echo "---- system --------------------------------------------------"
uname -a
echo
echo "python3: $(python3 --version 2>&1)  ($(command -v python3 2>/dev/null || echo NOT-FOUND))"
echo "shell openfiles limit: $(ulimit -n)"
echo

echo "---- TCP socket buffers (drives Experiment 7) ----------------"
for k in net.inet.tcp.sendspace net.inet.tcp.recvspace \
         net.inet.tcp.sendbuf_auto net.inet.tcp.recvbuf_auto \
         net.inet.tcp.sendbuf_inc net.inet.tcp.recvbuf_inc \
         net.inet.tcp.sendbuf_max net.inet.tcp.recvbuf_max \
         net.inet.tcp.msl net.inet.tcp.delayed_ack ; do
    printf '%-34s = %s\n' "$k" "$(sysctl -n $k 2>/dev/null || echo '(n/a)')"
done
echo

echo "---- file descriptors / sockets (drives the bonus) -----------"
for k in kern.maxfiles kern.maxfilesperproc kern.openfiles \
         kern.ipc.somaxconn kern.ipc.soacceptqueue \
         kern.ipc.maxsockbuf kern.ipc.nmbclusters \
         kern.threads.max_threads_per_proc kern.maxproc ; do
    printf '%-34s = %s\n' "$k" "$(sysctl -n $k 2>/dev/null || echo '(n/a)')"
done
echo

echo "---- ephemeral ports (the 70k wall, roadmap §10) -------------"
for k in net.inet.ip.portrange.first net.inet.ip.portrange.last \
         net.inet.ip.portrange.randomized ; do
    printf '%-34s = %s\n' "$k" "$(sysctl -n $k 2>/dev/null || echo '(n/a)')"
done
PFIRST=$(sysctl -n net.inet.ip.portrange.first 2>/dev/null || echo 0)
PLAST=$(sysctl -n net.inet.ip.portrange.last  2>/dev/null || echo 0)
if [ "$PLAST" -gt "$PFIRST" ] 2>/dev/null; then
    echo "  => ~$((PLAST - PFIRST)) ephemeral ports per source IP."
    echo "     70,000 connections from ONE source IP is impossible;"
    echo "     add lo0 aliases and bind() the generator to them."
fi
echo

echo "---- loopback interface --------------------------------------"
ifconfig lo0 2>/dev/null || ifconfig lo 2>/dev/null || echo "(no lo0)"
echo "NOTE: FreeBSD lo0 MTU is 16384, so loopback segments can be ~16 KB."
echo "      Do not expect 1460-byte segments when reading tcpdump."
echo

echo "---- mbuf usage ---------------------------------------------"
netstat -m 2>/dev/null | head -20 || echo "(netstat -m unavailable)"
echo

echo "=============================================================="
echo " EXPERIMENT 7 PRE-FLIGHT ARITHMETIC  (roadmap §5)"
echo "=============================================================="
SND=$(sysctl -n net.inet.tcp.sendspace 2>/dev/null || echo 0)
RCV=$(sysctl -n net.inet.tcp.recvspace 2>/dev/null || echo 0)
MSG=17                      # len("TRADE JNST 1 238\n")
TRADES=5000                 # experiment.py TRADE_COUNT
FEED=$((TRADES * MSG))
CAP=$((SND + RCV))
echo "  message size     = ${MSG} B   (\"TRADE JNST 1 238\\n\")"
echo "  trades generated = ${TRADES}  (experiment.py TRADE_COUNT)"
echo "  feed volume      = ${FEED} B to each subscribed MD client"
echo "  buffer capacity  = sendspace ${SND} + recvspace ${RCV} = ${CAP} B"
echo
if [ "$CAP" -ge "$FEED" ] 2>/dev/null; then
    echo "  VERDICT: capacity >= feed."
    echo "    Backpressure will probably NOT appear at these settings: the"
    echo "    whole feed fits in the socket buffers and send() may never"
    echo "    raise BlockingIOError.  Report this as a finding, then do run B:"
    echo
    echo "      sysctl net.inet.tcp.recvbuf_auto=0 net.inet.tcp.sendbuf_auto=0"
    echo "      sysctl net.inet.tcp.recvspace=8192 net.inet.tcp.sendspace=4096"
    echo
else
    echo "  VERDICT: feed > capacity."
    echo "    Expect the first EAGAIN roughly $((CAP / MSG)) trades in"
    echo "    (~$((CAP / MSG / 32)) s at the harness's ~32 trades/s)."
fi
echo
echo "Reminder: experiment.py 7 takes ~2.5-3 minutes (drain_socket timeouts"
echo "dominate: ~0.031 s/trade). It is not hung. Do not Ctrl-C it."
