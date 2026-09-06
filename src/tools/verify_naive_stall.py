#!/usr/bin/env python3
"""
Local sanity check for src/naive_server.py, BEFORE it ever touches the VM.

Reproduces Experiment 4's exact scenario: Client 1 sends 20 bytes with no
trailing '\n' and goes silent; ~1s later Client 2 connects and sends a
complete LOGIN line. Against the naive server, Client 2's connect() should
hang (never even accepted, since accept() only runs after Client 1's
handle_client() returns -- which never happens while Client 1 stays open).

Not part of the submission -- throwaway, like verify_writepath.py.
"""
import socket
import subprocess
import sys
import time

HOST, PORT = "127.0.0.1", 18765


def main():
    proc = subprocess.Popen(
        [sys.executable, "-u", "naive_server.py", HOST, str(PORT)],
        cwd="src",
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.3)
    try:
        c1 = socket.create_connection((HOST, PORT), timeout=2)
        c1.sendall(b"LOGIN blocked_client")   # no trailing \n -- deliberate stall
        print("Client 1 sent 20 bytes, no newline. Now silent.")

        time.sleep(1.0)

        t0 = time.monotonic()
        try:
            c2 = socket.create_connection((HOST, PORT), timeout=2)
            c2.sendall(b"LOGIN active_client\n")
            c2.settimeout(2)
            reply = c2.recv(4096)
            elapsed = time.monotonic() - t0
            print(f"UNEXPECTED: Client 2 got a reply ({reply!r}) in {elapsed:.3f}s "
                  f"-- naive server did NOT stall as predicted.")
            sys.exit(1)
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            elapsed = time.monotonic() - t0
            print(f"CONFIRMED: Client 2 got no response within {elapsed:.3f}s "
                  f"({type(e).__name__}) -- naive server is stalled behind "
                  f"Client 1, exactly as predicted.")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        out = proc.stderr.read() if proc.stderr else ""
        if out:
            print("--- naive_server.py stderr ---")
            print(out)


if __name__ == "__main__":
    main()
