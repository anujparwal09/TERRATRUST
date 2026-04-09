"""TerraTrust-AR FastAPI application entry point."""

import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

from app.config import settings
from app.firebase_auth import get_firebase_app
from app.gee import ensure_gee_initialized
from routers import auth, land, audit, credits

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("terratrust")

ALLOWED_CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
ALLOWED_CORS_HEADERS = [
    "Authorization",
    "Content-Type",
    "Accept",
    "Origin",
    "X-Requested-With",
]


def _get_cors_origins() -> list[str]:
    """Return explicit CORS origins compatible with credentialed requests."""
    configured_origins = [
        origin.strip()
        for origin in (settings.WEB_CORS_ORIGINS or "").split(",")
        if origin.strip()
    ]

    if configured_origins:
        return configured_origins

    if settings.ENVIRONMENT == "development":
        return [
            "http://localhost:8081",
            "http://127.0.0.1:8081",
            "http://localhost:19006",
            "http://127.0.0.1:19006",
        ]

    return []

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="TerraTrust-AR API",
    description="Autonomous carbon credit verification for Indian smallholder farmers.",
    version="3.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_credentials=True,
    allow_methods=ALLOWED_CORS_METHODS,
    allow_headers=ALLOWED_CORS_HEADERS,
)

# ---------------------------------------------------------------------------
# Include routers
# ---------------------------------------------------------------------------
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(land.router, prefix="/api/v1/land", tags=["Land"])
app.include_router(audit.router, prefix="/api/v1/audit", tags=["Audit"])
app.include_router(credits.router, prefix="/api/v1/credits", tags=["Credits"])


@app.middleware("http")
async def maintenance_mode_middleware(request: Request, call_next):
    """Return the documented 503 payload when maintenance mode is active."""
    exempt_paths = {
        "/health",
        "/api/v1/status",
        "/docs",
        "/redoc",
        "/openapi.json",
    }

    if settings.MAINTENANCE_MODE and request.url.path not in exempt_paths:
        return JSONResponse(
            status_code=503,
            content={
                "maintenance": True,
                "message": settings.MAINTENANCE_MESSAGE or "Scheduled maintenance in progress",
            },
        )

    return await call_next(request)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", tags=["Health"])
async def health_check():
    """Return basic service health status."""
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/status", tags=["Health"])
async def application_status():
    """Return the documented farmer-facing maintenance status payload."""
    maintenance = bool(settings.MAINTENANCE_MODE)
    return {
        "maintenance": maintenance,
        "message": settings.MAINTENANCE_MESSAGE if maintenance else None,
    }


# ---------------------------------------------------------------------------
# Startup event — initialise external SDKs
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    """Initialise Firebase Admin and Google Earth Engine on startup."""
    try:
        get_firebase_app()
        logger.info("Firebase Admin initialised.")
    except Exception as exc:
        logger.warning("Failed to initialise Firebase Admin: %s", exc)

    try:
        ensure_gee_initialized()
    except Exception as exc:
        logger.error("Failed to initialise Google Earth Engine: %s", exc)
