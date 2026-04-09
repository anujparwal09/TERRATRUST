"""Audit router aligned to the documented audit and history API contracts."""

import base64
import hashlib
import logging
from collections import Counter
from datetime import datetime, timezone
import math
from typing import Any, Dict, List
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shapely.geometry import Point, shape

from app.database import (
    fetch_land_parcel_record,
    insert_sampling_zone_records,
    insert_tree_scan_record,
    list_sampling_zones_for_audit,
    supabase_client,
)
from app.dependencies import get_current_user
from models.audit import (
    AuditHistoryItem,
    AuditHistoryResponse,
    AuditSubmitRequest,
    AuditSubmitResponse,
    AuditZonesResponse,
    ZoneResponse,
)
from services import zone_generation_service
from services.fusion_engine import SPECIES_WOOD_DENSITY
from services.ipfs_service import to_gateway_url

logger = logging.getLogger("terratrust.audit")

router = APIRouter()

ALLOWED_SPECIES = {species.casefold(): species for species in SPECIES_WOOD_DENSITY}


def _normalise_species_name(species: str) -> str:
    """Return the canonical supported species name or fail clearly."""
    canonical = ALLOWED_SPECIES.get(species.strip().casefold())
    if canonical:
        return canonical

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
            "Unsupported tree species. Allowed values are: "
            + ", ".join(sorted(SPECIES_WOOD_DENSITY))
            + "."
        ),
    )


def _decode_evidence_photo(photo_base64: str, expected_hash: str) -> bytes:
    """Decode and verify an evidence photo payload."""
    try:
        photo_bytes = base64.b64decode(photo_base64, validate=True)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Evidence photo must be valid base64-encoded image data.",
        ) from exc

    if not photo_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Evidence photo is empty.",
        )

    computed_hash = hashlib.sha256(photo_bytes).hexdigest()
    if computed_hash.lower() != expected_hash.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Evidence photo hash does not match the uploaded photo bytes.",
        )

    return photo_bytes


def _require_point_within_boundary(lat: float, lng: float, boundary_geojson: Dict[str, Any]) -> None:
    """Ensure a tree point lies within the registered parcel boundary."""
    try:
        boundary_geometry = shape(boundary_geojson)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registered land parcel has invalid boundary geometry.",
        ) from exc

    if not boundary_geometry.covers(Point(lng, lat)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tree sample GPS point lies outside the registered land boundary.",
        )


def _distance_metres(first_lat: float, first_lng: float, second_lat: float, second_lng: float) -> float:
    """Approximate straight-line distance between two GPS points."""
    dlat = (second_lat - first_lat) * 111_320
    dlng = (second_lng - first_lng) * 111_320 * math.cos(
        math.radians((first_lat + second_lat) / 2)
    )
    return math.sqrt(dlat**2 + dlng**2)


