"""
Shared FastAPI dependencies.

- ``get_current_user``: verifies the Supabase JWT from the Authorization
  header and returns the authenticated user id.
"""

import logging
from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from app.database import supabase_client

logger = logging.getLogger("terratrust.dependencies")


async def get_current_user(authorization: Optional[str] = Header(None)) -> str:
    """Verify the Supabase JWT and return the authenticated user's UUID.

    Expects the ``Authorization`` header in the form ``Bearer <jwt>``.

    Raises
    ------
    HTTPException 401
        If the header is missing or the token is invalid / expired.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header. Expected 'Bearer <token>'.",
        )

    token = authorization.removeprefix("Bearer ").strip()

    try:
        user_response = supabase_client.auth.get_user(token)
        user = user_response.user
        if user is None:
            raise ValueError("No user returned")
        return user.id
    except Exception as exc:
        logger.warning("JWT verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token.",
        ) from exc
