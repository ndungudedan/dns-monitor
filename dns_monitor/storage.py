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
    network_type     TEXT,
    user_agent       TEXT,
    services         INTEGER,
    protocol_version INTEGER,
    start_height     INTEGER,
    error            TEXT
);

CREATE TABLE IF NOT EXISTS ip_geo (
    ip           TEXT PRIMARY KEY,
    country_code TEXT,
    country_name TEXT,
    city         TEXT,
    latitude     REAL,
    longitude    REAL,
    asn          INTEGER,
    asn_org      TEXT,
    updated_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_seed_polls_seed      ON seed_polls(seed);
CREATE INDEX IF NOT EXISTS idx_seed_polls_queried   ON seed_polls(queried_at);
CREATE INDEX IF NOT EXISTS idx_spr_poll_id          ON seed_poll_records(poll_id);
CREATE INDEX IF NOT EXISTS idx_spr_ip               ON seed_poll_records(ip);
CREATE INDEX IF NOT EXISTS idx_node_probes_ip       ON node_probes(ip);
CREATE INDEX IF NOT EXISTS idx_node_probes_seed     ON node_probes(seed);
CREATE INDEX IF NOT EXISTS idx_node_probes_probed   ON node_probes(probed_at);
"""

# Additive migrations — run before schema to ensure columns exist before indexes
_MIGRATIONS = [
    "ALTER TABLE node_probes ADD COLUMN network_type TEXT",
    # Backfill network_type for rows inserted before the column existed
    """UPDATE node_probes SET network_type =
        CASE
            WHEN ip LIKE '%.onion'     THEN 'tor'
            WHEN ip LIKE '%.i2p'       THEN 'i2p'
            WHEN ip LIKE '%.b32.i2p'   THEN 'i2p'
            WHEN ip LIKE '%:%'         THEN 'ipv6'
            ELSE 'ipv4'
        END
    WHERE network_type IS NULL""",
]

_POST_MIGRATION_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_node_probes_net_type ON node_probes(network_type);
"""


class DB:
    def __init__(self, path: str | Path = "monitor.db"):
        self.path = str(path)
        self._init()

    def _init(self):
        with self._conn() as conn:
            # Migrations first so new columns exist before indexes reference them
            for migration in _MIGRATIONS:
                try:
                    conn.execute(migration)
                except sqlite3.OperationalError:
                    pass  # column already exists
            conn.executescript(SCHEMA)
            conn.executescript(_POST_MIGRATION_SCHEMA)
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
                int(r.reachable), r.connect_ms, r.network_type,
                r.user_agent, r.services, r.protocol_version, r.start_height,
                r.error,
            )
            for r in results
        ]
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO node_probes "
                "(ip, port, seed, network, probed_at, reachable, connect_ms, network_type, "
                " user_agent, services, protocol_version, start_height, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        logger.debug("Saved %d node probe records", len(rows))

    def save_geo_results(self, geo_map: dict):
        """Upsert GeoIP results. geo_map is {ip: GeoResult}."""
        if not geo_map:
            return
        now = time.time()
        rows = [
            (ip, g.country_code, g.country_name, g.city,
             g.latitude, g.longitude, g.asn, g.asn_org, now)
            for ip, g in geo_map.items()
        ]
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO ip_geo (ip, country_code, country_name, city, latitude, longitude, asn, asn_org, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(ip) DO UPDATE SET "
                "  country_code=excluded.country_code, country_name=excluded.country_name, "
                "  city=excluded.city, latitude=excluded.latitude, longitude=excluded.longitude, "
                "  asn=excluded.asn, asn_org=excluded.asn_org, updated_at=excluded.updated_at",
                rows,
            )
        logger.debug("Saved %d geo records", len(rows))

    def latest_summary(self) -> dict:
        with self._conn() as conn:
            # Anchor to the most recent probe cycle (within 15-min window of latest probe)
            latest = conn.execute("SELECT MAX(probed_at) FROM node_probes").fetchone()[0]
            if not latest:
                return {"total_nodes": 0, "reachable": 0, "unreachable": 0,
                        "seeds": [], "user_agents": []}
            since_probe = latest - 900  # 15-minute cycle window

            # Use last 2 hours for seed polls (they may not align perfectly with probes)
            since_seeds = time.time() - 7200

            row = conn.execute(
                "SELECT COUNT(*), SUM(reachable), SUM(1 - reachable) "
                "FROM node_probes WHERE probed_at > ?",
                (since_probe,),
            ).fetchone()
            total, reachable, unreachable = row

            seeds = conn.execute(
                "SELECT seed, record_count, error FROM seed_polls "
                "WHERE queried_at > ? ORDER BY queried_at DESC",
                (since_seeds,),
            ).fetchall()

            user_agents = conn.execute(
                "SELECT user_agent, COUNT(*) as cnt FROM node_probes "
                "WHERE probed_at > ? AND reachable = 1 AND user_agent IS NOT NULL "
                "GROUP BY user_agent ORDER BY cnt DESC LIMIT 20",
                (since_probe,),
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

    # -------------------------------------------------------------------------
    # Network type breakdown
    # -------------------------------------------------------------------------

    def network_type_breakdown(self, hours: int = 24) -> dict:
        """Reachable node counts by network type (ipv4/ipv6/tor/i2p)."""
        since = time.time() - hours * 3600
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT network_type, COUNT(*) AS cnt
                FROM node_probes
                WHERE probed_at > ? AND reachable = 1
                GROUP BY network_type
                ORDER BY cnt DESC
                """,
                (since,),
            ).fetchall()
        return {nt or "unknown": n for nt, n in rows}

    def network_type_history(self, hours: int = 168) -> list[dict]:
        """Reachable counts per network_type over time, 10-min buckets."""
        since = time.time() - hours * 3600
        bucket = 600
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    CAST(probed_at / ? AS INTEGER) * ? AS ts,
                    network_type,
                    COUNT(*) AS cnt
                FROM node_probes
                WHERE probed_at > ? AND reachable = 1
                GROUP BY ts, network_type
                ORDER BY ts
                """,
                (bucket, bucket, since),
            ).fetchall()

        # Pivot into [{timestamp, ipv4, ipv6, tor, i2p}, ...]
        pivot: dict[int, dict] = {}
        for ts, nt, cnt in rows:
            pivot.setdefault(int(ts), {})[nt or "unknown"] = cnt
        return [{"timestamp": ts, **counts} for ts, counts in sorted(pivot.items())]

    # -------------------------------------------------------------------------
    # Services breakdown
    # -------------------------------------------------------------------------

    def services_breakdown(self, hours: int = 24) -> list[dict]:
        """Count of reachable nodes supporting each service flag."""
        from .handshake import SERVICE_FLAGS
        since = time.time() - hours * 3600
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT services FROM node_probes WHERE probed_at > ? AND reachable = 1 AND services IS NOT NULL",
                (since,),
            ).fetchall()
        total = len(rows)
        counts = {name: 0 for name in SERVICE_FLAGS}
        for (svc,) in rows:
            for name, flag in SERVICE_FLAGS.items():
                if svc & flag:
                    counts[name] += 1
        return [
            {"service": name, "count": cnt, "pct": round(cnt / total * 100, 1) if total else 0}
            for name, cnt in sorted(counts.items(), key=lambda x: -x[1])
        ]

    # -------------------------------------------------------------------------
    # Stale nodes
    # -------------------------------------------------------------------------

    def seed_stale_nodes(self, seed: str, hours: int = 168) -> list[dict]:
        """
        IPs that this seed has returned in DNS polls but were never reachable
        in the window. These are candidates for stale / dead records.
        """
        since = time.time() - hours * 3600
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    spr.ip,
                    COUNT(DISTINCT sp.id)                              AS times_returned,
                    COALESCE(SUM(np.reachable), 0)                     AS times_reachable,
                    MAX(sp.queried_at)                                 AS last_returned
                FROM seed_poll_records spr
                JOIN seed_polls sp ON sp.id = spr.poll_id
                LEFT JOIN node_probes np ON np.ip = spr.ip AND np.seed = sp.seed AND np.probed_at > ?
                WHERE sp.seed = ? AND sp.queried_at > ?
                GROUP BY spr.ip
                HAVING times_reachable = 0
                ORDER BY times_returned DESC
                """,
                (since, seed, since),
            ).fetchall()
        return [
            {"ip": ip, "times_returned": tr, "last_returned": int(lr)}
            for ip, tr, _, lr in rows
        ]

    # -------------------------------------------------------------------------
    # Duplicate / uniqueness
    # -------------------------------------------------------------------------

    def seed_uniqueness(self, known_seeds: dict[str, list[str]], hours: int = 24) -> list[dict]:
        """
        For each seed, the % of its returned IPs that no other seed returned
        in the same window. High uniqueness = more valuable to the network.
        Only includes currently known seeds.
        """
        all_seeds = [s for seeds in known_seeds.values() for s in seeds]
        since = time.time() - hours * 3600
        placeholders = ",".join("?" * len(all_seeds))
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT sp.seed,
                    COUNT(DISTINCT spr.ip)                        AS total_ips,
                    COUNT(DISTINCT CASE
                        WHEN (
                            SELECT COUNT(DISTINCT sp2.seed)
                            FROM seed_poll_records spr2
                            JOIN seed_polls sp2 ON sp2.id = spr2.poll_id
                            WHERE spr2.ip = spr.ip AND sp2.queried_at > ?
                        ) = 1 THEN spr.ip END)                    AS unique_ips
                FROM seed_poll_records spr
                JOIN seed_polls sp ON sp.id = spr.poll_id
                WHERE sp.queried_at > ? AND sp.seed IN ({placeholders})
                GROUP BY sp.seed
                """,
                (since, since, *all_seeds),
            ).fetchall()
        db_results = {
            seed: {"total_ips": total, "unique_ips": uniq,
                   "uniqueness_pct": round(uniq / total * 100, 1) if total else 0}
            for seed, total, uniq in rows
        }
        return [
            {"seed": seed, **db_results.get(seed, {"total_ips": 0, "unique_ips": 0, "uniqueness_pct": 0})}
            for seed in all_seeds
        ]

    # -------------------------------------------------------------------------
    # Long-term nodes
    # -------------------------------------------------------------------------

    def long_term_nodes(self, min_pct: float = 80.0, hours: int = 168) -> list[dict]:
        """Nodes reachable in >= min_pct% of probe cycles over the window."""
        since = time.time() - hours * 3600
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    ip,
                    COUNT(*)          AS total_probes,
                    SUM(reachable)    AS reachable_probes,
                    MIN(probed_at)    AS first_seen,
                    MAX(probed_at)    AS last_seen,
                    MAX(CASE WHEN reachable=1 THEN user_agent END)  AS user_agent,
                    MAX(CASE WHEN reachable=1 THEN network_type END) AS network_type
                FROM node_probes
                WHERE probed_at > ?
                GROUP BY ip
                HAVING CAST(reachable_probes AS REAL) / total_probes * 100 >= ?
                ORDER BY reachable_probes DESC
                """,
                (since, min_pct),
            ).fetchall()
        return [
            {
                "ip": ip,
                "total_probes": tp,
                "reachable_probes": int(rp or 0),
                "uptime_pct": round(rp / tp * 100, 1) if tp else 0,
                "first_seen": int(fs),
                "last_seen": int(ls),
                "user_agent": ua,
                "network_type": nt,
            }
            for ip, tp, rp, fs, ls, ua, nt in rows
        ]

    # -------------------------------------------------------------------------
    # Version adoption over time
    # -------------------------------------------------------------------------

    def version_history(self, hours: int = 168, top_n: int = 8) -> dict:
        """
        UA distribution as a time series. Returns top_n UAs + buckets.
        Shape: {labels: [...timestamps], series: [{ua, data: [counts]}]}
        """
        since = time.time() - hours * 3600
        bucket = 600
        with self._conn() as conn:
            # Top N user agents in the window
            top = [
                row[0] for row in conn.execute(
                    """
                    SELECT user_agent FROM node_probes
                    WHERE probed_at > ? AND reachable=1 AND user_agent IS NOT NULL
                    GROUP BY user_agent ORDER BY COUNT(*) DESC LIMIT ?
                    """,
                    (since, top_n),
                ).fetchall()
            ]
            if not top:
                return {"labels": [], "series": []}

            rows = conn.execute(
                """
                SELECT
                    CAST(probed_at / ? AS INTEGER) * ? AS ts,
                    user_agent,
                    COUNT(*) AS cnt
                FROM node_probes
                WHERE probed_at > ? AND reachable=1 AND user_agent IN ({})
                GROUP BY ts, user_agent
                ORDER BY ts
                """.format(",".join("?" * len(top))),
                (bucket, bucket, since, *top),
            ).fetchall()

        all_ts = sorted({int(r[0]) for r in rows})
        pivot: dict[str, dict[int, int]] = {ua: {} for ua in top}
        for ts, ua, cnt in rows:
            if ua in pivot:
                pivot[ua][int(ts)] = cnt

        return {
            "labels": all_ts,
            "series": [
                {"ua": ua, "data": [pivot[ua].get(ts, 0) for ts in all_ts]}
                for ua in top
            ],
        }

    # -------------------------------------------------------------------------
    # Node count deltas
    # -------------------------------------------------------------------------

    def node_count_deltas(self) -> dict:
        """
        Total reachable node counts now vs 7d and 30d ago, overall and by
        network type.
        """
        now   = time.time()
        w1    = 3600       # 1-hour window for "current"
        w7d   = 3600 * 24 * 7
        w30d  = 3600 * 24 * 30

        def _counts(since: float, until: float) -> dict:
            with self._conn() as conn:
                rows = conn.execute(
                    """
                    SELECT network_type, COUNT(DISTINCT ip) AS cnt
                    FROM node_probes
                    WHERE probed_at BETWEEN ? AND ? AND reachable = 1
                    GROUP BY network_type
                    """,
                    (since, until),
                ).fetchall()
            total_row = sum(r[1] for r in rows)
            by_type = {nt or "unknown": n for nt, n in rows}
            return {"total": total_row, **by_type}

        current  = _counts(now - w1,                   now)
        week_ago = _counts(now - w7d  - w1,            now - w7d)
        month_ago= _counts(now - w30d - w1,            now - w30d)

        def _delta(a: dict, b: dict) -> dict:
            keys = set(a) | set(b)
            return {k: a.get(k, 0) - b.get(k, 0) for k in keys}

        return {
            "current":    current,
            "delta_7d":   _delta(current, week_ago),
            "delta_30d":  _delta(current, month_ago),
        }

    # -------------------------------------------------------------------------
    # GeoIP summaries
    # -------------------------------------------------------------------------

    def geo_summary(self, hours: int = 24) -> dict:
        """Country and ASN distribution of reachable nodes."""
        since = time.time() - hours * 3600
        with self._conn() as conn:
            countries = conn.execute(
                """
                SELECT g.country_code, g.country_name, COUNT(DISTINCT np.ip) AS cnt
                FROM node_probes np
                JOIN ip_geo g ON g.ip = np.ip
                WHERE np.probed_at > ? AND np.reachable = 1 AND g.country_code IS NOT NULL
                GROUP BY g.country_code ORDER BY cnt DESC LIMIT 20
                """,
                (since,),
            ).fetchall()
            asns = conn.execute(
                """
                SELECT g.asn, g.asn_org, COUNT(DISTINCT np.ip) AS cnt
                FROM node_probes np
                JOIN ip_geo g ON g.ip = np.ip
                WHERE np.probed_at > ? AND np.reachable = 1 AND g.asn IS NOT NULL
                GROUP BY g.asn ORDER BY cnt DESC LIMIT 15
                """,
                (since,),
            ).fetchall()
        return {
            "countries": [{"code": c, "name": n, "count": cnt} for c, n, cnt in countries],
            "asns": [{"asn": a, "org": o, "count": cnt} for a, o, cnt in asns],
        }
