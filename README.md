# Bitcoin DNS Seed Monitor

Open-source monitoring for Bitcoin DNS seeds. Tracks seed health, node reachability, and version distribution across all Bitcoin networks — with a live web dashboard.

Inspired by [0xB10C's project idea](https://github.com/0xB10C/project-ideas/issues/14) and the prior work at [21.ninja/dns-seeds](https://21.ninja/dns-seeds/).

## What it does

Every probe cycle the monitor:

1. **Resolves** each DNS seed and records response time and IP count
2. **Probes** every returned node via the Bitcoin P2P handshake, collecting:
   - Reachability (TCP + version handshake)
   - `UserAgent` (e.g. `/Satoshi:27.0.0/`)
   - Services bitmask (`NODE_NETWORK`, `NODE_WITNESS`, `NODE_BLOOM`, etc.)
   - Protocol version and block height
3. **Stores** everything in SQLite (WAL mode)
4. **Serves** a live dashboard with charts and per-seed health tables

## Networks covered

- **Mainnet** (port 8333)
- **Testnet3** (port 18333)
- **Signet** (port 38333)

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager)

## Quick start

```bash
git clone https://github.com/yourname/dns-monitor
cd dns-monitor

# Install dependencies
uv sync

# Run one probe cycle (populates the DB)
uv run python main.py once

# Start the web dashboard
uv run python serve.py
# → http://localhost:8000
```

## Running continuously

Open two terminals (or use systemd — see [Deployment](#deployment)):

```bash
# Terminal 1: monitor loop (every 10 minutes)
uv run python main.py watch --interval 600

# Terminal 2: web server
uv run python serve.py --host 0.0.0.0 --port 8000
```

Both processes share the same `monitor.db` file. SQLite WAL mode handles concurrent reads and writes safely.

## CLI reference

```
uv run python main.py [--db PATH] [--network mainnet|testnet3|signet] <command>

Commands:
  once      Run one probe cycle and print a JSON summary
  watch     Run continuously (--interval seconds, default 600)
  summary   Print the latest summary from the DB and exit
```

```
uv run python serve.py [--db PATH] [--host HOST] [--port PORT]
```

## Dashboard

`http://localhost:8000`

| Panel | What it shows |
|---|---|
| Stat cards | Reachable nodes, unreachable nodes, total probed, seeds up/down |
| Reachability chart | % reachable over the last 24h in 10-minute buckets |
| Version chart | Node version distribution (doughnut) |
| Seeds table | Per-seed status, record count, reachable %, avg DNS response time, last poll |

Auto-refreshes every 60 seconds.

## REST API

All endpoints accept an optional `?hours=N` query param (1–168, default 24).

| Endpoint | Description |
|---|---|
| `GET /api/summary` | Current totals and per-seed status |
| `GET /api/history` | Reachability % over time (10-min buckets) |
| `GET /api/seeds` | Per-seed breakdown with stats |
| `GET /api/user-agents` | Node version distribution |

Interactive API docs: `http://localhost:8000/docs`

## Deployment

### systemd

Two unit files — one for the monitor loop, one for the web server.

**`/etc/systemd/system/dns-monitor.service`**
```ini
[Unit]
Description=Bitcoin DNS Seed Monitor
After=network.target

[Service]
Type=simple
User=dns-monitor
WorkingDirectory=/opt/dns-monitor
ExecStart=/opt/dns-monitor/.venv/bin/python main.py watch --interval 600
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/dns-monitor-web.service`**
```ini
[Unit]
Description=Bitcoin DNS Seed Monitor Web
After=network.target dns-monitor.service

[Service]
Type=simple
User=dns-monitor
WorkingDirectory=/opt/dns-monitor
ExecStart=/opt/dns-monitor/.venv/bin/python serve.py --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now dns-monitor dns-monitor-web
```

Put nginx or Caddy in front of port 8000 for TLS.

### Docker

Coming soon.

## Project layout

```
dns-monitor/
├── dns_monitor/
│   ├── seeds.py       — seed lists for all networks
│   ├── resolver.py    — DNS A-record resolution with timing
│   ├── handshake.py   — Bitcoin P2P version handshake (pure stdlib)
│   ├── prober.py      — concurrent node prober (ThreadPoolExecutor)
│   ├── storage.py     — SQLite schema, writes, and query methods
│   ├── runner.py      — probe cycle orchestration
│   ├── api.py         — FastAPI app and route definitions
│   └── static/
│       └── index.html — dashboard (Chart.js, no build step)
├── main.py            — CLI entrypoint (once / watch / summary)
├── serve.py           — web server entrypoint
└── pyproject.toml
```

## Contributing

Pull requests welcome. Some good first contributions:

- Tor and I2P support in the prober
- Stale node detection (IPs that never change across cycles)
- Alerting (webhook / email when a seed goes down)
- Docker Compose setup
- Additional community seeds

## License

MIT
