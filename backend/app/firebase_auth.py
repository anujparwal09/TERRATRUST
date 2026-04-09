"""Firebase Admin initialisation and ID-token verification helpers."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

import firebase_admin
from firebase_admin import auth, credentials

from app.config import settings

logger = logging.getLogger("terratrust.firebase_auth")


def resolve_google_credentials_path() -> Path:
    """Resolve the shared Google credentials file relative to the backend root."""
    configured_path = Path(settings.GOOGLE_APPLICATION_CREDENTIALS)
    if configured_path.is_absolute():
        return configured_path
    return Path(__file__).resolve().parents[1] / configured_path


def _ensure_google_credentials_env() -> Path:
    """Export the documented ADC credential path for Google SDKs."""
    credentials_path = resolve_google_credentials_path()
    if not credentials_path.exists():
        raise FileNotFoundError(
            "Shared Google credentials file not found at "
            f"'{credentials_path}'."
        )

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)
    return credentials_path


def get_firebase_app() -> firebase_admin.App:
    """Return the shared Firebase Admin app, initialising it on first use."""
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    credentials_path = _ensure_google_credentials_env()
    app = firebase_admin.initialize_app(
        credentials.ApplicationDefault(),
        {"projectId": settings.FIREBASE_PROJECT_ID},
    )
    logger.info("Firebase Admin initialised using ADC from %s", credentials_path)
    return app


def verify_firebase_token(id_token: str) -> Dict[str, Any]:
    """Verify a Firebase ID token and return the decoded claims."""
    return auth.verify_id_token(id_token, app=get_firebase_app())