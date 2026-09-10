#!/usr/bin/env python3
from __future__ import annotations

import socket
import sys


def handle_client(conn: socket.socket, addr) -> None:
    rbuf = bytearray()
    while True:
        data = conn.recv(4096)
        if not data:
            break
        rbuf += data
        while True:
            nl = rbuf.find(b"\n")
            if nl < 0:
                break
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
    print("Naive blocking server listening.", file=sys.stderr, flush=True)

    while True:
        conn, addr = ls.accept()
        try:
            handle_client(conn, addr)
        except OSError:
            try:
                conn.close()
            except OSError:
                pass


if __name__ == "__main__":
    main()
