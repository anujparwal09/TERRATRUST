"""
TerraTrust-AR Backend — main application entry point.

FastAPI application with CORS middleware, router includes,
health check, and GEE initialization on startup.
"""

import os
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from app.config import settings
from routers import auth, land, audit, credits
from app.api import upload

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("terratrust")

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="TerraTrust-AR API",
    description="Autonomous carbon credit verification for Indian smallholder farmers.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------
cors_origins = ["*"] if settings.ENVIRONMENT == "development" else [settings.CORS_ORIGIN]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Include routers
# ---------------------------------------------------------------------------
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(land.router, prefix="/api/v1/land", tags=["Land"])
app.include_router(audit.router, prefix="/api/v1/audit", tags=["Audit"])
app.include_router(credits.router, prefix="/api/v1/credits", tags=["Credits"])
app.include_router(upload.router, prefix="/api/v1/document", tags=["Blockchain"])


# ---------------------------------------------------------------------------
# Root endpoint
# ---------------------------------------------------------------------------
@app.get("/", tags=["Health"])
def root():
    """Root endpoint — confirms the backend is running."""
    return {"message": "TerraTrust backend running"}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", tags=["Health"])
async def health_check():
    """Return basic service health status."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Startup event — initialise Google Earth Engine
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    """Initialise Google Earth Engine with service account on startup."""
    try:
        import ee

        gee_key_path = settings.GEE_SERVICE_ACCOUNT_KEY_PATH
        gee_email = settings.GEE_SERVICE_ACCOUNT_EMAIL

        if gee_key_path and os.path.exists(gee_key_path):
            credentials = ee.ServiceAccountCredentials(gee_email, gee_key_path)
            ee.Initialize(credentials)
            logger.info("Google Earth Engine initialised with service account.")
        else:
            logger.warning(
                "GEE service-account key file not found at '%s'. "
                "GEE-dependent features will be unavailable.",
                gee_key_path,
            )
    except Exception as exc:
        logger.error("Failed to initialise Google Earth Engine: %s", exc)
