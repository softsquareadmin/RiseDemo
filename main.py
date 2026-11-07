"""RepAssist - Hotel Booking Conversation Assistant. Run with:  python main.py"""
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import configurations, sessions, websocket
from app.config import BASE_DIR, Settings
from app.services.gemini_client import GeminiClient
from app.services.session_service import SessionManager
from app.utils.storage import ConfigStore, SessionStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # never log request details
logging.getLogger("google_genai").setLevel(logging.WARNING)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.configs.seed_defaults()
        purged = app.state.store.purge_older_than(settings.retention_days)
        if purged:
            logging.getLogger("repassist").info("Retention: removed %d expired session(s)", purged)
        if not settings.configured:
            logging.getLogger("repassist").warning("No GOOGLE_API_KEY / GEMINI_API_KEY found - AI features disabled until set in .env")
        yield

    app = FastAPI(title="RepAssist", lifespan=lifespan)
    app.state.settings = settings
    app.state.gemini = GeminiClient(settings)
    app.state.configs = ConfigStore(settings.configs_dir, settings.templates_dir)
    app.state.store = SessionStore(settings.sessions_dir, persist=settings.save_sessions)
    app.state.mgr = SessionManager(settings, app.state.gemini, app.state.store, app.state.configs)

    app.include_router(sessions.router)
    app.include_router(configurations.router)
    app.include_router(websocket.router)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    return app


app = create_app()

if __name__ == "__main__":
    s = app.state.settings
    print(f"\n  RepAssist running at  http://localhost:{s.port}\n")
    if not s.configured:
        print("  WARNING: no GOOGLE_API_KEY / GEMINI_API_KEY in .env - add one and restart.\n")
    uvicorn.run(app, host=s.host, port=s.port, log_level="warning")