def _to_utc_iso(timestamp: datetime) -> str:
    """Normalize a timestamp to UTC ISO-8601 for persistence."""
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# GET /zones
# ---------------------------------------------------------------------------
@router.get("/zones", response_model=AuditZonesResponse)
async def get_audit_zones(
    land_id: str = Query(..., description="UUID of the registered land parcel"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Generate NDVI-stratified sampling zones for a land parcel.

    Creates a new audit record in Supabase with status ``PROCESSING``
    and returns the list of zones with walking path estimate.
    """
    try:
        land_data = await fetch_land_parcel_record(land_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Land parcel '{land_id}' not found.",
        ) from exc
    except Exception as exc:
        logger.error("Failed to load land parcel %s for zone generation: %s", land_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load land parcel data.",
        ) from exc

    # Ownership check
    if land_data.get("user_id") != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this land parcel.",
        )

    boundary_geojson = land_data.get("boundary_geojson")
    if not boundary_geojson:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registered land parcel is missing boundary geometry.",
        )

    audit_year = datetime.now(timezone.utc).year
    existing_audit_resp = (
        supabase_client.table("carbon_audits")
        .select("id, status")
        .eq("land_id", land_id)
        .eq("audit_year", audit_year)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    existing_audit = (existing_audit_resp.data or [None])[0]
    audit_id = str(uuid.uuid4())

    if existing_audit:
        existing_status = existing_audit.get("status")
        if existing_status in {"MINTED", "COMPLETE_NO_CREDITS"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A carbon audit for {audit_year} has already been completed for this land.",
            )

        if existing_status in {"PROCESSING", "CALCULATING", "READY_TO_MINT"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This land already has an audit in progress for the current year.",
            )

        audit_id = existing_audit["id"]
        supabase_client.table("sampling_zones").delete().eq("audit_id", audit_id).execute()
        supabase_client.table("carbon_audits").update(
            {
                "status": "PROCESSING",
                "error": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", audit_id).execute()

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

    # Create or refresh the audit record
    walking_path = zones[0].get("walking_path_metres", 0) if zones else 0

    if not existing_audit:
        supabase_client.table("carbon_audits").insert(
            {
                "id": audit_id,
                "land_id": land_id,
                "user_id": current_user["id"],
                "audit_year": audit_year,
                "status": "PROCESSING",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute()

    await insert_sampling_zone_records(land_id=land_id, audit_id=audit_id, zones=zones)

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
        min_trees_required=len(zone_responses) * 3,
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
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Accept tree-scan samples and kick off the fusion pipeline.

    Validations
    -----------
    - Minimum **9** trees total.
    - At least **3** trees per zone.
    - Each tree must have valid photo data.
    """
    trees = body.trees

    try:
        audit_resp = (
            supabase_client.table("carbon_audits")
            .select("id, user_id, land_id, status")
            .eq("id", body.audit_id)
            .single()
            .execute()
        )
        audit_data = audit_resp.data
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audit '{body.audit_id}' not found.",
        ) from exc

    if audit_data.get("user_id") != current_user["id"] or audit_data.get("land_id") != body.land_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this audit session.",
        )

    if audit_data.get("status") != "PROCESSING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tree samples can only be submitted while the audit is in PROCESSING state.",
        )

    try:
        land_data = await fetch_land_parcel_record(body.land_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Land parcel '{body.land_id}' not found.",
        ) from exc
    except Exception as exc:
        logger.error("Failed to load land parcel %s for audit submission: %s", body.land_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load land parcel data.",
        ) from exc

    boundary_geojson = land_data.get("boundary_geojson")
    if not boundary_geojson:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registered land parcel is missing boundary geometry.",
        )

    # --- Minimum total check -----------------------------------------------
    if len(trees) < 9:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At least 9 tree samples are required. Received {len(trees)}.",
        )

    # --- Species and per-zone checks ---------------------------------------
    zone_counts: Dict[str, int] = Counter(t.zone_id for t in trees)
    canonical_species: Dict[int, str] = {}
    for index, tree in enumerate(trees):
        if tree.species_confidence < 0.80:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Tree sample in zone '{tree.zone_id}' has species confidence "
                    f"{tree.species_confidence:.2f}. Minimum allowed is 0.80."
                ),
            )
        canonical_species[index] = _normalise_species_name(tree.species)

    for zone_id, count in zone_counts.items():
        if count < 3:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Zone '{zone_id}' has only {count} tree(s). "
                    "Minimum 3 per zone."
                ),
            )
        if count > 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Zone '{zone_id}' has {count} tree samples. "
                    "Maximum 5 per zone."
                ),
            )

    # --- GPS accuracy check (≤ 30 m) --------------------------------------
    for tree in trees:
        if tree.gps_accuracy_m > 30:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Tree sample in zone '{tree.zone_id}' has GPS accuracy "
                    f"{tree.gps_accuracy_m:.1f} m. Maximum allowed is 30 m."
                ),
            )

    # --- Ensure submitted zones belong to the audit -----------------------
    try:
        sampling_zones = await list_sampling_zones_for_audit(body.audit_id)
        zone_map = {zone["id"]: zone for zone in sampling_zones}
        valid_zone_ids = set(zone_map)
        if not valid_zone_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No sampling zones were found for this audit session.",
            )

        invalid_zone_ids = sorted(
            {tree.zone_id for tree in trees if valid_zone_ids and tree.zone_id not in valid_zone_ids}
        )
        if invalid_zone_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Submitted tree samples contain zone IDs that do not belong "
                    f"to audit '{body.audit_id}': {', '.join(invalid_zone_ids)}."
                ),
            )

        missing_zone_ids = sorted(valid_zone_ids - set(zone_counts))
        if missing_zone_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "At least 3 trees must be submitted for every sampling zone. Missing zone IDs: "
                    + ", ".join(missing_zone_ids)
                    + "."
                ),
            )

        for tree in trees:
            zone = zone_map[tree.zone_id]
            centre = zone["centre_gps"]
            radius_metres = float(zone["radius_metres"])
            distance_metres = _distance_metres(
                tree.gps.lat,
                tree.gps.lng,
                float(centre["lat"]),
                float(centre["lng"]),
            )
            if distance_metres > radius_metres:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Tree sample for zone '{tree.zone_id}' lies {distance_metres:.1f} m from "
                        f"the zone centre. Maximum allowed is {radius_metres:.1f} m."
                    ),
                )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to validate sampling zones for this audit.",
        ) from exc

    # --- Process each tree ------------------------------------------------
    for index, tree in enumerate(trees):
        species_name = canonical_species[index]
        wood_density = SPECIES_WOOD_DENSITY[species_name]
        _require_point_within_boundary(tree.gps.lat, tree.gps.lng, boundary_geojson)
        photo_bytes = _decode_evidence_photo(
            tree.evidence_photo_base64,
            tree.evidence_photo_hash,
        )

        photo_storage_path = f"audit-photos/{body.audit_id}/{tree.zone_id}/{uuid.uuid4()}.jpg"
        try:
            supabase_client.storage.from_("evidence-photos").upload(
                photo_storage_path, photo_bytes
            )
        except Exception as exc:
            logger.error("Photo upload failed for audit %s: %s", body.audit_id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to store tree evidence photo.",
            ) from exc

        # Insert into ar_tree_scans table
        scan_record = {
            "id": str(uuid.uuid4()),
            "audit_id": body.audit_id,
            "land_id": body.land_id,
            "zone_id": tree.zone_id,
            "species": species_name,
            "species_confidence": tree.species_confidence,
            "dbh_cm": tree.dbh_cm,
            "height_m": tree.height_m,
            "gps": {"lat": tree.gps.lat, "lng": tree.gps.lng},
            "gps_accuracy_m": tree.gps_accuracy_m,
            "gedi_height_m": None,
            "height_source": "AR_FALLBACK" if tree.height_m else None,
            "ar_tier_used": tree.ar_tier_used,
            "confidence_score": tree.confidence_score,
            "evidence_photo_hash": tree.evidence_photo_hash,
            "evidence_photo_path": photo_storage_path,
            "wood_density": wood_density,
            "agb_kg": None,
            "scan_timestamp": _to_utc_iso(tree.scan_timestamp),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await insert_tree_scan_record(scan_record)

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
        message="Satellite verification in progress",
    )


