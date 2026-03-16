"""
Auth router — KYC endpoint.

POST /api/v1/auth/kyc
"""

import hashlib
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.database import supabase_client
from app.dependencies import get_current_user
from models.user import KYCRequest

logger = logging.getLogger("terratrust.auth")

router = APIRouter()


@router.post("/kyc", status_code=status.HTTP_200_OK)
async def submit_kyc(
    body: KYCRequest,
    user_id: str = Depends(get_current_user),
):
    """
    Complete KYC for the authenticated user.

    - Hashes the Aadhaar number with SHA-256 (plain text is **never** stored).
    - Stores full_name and aadhaar_hash in the ``users`` table.
    - Sets ``kyc_completed = true``.
    """
    try:
        aadhaar_hash = hashlib.sha256(body.aadhaar_number.encode("utf-8")).hexdigest()

        update_data = {
            "full_name": body.full_name,
            "aadhaar_hash": aadhaar_hash,
            "kyc_completed": True,
        }

        response = (
            supabase_client.table("users")
            .update(update_data)
            .eq("id", user_id)
            .execute()
        )

        if not response.data:
            # User row may not exist yet — upsert instead
            insert_data = {
                "id": user_id,
                **update_data,
            }
            supabase_client.table("users").upsert(insert_data).execute()

        logger.info("KYC completed for user %s", user_id)
        return {"status": "success", "user_id": user_id}

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("KYC submission failed for user %s: %s", user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="KYC processing failed. Please try again.",
        ) from exc
