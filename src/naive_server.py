#!/usr/bin/env python3


from __future__ import annotations

import socket
import sys


def handle_client(conn: socket.socket, addr) -> None:

    #fully blocking the connection
    
    rbuf = bytearray()
    while True:
        data = conn.recv(4096)          # BLOCKING -- no timeout, no select
        if not data:                     # EOF: client closed
            break
        rbuf += data
        while True:
            nl = rbuf.find(b"\n")
            if nl < 0:
                break                    # partial line: loop back to the
                                          # blocking recv() above and WAIT
            line = bytes(rbuf[:nl])
            del rbuf[:nl + 1]
            tokens = line.split()
            if tokens[:1] == [b"LOGIN"]:
                conn.sendall(b"OK\n")
            else:
                conn.sendall(b"ERROR unknown_command\n")
    conn.close()


def main() -> None:
    host, port = sys.argv[1], int(sys.argv[2])
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((host, port))
    ls.listen(4096)
    print("Naive (blocking, single-threaded) server listening.", file=sys.stderr, flush=True)

    while True:
        conn, addr = ls.accept()         # BLOCKING -- next client waits
                                          # behind whatever this call to
                                          # handle_client() below is doing
        try:
            handle_client(conn, addr)
        except OSError:
            try:
                conn.close()
            except OSError:
                pass


if __name__ == "__main__":
    main()
