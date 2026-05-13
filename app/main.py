"""
app/main.py
────────────
FastAPI application entry-point.

Lifespan:
  startup  → configure_logging(), init_db()
  shutdown → (nothing; connection pool cleaned up automatically)
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import init_db
from app.core.logging_config import configure_logging, get_logger
from app.api.v1.evaluations import router as evaluations_router
from app.api.v1.health import router as health_router
from app.api.v1.reports import router as reports_router
from app.api.v1.uploads import router as uploads_router
from app.api.v1.auth import router as auth_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    logger = get_logger(__name__)
    logger.info("startup", debug=settings.debug)
    await init_db()
    yield
    logger.info("shutdown")


app = FastAPI(
    title="Employee Performance Evaluation API",
    description="AI-powered multi-team performance evaluation system.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production via settings
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
API_PREFIX = "/api/v1"
app.include_router(health_router)  # /health (no prefix)
app.include_router(auth_router, prefix=API_PREFIX)
app.include_router(evaluations_router, prefix=API_PREFIX)
app.include_router(uploads_router, prefix=API_PREFIX)
app.include_router(reports_router, prefix=API_PREFIX)
