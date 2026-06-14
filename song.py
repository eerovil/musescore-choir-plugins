#!/usr/bin/env python3
"""Launch the `song` web app — one state-aware door over the choir-track toolkit.

    ./song.py            # start the server and open the browser
    ./song.py --port 8123 --no-browser

See DESIGN.md for the design.
"""

import argparse
import os
import socket
import sys
import threading
import webbrowser

# Run from repo root so `from src...` imports resolve.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _find_free_port(host: str, preferred: int) -> int:
    """Use `preferred` if free; otherwise scan upward, then fall back to any OS port."""
    for port in range(preferred, preferred + 50):
        if _port_is_free(host, port):
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch the song web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000,
                        help="Preferred port; an open one nearby is used if it's taken")
    parser.add_argument("--no-browser", action="store_true", help="Don't open a browser")
    args = parser.parse_args()

    port = _find_free_port(args.host, args.port)
    if port != args.port:
        print(f"Port {args.port} busy — using {port}")
    args.port = port
    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    import uvicorn
    print(f"song → {url}")
    uvicorn.run("src.song_app.server:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
