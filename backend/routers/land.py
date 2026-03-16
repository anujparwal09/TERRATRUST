"""
Land router — document verification, boundary fetch, and registration.

POST /api/v1/land/verify-document
GET  /api/v1/land/fetch-boundary
POST /api/v1/land/register
"""

import logging
import unicodedata
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from shapely.geometry import shape

from app.database import supabase_client
from app.dependencies import get_current_user
from models.land import (
    BoundaryFetchResponse,
    DocumentUploadResponse,
    LandRegisterRequest,
    LandRegisterResponse,
)
from services import land_boundary_service, ocr_service

logger = logging.getLogger("terratrust.land")

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper: normalise names for comparison
# ---------------------------------------------------------------------------
def _normalise_name(name: str) -> str:
    """Lower-case, strip accents / diacritics, collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(ascii_only.lower().split())


# ---------------------------------------------------------------------------
# POST /verify-document
# ---------------------------------------------------------------------------
@router.post(
    "/verify-document",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_200_OK,
)
async def verify_document(
    file: UploadFile = File(..., description="Scanned land document image"),
    _user_id: str = Depends(get_current_user),
):
    """Extract structured fields from a scanned land document using OCR."""
    try:
        image_bytes = await file.read()
        fields = ocr_service.extract_fields_from_document(image_bytes)
        return DocumentUploadResponse(
            survey_number=fields["survey_number"],
            owner_name=fields["owner_name"],
            village=fields.get("village"),
            taluka=fields.get("taluka"),
            district=fields.get("district"),
            extraction_confidence=fields["extraction_confidence"],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.error("Document verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document processing failed.",
        ) from exc


# ---------------------------------------------------------------------------
# GET /fetch-boundary
# ---------------------------------------------------------------------------
@router.get("/fetch-boundary", response_model=BoundaryFetchResponse)
async def fetch_boundary(
    survey_number: str = Query(...),
    district: str = Query(...),
    taluka: str = Query(...),
    village: str = Query(...),
    state: str = Query(...),
    user_lat: float = Query(...),
    user_lng: float = Query(...),
    _user_id: str = Depends(get_current_user),
):
    """Attempt to auto-fetch the land boundary from government GIS layers."""
    result = await land_boundary_service.fetch_land_boundary(
        survey_number=survey_number,
        district=district,
        taluka=taluka,
        village=village,
        state=state,
        user_lat=user_lat,
        user_lng=user_lng,
    )
    return BoundaryFetchResponse(**result)


# ---------------------------------------------------------------------------
# POST /register
# ---------------------------------------------------------------------------
@router.post(
    "/register",
    response_model=LandRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_land(
    body: LandRegisterRequest,
    user_id: str = Depends(get_current_user),
):
    """Register a verified land parcel.

    Validations
    -----------
    1. OCR owner name must match KYC name (normalised comparison).
    2. GeoJSON must produce a valid Shapely polygon.
    3. No duplicate (same user + survey_number).
    """

    # --- 1. Cross-check owner name with KYC name --------------------------
    try:
        user_resp = (
            supabase_client.table("users")
            .select("full_name")
            .eq("id", user_id)
            .single()
            .execute()
        )
        kyc_name = user_resp.data.get("full_name", "")
    except Exception:
        kyc_name = ""

    if not kyc_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="KYC must be completed before registering land.",
        )

    if _normalise_name(body.ocr_owner_name) != _normalise_name(kyc_name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Document owner name '{body.ocr_owner_name}' does not "
                f"match your KYC name '{kyc_name}'."
            ),
        )

    # --- 2. Validate GeoJSON with Shapely ----------------------------------
    try:
        geom = shape(body.geojson)
        if not geom.is_valid:
            raise ValueError("Invalid geometry")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid GeoJSON geometry: {exc}",
        ) from exc

    # --- 3. Duplicate check ------------------------------------------------
    dup_check = (
        supabase_client.table("land_parcels")
        .select("id")
        .eq("user_id", user_id)
        .eq("survey_number", body.survey_number)
        .execute()
    )
    if dup_check.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This survey number is already registered under your account.",
        )

    # --- 4. Calculate area in hectares -------------------------------------
    # Approximate conversion for small areas in decimal-degree geometry
    # 1° lat ≈ 111 320 m, area in sq-degrees → sq-metres → hectares
    area_sq_deg = geom.area
    area_hectares = round(area_sq_deg * 111320 * 111320 / 10000, 4)

    # --- 5. Insert into Supabase -------------------------------------------
    land_id = str(uuid.uuid4())
    insert_data: Dict[str, Any] = {
        "id": land_id,
        "user_id": user_id,
        "farm_name": body.farm_name,
        "survey_number": body.survey_number,
        "district": body.district,
        "taluka": body.taluka,
        "village": body.village,
        "state": body.state,
        "boundary_source": body.boundary_source,
        "geojson": body.geojson,
        "area_hectares": area_hectares,
        "status": "verified",
    }

    try:
        supabase_client.table("land_parcels").insert(insert_data).execute()
    except Exception as exc:
        logger.error("Failed to insert land parcel: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register land parcel.",
        ) from exc

    logger.info("Land parcel %s registered for user %s", land_id, user_id)
    return LandRegisterResponse(
        land_id=land_id,
        area_hectares=area_hectares,
        status="verified",
    )
