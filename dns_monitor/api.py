import threading
import time
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .seeds import SEEDS
from .storage import DB
from .runner import run_cycle

app = FastAPI(title="Bitcoin DNS Seed Monitor")

_db: DB | None = None

# Probe state — shared across requests
_probe_lock = threading.Lock()
_probe_status: dict = {"running": False, "started_at": None, "finished_at": None, "error": None}


def init_app(db: DB) -> FastAPI:
    global _db
    _db = db
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app


def get_db() -> DB:
    assert _db is not None, "DB not initialized — call init_app() first"
    return _db


def _run_probe_thread():
    global _probe_status
    _probe_status["running"] = True
    _probe_status["started_at"] = time.time()
    _probe_status["error"] = None
    try:
        run_cycle(get_db())
        _probe_status["finished_at"] = time.time()
    except Exception as e:
        _probe_status["error"] = str(e)
        _probe_status["finished_at"] = time.time()
    finally:
        _probe_status["running"] = False
        _probe_lock.release()


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/summary")
def api_summary():
    return get_db().latest_summary()


@app.get("/api/history")
def api_history(hours: int = Query(default=24, ge=1, le=168)):
    return get_db().reachability_history(hours)


@app.get("/api/seeds")
def api_seeds(hours: int = Query(default=24, ge=1, le=168)):
    return get_db().seed_stats(SEEDS, hours)


@app.get("/api/seeds/{seed_name:path}/history")
def api_seed_history(seed_name: str, hours: int = Query(default=168, ge=1, le=720)):
    all_seeds = [s for seeds in SEEDS.values() for s in seeds]
    if seed_name not in all_seeds:
        raise HTTPException(status_code=404, detail="Seed not found")
    return get_db().seed_history(seed_name, hours)


@app.get("/api/seeds/{seed_name:path}/nodes")
def api_seed_nodes(seed_name: str, hours: int = Query(default=168, ge=1, le=720)):
    all_seeds = [s for seeds in SEEDS.values() for s in seeds]
    if seed_name not in all_seeds:
        raise HTTPException(status_code=404, detail="Seed not found")
    return get_db().seed_nodes(seed_name, hours)


@app.get("/api/seeds/{seed_name:path}/polls")
def api_seed_polls(seed_name: str, limit: int = Query(default=50, ge=1, le=500)):
    all_seeds = [s for seeds in SEEDS.values() for s in seeds]
    if seed_name not in all_seeds:
        raise HTTPException(status_code=404, detail="Seed not found")
    return get_db().seed_poll_log(seed_name, limit)


@app.get("/api/seeds/{seed_name:path}/stale")
def api_seed_stale(seed_name: str, hours: int = Query(default=168, ge=1, le=720)):
    all_seeds = [s for seeds in SEEDS.values() for s in seeds]
    if seed_name not in all_seeds:
        raise HTTPException(status_code=404, detail="Seed not found")
    return get_db().seed_stale_nodes(seed_name, hours)


@app.get("/api/user-agents")
def api_user_agents(hours: int = Query(default=24, ge=1, le=168)):
    return get_db().user_agent_history(hours)


@app.get("/api/version-history")
def api_version_history(hours: int = Query(default=168, ge=1, le=720)):
    return get_db().version_history(hours)


@app.get("/api/network-types")
def api_network_types(hours: int = Query(default=24, ge=1, le=168)):
    return get_db().network_type_breakdown(hours)


@app.get("/api/network-types/history")
def api_network_types_history(hours: int = Query(default=168, ge=1, le=720)):
    return get_db().network_type_history(hours)


@app.get("/api/services")
def api_services(hours: int = Query(default=24, ge=1, le=168)):
    return get_db().services_breakdown(hours)


@app.get("/api/long-term-nodes")
def api_long_term_nodes(
    min_pct: float = Query(default=80.0, ge=0, le=100),
    hours: int = Query(default=168, ge=1, le=720),
):
    return get_db().long_term_nodes(min_pct, hours)


@app.get("/api/deltas")
def api_deltas():
    return get_db().node_count_deltas()


@app.get("/api/uniqueness")
def api_uniqueness(hours: int = Query(default=24, ge=1, le=168)):
    return get_db().seed_uniqueness(SEEDS, hours)


@app.get("/api/geo")
def api_geo(hours: int = Query(default=24, ge=1, le=168)):
    return get_db().geo_summary(hours)


@app.post("/api/probe")
def api_probe():
    """Trigger a probe cycle. Returns 409 if one is already running."""
    acquired = _probe_lock.acquire(blocking=False)
    if not acquired:
        raise HTTPException(status_code=409, detail="A probe cycle is already running")
    thread = threading.Thread(target=_run_probe_thread, daemon=True)
    thread.start()
    return {"status": "started", "started_at": _probe_status["started_at"]}


@app.get("/api/probe/status")
def api_probe_status():
    return {**_probe_status}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text())