# ---------------------------------------------------------------------------
# GET /result/{audit_id}
# ---------------------------------------------------------------------------
@router.get("/result/{audit_id}")
async def get_audit_result(
    audit_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
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

    if audit.get("user_id") != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this audit.",
        )

    current_status = audit.get("status", "UNKNOWN")

    if current_status == "MINTED":
        return {
            "status": "MINTED",
            "audit_id": audit_id,
            "total_biomass_tonnes": audit.get("total_biomass_tonnes"),
            "credits_issued": audit.get("credits_issued"),
            "tx_hash": audit.get("tx_hash"),
            "ipfs_certificate_url": to_gateway_url(
                audit.get("ipfs_metadata_cid") or audit.get("ipfs_url")
            ),
            "audit_year": audit.get("audit_year"),
        }

    if current_status == "COMPLETE_NO_CREDITS":
        return {
            "status": "COMPLETE_NO_CREDITS",
            "audit_id": audit_id,
            "total_biomass_tonnes": audit.get("total_biomass_tonnes"),
            "credits_issued": audit.get("credits_issued") or 0,
            "audit_year": audit.get("audit_year"),
            "reason": audit.get("reason") or "No biomass growth detected compared to the latest prior successful audit",
        }

    if current_status in ("CALCULATING", "PROCESSING", "READY_TO_MINT"):
        return {"status": "CALCULATING", "audit_id": audit_id}

    if current_status == "FAILED":
        return {
            "status": "FAILED",
            "audit_id": audit_id,
            "error": audit.get("error", "Unknown error"),
        }

    return {"status": current_status, "audit_id": audit_id}


@router.get(
    "/history/{land_id}",
    response_model=AuditHistoryResponse,
    status_code=status.HTTP_200_OK,
)
async def get_audit_history(
    land_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Return full audit history for a specific land parcel."""
    try:
        land_resp = (
            supabase_client.table("land_parcels")
            .select("id, user_id")
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

    if land_data.get("user_id") != current_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this land parcel.",
        )

    try:
        audits_resp = (
            supabase_client.table("carbon_audits")
            .select("audit_year, status, total_biomass_tonnes, credits_issued, tx_hash, ipfs_metadata_cid, ipfs_url, minted_at")
            .eq("land_id", land_id)
            .order("audit_year", desc=True)
            .execute()
        )

        items = [
            AuditHistoryItem(
                audit_year=audit.get("audit_year", 0),
                total_biomass_tonnes=audit.get("total_biomass_tonnes"),
                credits_issued=audit.get("credits_issued"),
                tx_hash=audit.get("tx_hash"),
                ipfs_certificate_url=to_gateway_url(
                    audit.get("ipfs_metadata_cid") or audit.get("ipfs_url")
                ),
                minted_at=audit.get("minted_at"),
            )
            for audit in audits_resp.data or []
            if audit.get("status") in {"MINTED", "COMPLETE_NO_CREDITS"}
        ]

        total = len(items)
        start_index = (page - 1) * limit
        return AuditHistoryResponse(
            items=items[start_index : start_index + limit],
            page=page,
            limit=limit,
            total=total,
            has_more=start_index + limit < total,
        )
    except Exception as exc:
        logger.error("Failed to fetch audit history for land %s: %s", land_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch audit history.",
        ) from exc
