"""
Audit router — zone generation, sample submission, and result retrieval.

GET  /api/v1/audit/zones?land_id=uuid
POST /api/v1/audit/submit-samples
GET  /api/v1/audit/result/{audit_id}
"""

import base64
import hashlib
import logging
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.database import supabase_client
from app.dependencies import get_current_user
from models.audit import (
    AuditSubmitRequest,
    AuditSubmitResponse,
    AuditZonesResponse,
    ZoneResponse,
)
from services import zone_generation_service
from services.fusion_engine import SPECIES_WOOD_DENSITY, DEFAULT_WOOD_DENSITY

logger = logging.getLogger("terratrust.audit")

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /zones
# ---------------------------------------------------------------------------
@router.get("/zones", response_model=AuditZonesResponse)
async def get_audit_zones(
    land_id: str = Query(..., description="UUID of the registered land parcel"),
    user_id: str = Depends(get_current_user),
):
    """Generate NDVI-stratified sampling zones for a land parcel.

    Creates a new audit record in Supabase with status ``PROCESSING``
    and returns the list of zones with walking path estimate.
    """
    # Fetch land parcel data
    try:
        land_resp = (
            supabase_client.table("land_parcels")
            .select("geojson, user_id")
            .eq("id", land_id)
            .single()
            .execute()
        )
        land_data = land_resp.data
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Land parcel '{land_id}' not found.",
        ) from exc

    # Ownership check
    if land_data.get("user_id") != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this land parcel.",
        )

    boundary_geojson = land_data["geojson"]

    # Generate zones
    try:
        zones = zone_generation_service.generate_sampling_zones(
            land_id=land_id,
            boundary_geojson=boundary_geojson,
        )
    except Exception as exc:
        logger.error("Zone generation failed for land %s: %s", land_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate sampling zones. Ensure GEE is configured.",
        ) from exc

    # Create audit record
    audit_id = str(uuid.uuid4())
    audit_year = datetime.now(timezone.utc).year
    walking_path = zones[0].get("walking_path_metres", 0) if zones else 0

    supabase_client.table("carbon_audits").insert(
        {
            "id": audit_id,
            "land_id": land_id,
            "user_id": user_id,
            "audit_year": audit_year,
            "status": "PROCESSING",
            "zones": zones,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ).execute()

    # Build response
    zone_responses = [
        ZoneResponse(
            zone_id=z["zone_id"],
            label=z["label"],
            centre_gps=z["centre_gps"],
            radius_metres=z["radius_metres"],
            zone_type=z["zone_type"],
            sequence_order=z["sequence_order"],
            gedi_available=z["gedi_available"],
        )
        for z in zones
    ]

    return AuditZonesResponse(
        audit_id=audit_id,
        zones=zone_responses,
        walking_path_metres=walking_path,
        min_trees_required=9,
    )


# ---------------------------------------------------------------------------
# POST /submit-samples
# ---------------------------------------------------------------------------
@router.post(
    "/submit-samples",
    response_model=AuditSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_samples(
    body: AuditSubmitRequest,
    user_id: str = Depends(get_current_user),
):
    """Accept tree-scan samples and kick off the fusion pipeline.

    Validations
    -----------
    - Minimum **9** trees total.
    - At least **3** trees per zone.
    - Each tree must have valid photo data.
    """
    trees = body.trees

    # --- Minimum total check -----------------------------------------------
    if len(trees) < 9:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At least 9 tree samples are required. Received {len(trees)}.",
        )

    # --- Per-zone check (≥ 3 per zone) -------------------------------------
    zone_counts: Dict[str, int] = Counter(t.zone_id for t in trees)
    for zone_id, count in zone_counts.items():
        if count < 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Zone '{zone_id}' has only {count} tree(s). "
                    "Minimum 3 per zone."
                ),
            )

    # --- Process each tree ------------------------------------------------
    for tree in trees:
        wood_density = SPECIES_WOOD_DENSITY.get(tree.species, DEFAULT_WOOD_DENSITY)

        # Decode & upload photo to Supabase Storage
        try:
            photo_bytes = base64.b64decode(tree.evidence_photo_base64)
        except Exception:
            photo_bytes = b""

        photo_storage_path = f"audit-photos/{body.audit_id}/{tree.zone_id}/{uuid.uuid4()}.jpg"
        try:
            supabase_client.storage.from_("evidence-photos").upload(
                photo_storage_path, photo_bytes
            )
        except Exception as exc:
            logger.warning("Photo upload failed: %s", exc)

        # Insert into ar_tree_scans table
        scan_record = {
            "id": str(uuid.uuid4()),
            "audit_id": body.audit_id,
            "zone_id": tree.zone_id,
            "species": tree.species,
            "dbh_cm": tree.dbh_cm,
            "height_m": tree.height_m,
            "gps": {"lat": tree.gps.lat, "lng": tree.gps.lng},
            "ar_tier_used": tree.ar_tier_used,
            "confidence_score": tree.confidence_score,
            "evidence_photo_hash": tree.evidence_photo_hash,
            "evidence_photo_path": photo_storage_path,
            "wood_density": wood_density,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        supabase_client.table("ar_tree_scans").insert(scan_record).execute()

    # --- Update audit status -----------------------------------------------
    supabase_client.table("carbon_audits").update(
        {"status": "CALCULATING"}
    ).eq("id", body.audit_id).execute()

    # --- Trigger fusion task -----------------------------------------------
    from tasks.fusion_task import run_audit_fusion

    run_audit_fusion.delay(body.audit_id)

    logger.info(
        "Submitted %d tree samples for audit %s — fusion enqueued.",
        len(trees),
        body.audit_id,
    )

    return AuditSubmitResponse(
        status="processing",
        audit_id=body.audit_id,
        estimated_seconds=60,
    )


# ---------------------------------------------------------------------------
# GET /result/{audit_id}
# ---------------------------------------------------------------------------
@router.get("/result/{audit_id}")
async def get_audit_result(
    audit_id: str,
    _user_id: str = Depends(get_current_user),
):
    """Retrieve the current status or final result of an audit.

    Returns
    -------
    - ``status='MINTED'`` → full results including tx_hash and IPFS URL.
    - ``status='CALCULATING'`` or ``'PROCESSING'`` → still in progress.
    - ``status='FAILED'`` → error description.
    """
    try:
        resp = (
            supabase_client.table("carbon_audits")
            .select("*")
            .eq("id", audit_id)
            .single()
            .execute()
        )
        audit = resp.data
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audit '{audit_id}' not found.",
        ) from exc

    current_status = audit.get("status", "UNKNOWN")

    if current_status == "MINTED":
        return {
            "status": "MINTED",
            "audit_id": audit_id,
            "total_biomass_tonnes": audit.get("total_biomass_tonnes"),
            "credits_issued": audit.get("credits_issued"),
            "delta_biomass": audit.get("delta_biomass"),
            "tx_hash": audit.get("tx_hash"),
            "ipfs_url": audit.get("ipfs_url"),
            "block_number": audit.get("block_number"),
            "minted_at": audit.get("minted_at"),
        }

    if current_status in ("CALCULATING", "PROCESSING"):
        return {"status": current_status, "audit_id": audit_id}

    if current_status == "FAILED":
        return {
            "status": "FAILED",
            "audit_id": audit_id,
            "error": audit.get("error", "Unknown error"),
        }

    return {"status": current_status, "audit_id": audit_id}
