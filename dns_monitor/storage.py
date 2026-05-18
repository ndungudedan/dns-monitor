import sqlite3
import time
import logging
from contextlib import contextmanager
from pathlib import Path

from .resolver import SeedResult
from .prober import NodeResult

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS seed_polls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    seed        TEXT    NOT NULL,
    network     TEXT    NOT NULL,
    queried_at  REAL    NOT NULL,
    response_ms REAL,
    record_count INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS node_probes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ip               TEXT    NOT NULL,
    port             INTEGER NOT NULL,
    seed             TEXT    NOT NULL,
    network          TEXT    NOT NULL,
    probed_at        REAL    NOT NULL,
    reachable        INTEGER NOT NULL,
    connect_ms       REAL,
    user_agent       TEXT,
    services         INTEGER,
    protocol_version INTEGER,
    start_height     INTEGER,
    error            TEXT
);

CREATE INDEX IF NOT EXISTS idx_seed_polls_seed      ON seed_polls(seed);
CREATE INDEX IF NOT EXISTS idx_seed_polls_queried   ON seed_polls(queried_at);
CREATE INDEX IF NOT EXISTS idx_node_probes_ip       ON node_probes(ip);
CREATE INDEX IF NOT EXISTS idx_node_probes_probed   ON node_probes(probed_at);
"""


class DB:
    def __init__(self, path: str | Path = "monitor.db"):
        self.path = str(path)
        self._init()

    def _init(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)
        logger.info("Database ready at %s", self.path)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save_seed_results(self, results: list[SeedResult]):
        rows = [
            (r.seed, r.network, r.queried_at, r.response_time_ms, len(r.records), r.error)
            for r in results
        ]
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO seed_polls (seed, network, queried_at, response_ms, record_count, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        logger.debug("Saved %d seed poll records", len(rows))

    def save_node_results(self, results: list[NodeResult]):
        rows = [
            (
                r.ip, r.port, r.seed, r.network, r.probed_at,
                int(r.reachable), r.connect_ms,
                r.user_agent, r.services, r.protocol_version, r.start_height,
                r.error,
            )
            for r in results
        ]
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO node_probes "
                "(ip, port, seed, network, probed_at, reachable, connect_ms, "
                " user_agent, services, protocol_version, start_height, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        logger.debug("Saved %d node probe records", len(rows))

    def latest_summary(self) -> dict:
        since = time.time() - 3600
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*), SUM(reachable), SUM(1 - reachable) "
                "FROM node_probes WHERE probed_at > ?",
                (since,),
            ).fetchone()
            total, reachable, unreachable = row

            seeds = conn.execute(
                "SELECT seed, record_count, error FROM seed_polls "
                "WHERE queried_at > ? ORDER BY queried_at DESC",
                (since,),
            ).fetchall()

            user_agents = conn.execute(
                "SELECT user_agent, COUNT(*) as cnt FROM node_probes "
                "WHERE probed_at > ? AND reachable = 1 AND user_agent IS NOT NULL "
                "GROUP BY user_agent ORDER BY cnt DESC LIMIT 20",
                (since,),
            ).fetchall()

        return {
            "total_nodes": total or 0,
            "reachable": reachable or 0,
            "unreachable": unreachable or 0,
            "seeds": [{"seed": s, "records": r, "error": e} for s, r, e in seeds],
            "user_agents": [{"user_agent": ua, "count": n} for ua, n in user_agents],
        }

    def reachability_history(self, hours: int = 24) -> list[dict]:
        """Reachability % bucketed into ~10-minute intervals for charting."""
        since = time.time() - hours * 3600
        bucket = 600  # 10 minutes
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    CAST(probed_at / ? AS INTEGER) * ? AS bucket,
                    COUNT(*) AS total,
                    SUM(reachable) AS reachable
                FROM node_probes
                WHERE probed_at > ?
                GROUP BY bucket
                ORDER BY bucket
                """,
                (bucket, bucket, since),
            ).fetchall()
        return [
            {
                "timestamp": int(b),
                "total": t,
                "reachable": int(r or 0),
                "pct": round(r / t * 100, 1) if t else 0,
            }
            for b, t, r in rows
        ]

    def seed_stats(self, hours: int = 24) -> list[dict]:
        """Per-seed: last poll time, record count, reachable %, avg response ms."""
        since = time.time() - hours * 3600
        with self._conn() as conn:
            polls = conn.execute(
                """
                SELECT seed, network,
                    MAX(queried_at) AS last_poll,
                    AVG(response_ms) AS avg_ms,
                    SUM(CASE WHEN error IS NULL THEN 0 ELSE 1 END) AS errors,
                    COUNT(*) AS polls,
                    MAX(record_count) AS max_records
                FROM seed_polls
                WHERE queried_at > ?
                GROUP BY seed, network
                ORDER BY network, seed
                """,
                (since,),
            ).fetchall()

            reachability = conn.execute(
                """
                SELECT seed,
                    COUNT(*) AS total,
                    SUM(reachable) AS reachable
                FROM node_probes
                WHERE probed_at > ?
                GROUP BY seed
                """,
                (since,),
            ).fetchall()

        reach_map = {seed: (t, int(r or 0)) for seed, t, r in reachability}

        return [
            {
                "seed": seed,
                "network": network,
                "last_poll": int(last_poll) if last_poll else None,
                "avg_response_ms": round(avg_ms, 1) if avg_ms else None,
                "error_polls": errors,
                "total_polls": polls,
                "max_records": max_rec,
                "nodes_total": reach_map.get(seed, (0, 0))[0],
                "nodes_reachable": reach_map.get(seed, (0, 0))[1],
                "reachable_pct": round(
                    reach_map[seed][1] / reach_map[seed][0] * 100, 1
                ) if seed in reach_map and reach_map[seed][0] else None,
            }
            for seed, network, last_poll, avg_ms, errors, polls, max_rec in polls
        ]

    def user_agent_history(self, hours: int = 24) -> list[dict]:
        """UA counts over the window, top 15."""
        since = time.time() - hours * 3600
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT user_agent, COUNT(*) AS cnt
                FROM node_probes
                WHERE probed_at > ? AND reachable = 1 AND user_agent IS NOT NULL
                GROUP BY user_agent
                ORDER BY cnt DESC
                LIMIT 15
                """,
                (since,),
            ).fetchall()
        return [{"user_agent": ua, "count": n} for ua, n in rows]
