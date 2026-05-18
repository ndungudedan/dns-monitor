from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .storage import DB

app = FastAPI(title="Bitcoin DNS Seed Monitor")

_db: DB | None = None


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
    return get_db().seed_stats(hours)


@app.get("/api/user-agents")
def api_user_agents(hours: int = Query(default=24, ge=1, le=168)):
    return get_db().user_agent_history(hours)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text())
