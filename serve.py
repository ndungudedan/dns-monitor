#!/usr/bin/env python3
"""
Web server entrypoint. Run alongside main.py watch in a separate process.
Both share the same SQLite DB (WAL mode handles concurrent access).

Usage:
  uv run python serve.py [--db monitor.db] [--host 0.0.0.0] [--port 8000]
"""
import argparse
import uvicorn

from dns_monitor.storage import DB
from dns_monitor.api import app, init_app


def main():
    parser = argparse.ArgumentParser(description="DNS monitor web server")
    parser.add_argument("--db", default="monitor.db")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    db = DB(args.db)
    init_app(db)

    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
