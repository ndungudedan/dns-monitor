#!/usr/bin/env python3
import argparse
import logging
import json

from dns_monitor.storage import DB
from dns_monitor.runner import run_cycle, loop


def main():
    parser = argparse.ArgumentParser(description="Bitcoin DNS seed monitor")
    parser.add_argument("--db", default="monitor.db", help="SQLite database path")
    parser.add_argument(
        "--network", action="append", dest="networks",
        choices=["mainnet", "testnet3", "signet"],
        help="Network(s) to monitor (default: all)",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("once", help="Run one probe cycle and exit")

    watch = sub.add_parser("watch", help="Run continuously on an interval")
    watch.add_argument("--interval", type=int, default=600, help="Seconds between cycles (default: 600)")

    sub.add_parser("summary", help="Print latest summary from the database")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    db = DB(args.db)

    if args.command == "once" or args.command is None:
        summary = run_cycle(db, args.networks)
        print(json.dumps(summary, indent=2))

    elif args.command == "watch":
        loop(db, args.interval, args.networks)

    elif args.command == "summary":
        print(json.dumps(db.latest_summary(), indent=2))


if __name__ == "__main__":
    main()
