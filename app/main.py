import os
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from .database import Base, engine, SessionLocal
from . import models  # noqa: F401  (register tables)
from .routes import auth_r, hosp_r, search_r, req_r, notif_r, ana_r, admin_r
from .seed import seed
from .services import expire_old_reservations

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_CANDIDATES = [
    BASE_DIR / "frontend" / "index.html",
    BASE_DIR / "index.html",
    BASE_DIR / "public" / "index.html",
    Path.cwd() / "frontend" / "index.html",
]


def find_frontend() -> Path | None:
    for p in FRONTEND_CANDIDATES:
        if p.exists():
            return p
    return None


async def expiry_loop():
    """Every 30s: expired 15-minute reservations release their capacity."""
    while True:
        await asyncio.sleep(30)
        try:
            with SessionLocal() as db:
                expire_old_reservations(db)
        except Exception as e:
            print("expiry_loop error:", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    # Vercel par background loop nahi chalti; wahan expiry requests par check hoti hai
    task = None if os.getenv("VERCEL") else asyncio.create_task(expiry_loop())
    yield
    if task:
        task.cancel()


app = FastAPI(
    title="Smart Hospital Bed & Emergency Capacity System",
    description="Need -> Capacity -> Matching -> Referral -> Admission -> Updated Capacity",
    version="2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth_r, hosp_r, search_r, req_r, notif_r, ana_r, admin_r):
    app.include_router(r)


@app.get("/", include_in_schema=False)
def frontend():
    path = find_frontend()
    if path:
        html = path.read_text(encoding="utf-8")
        # Frontend hamesha usi link ke backend se baat kare jis par khula hai
        html = html.replace("http://127.0.0.1:8000", "").replace("http://localhost:8000", "")
        return HTMLResponse(html)
    return {"message": "Smart Hospital API is Running", "docs": "/docs"}


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok", "docs": "/docs"}