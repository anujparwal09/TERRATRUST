"""Shared Google Earth Engine initialization helpers."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import ee

from app.config import settings

logger = logging.getLogger("terratrust.gee")


def resolve_google_credentials_path() -> Path:
    """Resolve the ADC credential path relative to the backend root."""
    configured_path = Path(settings.GOOGLE_APPLICATION_CREDENTIALS)
    if configured_path.is_absolute():
        return configured_path
    return Path(__file__).resolve().parents[1] / configured_path


def has_gee_configuration() -> bool:
    """Return whether the shared Google credentials file is available."""
    return resolve_google_credentials_path().exists()


def ensure_gee_initialized() -> None:
    """Initialise Google Earth Engine once using shared ADC credentials."""
    try:
        ee.Number(1).getInfo()
        return
    except Exception:
        pass

    credentials_path = resolve_google_credentials_path()
    if not credentials_path.exists():
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS does not point to a readable file: "
            f"'{credentials_path}'."
        )

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)
    ee.Initialize(project=settings.GOOGLE_CLOUD_PROJECT)
    logger.info(
        "Google Earth Engine initialised for project %s using ADC from %s.",
        settings.GOOGLE_CLOUD_PROJECT,
        credentials_path,
    )