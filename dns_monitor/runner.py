import logging
import time

from .seeds import SEEDS, DEFAULT_PORT
from .resolver import resolve_all
from .prober import probe_seed_results
from .storage import DB
from .geo import enrich_batch

logger = logging.getLogger(__name__)


def run_cycle(db: DB, networks: list[str] | None = None):
    seeds = {k: v for k, v in SEEDS.items() if networks is None or k in networks}

    logger.info("=== Resolving %d seeds across %s ===", sum(len(v) for v in seeds.values()), list(seeds))
    seed_results = resolve_all(seeds)
    db.save_seed_results(seed_results)

    ok = sum(1 for r in seed_results if r.ok)
    logger.info("DNS: %d/%d seeds responded", ok, len(seed_results))

    logger.info("=== Probing nodes ===")
    node_results = probe_seed_results(seed_results, DEFAULT_PORT)
    db.save_node_results(node_results)

    # GeoIP — only enrich IPs we haven't seen before
    all_ips = list({r.ip for r in node_results})
    geo_map = enrich_batch(all_ips)
    if geo_map:
        db.save_geo_results(geo_map)
        logger.info("GeoIP: enriched %d/%d IPs", len(geo_map), len(all_ips))

    summary = db.latest_summary()
    reachable = summary["reachable"]
    total = summary["total_nodes"]
    pct = (reachable / total * 100) if total else 0
    logger.info("Nodes: %d/%d reachable (%.1f%%)", reachable, total, pct)

    return summary


def loop(db: DB, interval_seconds: int = 600, networks: list[str] | None = None):
    logger.info("Starting monitor loop (interval=%ds)", interval_seconds)
    while True:
        try:
            run_cycle(db, networks)
        except Exception as e:
            logger.error("Cycle error: %s", e)
        time.sleep(interval_seconds)
