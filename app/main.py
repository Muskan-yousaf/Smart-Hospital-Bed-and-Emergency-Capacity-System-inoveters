import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .database import Base, engine, SessionLocal
from . import models  # noqa: F401  (register tables)
from .routes import auth_r, hosp_r, search_r, req_r, notif_r, ana_r, admin_r
from .seed import seed
from .services import expire_old_reservations

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


async def expiry_loop():
    """Every 30s: expired 15-minute reservations release their capacity."""
    while True:
        await asyncio.sleep(30)
        try:
            with SessionLocal() as db:
                expire_old_reservations(db)
        except Exception as e:  # never let the background loop die
            print("expiry_loop error:", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    if os.getenv("SEED_DEMO_DATA", "1") == "1":
        with SessionLocal() as db:
            seed(db)
    task = asyncio.create_task(expiry_loop())
    yield
    task.cancel()


app = FastAPI(
    title="Smart Hospital Bed & Emergency Capacity System",
    description="Need -> Capacity -> Matching -> Referral -> Admission -> Updated Capacity",
    version="2.0", lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])

for r in (auth_r, hosp_r, search_r, req_r, notif_r, ana_r, admin_r):
    app.include_router(r)


@app.get("/", include_in_schema=False)
def frontend():
    return FileResponse(FRONTEND)


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok", "docs": "/docs"}
