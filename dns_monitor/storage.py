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
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    seed         TEXT    NOT NULL,
    network      TEXT    NOT NULL,
    queried_at   REAL    NOT NULL,
    response_ms  REAL,
    record_count INTEGER NOT NULL DEFAULT 0,
    error        TEXT
);

-- Individual IPs returned by each DNS poll
CREATE TABLE IF NOT EXISTS seed_poll_records (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id INTEGER NOT NULL REFERENCES seed_polls(id) ON DELETE CASCADE,
    ip      TEXT    NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_spr_poll_id          ON seed_poll_records(poll_id);
CREATE INDEX IF NOT EXISTS idx_spr_ip               ON seed_poll_records(ip);
CREATE INDEX IF NOT EXISTS idx_node_probes_ip       ON node_probes(ip);
CREATE INDEX IF NOT EXISTS idx_node_probes_seed     ON node_probes(seed);
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
        with self._conn() as conn:
            for r in results:
                cursor = conn.execute(
                    "INSERT INTO seed_polls (seed, network, queried_at, response_ms, record_count, error) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (r.seed, r.network, r.queried_at, r.response_time_ms, len(r.records), r.error),
                )
                if r.records:
                    conn.executemany(
                        "INSERT INTO seed_poll_records (poll_id, ip) VALUES (?, ?)",
                        [(cursor.lastrowid, ip) for ip in r.records],
                    )
        logger.debug("Saved %d seed poll records", len(results))

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

    def seed_stats(self, known_seeds: dict[str, list[str]], hours: int = 24) -> list[dict]:
        """
        Per-seed stats for every known seed, merged with DB data.
        Seeds with no DB data yet appear with null metrics so the UI always
        shows the full configured list.
        """
        since = time.time() - hours * 3600
        with self._conn() as conn:
            polls = conn.execute(
                """
                SELECT seed, network,
                    MAX(queried_at) AS last_poll,
                    AVG(response_ms) AS avg_ms,
                    SUM(CASE WHEN error IS NULL THEN 1 ELSE 0 END) AS ok_polls,
                    COUNT(*) AS total_polls,
                    MAX(record_count) AS max_records
                FROM seed_polls
                WHERE queried_at > ?
                GROUP BY seed, network
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

        poll_map = {
            seed: (last_poll, avg_ms, ok_polls, total_polls, max_rec, network)
            for seed, network, last_poll, avg_ms, ok_polls, total_polls, max_rec in polls
        }
        reach_map = {seed: (t, int(r or 0)) for seed, t, r in reachability}

        result = []
        for network, seeds in known_seeds.items():
            for seed in seeds:
                p = poll_map.get(seed)
                r = reach_map.get(seed, (0, 0))
                last_poll, avg_ms, ok_polls, total_polls, max_rec, _ = p if p else (None, None, 0, 0, None, network)
                reachable_pct = round(r[1] / r[0] * 100, 1) if r[0] else None

                # Health score: weighted combo of DNS success rate and node reachability
                dns_ok_rate = (ok_polls / total_polls) if total_polls else None
                health = None
                if dns_ok_rate is not None and reachable_pct is not None:
                    health = round((dns_ok_rate * 0.4 + (reachable_pct / 100) * 0.6) * 100, 1)
                elif dns_ok_rate is not None:
                    health = round(dns_ok_rate * 100, 1)

                result.append({
                    "seed": seed,
                    "network": network,
                    "last_poll": int(last_poll) if last_poll else None,
                    "avg_response_ms": round(avg_ms, 1) if avg_ms else None,
                    "ok_polls": ok_polls,
                    "total_polls": total_polls,
                    "max_records": max_rec,
                    "nodes_total": r[0],
                    "nodes_reachable": r[1],
                    "reachable_pct": reachable_pct,
                    "health_score": health,
                    "no_data": total_polls == 0,
                })

        result.sort(key=lambda x: (x["network"], x["seed"]))
        return result

    def seed_history(self, seed: str, hours: int = 168) -> list[dict]:
        """Time-series reachability for a single seed (default 7 days)."""
        since = time.time() - hours * 3600
        bucket = 600  # 10-minute buckets
        with self._conn() as conn:
            dns_rows = conn.execute(
                """
                SELECT
                    CAST(queried_at / ? AS INTEGER) * ? AS bucket,
                    AVG(response_ms) AS avg_ms,
                    MAX(record_count) AS records,
                    SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors,
                    COUNT(*) AS polls
                FROM seed_polls
                WHERE seed = ? AND queried_at > ?
                GROUP BY bucket ORDER BY bucket
                """,
                (bucket, bucket, seed, since),
            ).fetchall()

            probe_rows = conn.execute(
                """
                SELECT
                    CAST(probed_at / ? AS INTEGER) * ? AS bucket,
                    COUNT(*) AS total,
                    SUM(reachable) AS reachable
                FROM node_probes
                WHERE seed = ? AND probed_at > ?
                GROUP BY bucket ORDER BY bucket
                """,
                (bucket, bucket, seed, since),
            ).fetchall()

        probe_map = {b: (t, int(r or 0)) for b, t, r in probe_rows}

        return [
            {
                "timestamp": int(b),
                "avg_dns_ms": round(avg_ms, 1) if avg_ms else None,
                "records": records,
                "dns_errors": errors,
                "dns_polls": polls,
                "nodes_total": probe_map.get(b, (0, 0))[0],
                "nodes_reachable": probe_map.get(b, (0, 0))[1],
                "reachable_pct": round(
                    probe_map[b][1] / probe_map[b][0] * 100, 1
                ) if b in probe_map and probe_map[b][0] else None,
            }
            for b, avg_ms, records, errors, polls in dns_rows
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

    def seed_nodes(self, seed: str, hours: int = 168) -> list[dict]:
        """
        Every unique IP ever seen from this seed in the window, with reachability stats
        and the most recent user agent. Sorted by times seen desc.
        """
        since = time.time() - hours * 3600
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    ip,
                    COUNT(*)                                        AS times_probed,
                    SUM(reachable)                                  AS times_reachable,
                    MAX(probed_at)                                  AS last_probed,
                    MAX(CASE WHEN reachable=1 THEN probed_at END)   AS last_reachable,
                    MAX(CASE WHEN reachable=1 THEN user_agent END)  AS user_agent,
                    MAX(CASE WHEN reachable=1 THEN protocol_version END) AS protocol_version,
                    MAX(CASE WHEN reachable=1 THEN start_height END)     AS start_height,
                    MAX(CASE WHEN reachable=1 THEN services END)         AS services,
                    AVG(CASE WHEN reachable=1 THEN connect_ms END)       AS avg_connect_ms
                FROM node_probes
                WHERE seed = ? AND probed_at > ?
                GROUP BY ip
                ORDER BY times_probed DESC, times_reachable DESC
                """,
                (seed, since),
            ).fetchall()

        return [
            {
                "ip": ip,
                "times_probed": tp,
                "times_reachable": int(tr or 0),
                "reachable_pct": round(tr / tp * 100, 1) if tp else 0,
                "last_probed": int(lp) if lp else None,
                "last_reachable": int(lr) if lr else None,
                "user_agent": ua,
                "protocol_version": pv,
                "start_height": sh,
                "services": svc,
                "avg_connect_ms": round(acm, 1) if acm else None,
            }
            for ip, tp, tr, lp, lr, ua, pv, sh, svc, acm in rows
        ]

    def seed_poll_log(self, seed: str, limit: int = 50) -> list[dict]:
        """Recent DNS poll log for a seed — each poll with its returned IPs."""
        with self._conn() as conn:
            polls = conn.execute(
                """
                SELECT id, queried_at, response_ms, record_count, error
                FROM seed_polls
                WHERE seed = ?
                ORDER BY queried_at DESC
                LIMIT ?
                """,
                (seed, limit),
            ).fetchall()

            if not polls:
                return []

            poll_ids = [p[0] for p in polls]
            placeholders = ",".join("?" * len(poll_ids))
            ips_rows = conn.execute(
                f"SELECT poll_id, ip FROM seed_poll_records WHERE poll_id IN ({placeholders})",
                poll_ids,
            ).fetchall()

        ips_by_poll: dict[int, list[str]] = {}
        for poll_id, ip in ips_rows:
            ips_by_poll.setdefault(poll_id, []).append(ip)

        return [
            {
                "poll_id": pid,
                "queried_at": int(qa),
                "response_ms": round(ms, 1) if ms else None,
                "record_count": rc,
                "error": err,
                "ips": ips_by_poll.get(pid, []),
            }
            for pid, qa, ms, rc, err in polls
        ]
