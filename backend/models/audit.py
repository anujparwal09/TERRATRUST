"""
Pydantic schemas for audits, sampling zones, and tree scans.
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class GPSPoint(BaseModel):
    """A single GPS coordinate."""

    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class TreeSample(BaseModel):
    """One AR-scanned tree sample submitted by the farmer."""

    zone_id: str
    species: str
    dbh_cm: float = Field(..., gt=0, description="Diameter at breast height in cm")
    height_m: Optional[float] = Field(None, gt=0, description="Height in metres (optional)")
    gps: GPSPoint
    ar_tier_used: Literal[1, 2, 3] = Field(
        ..., description="AR measurement tier: 1=LiDAR, 2=ARCore, 3=manual"
    )
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    evidence_photo_base64: str = Field(..., description="Base64-encoded photo evidence")
    evidence_photo_hash: str = Field(..., description="SHA-256 hash of the photo bytes")


class AuditSubmitRequest(BaseModel):
    """Request body to submit tree scan samples for an audit."""

    land_id: str
    audit_id: str
    trees: List[TreeSample] = Field(..., min_length=1)


class AuditSubmitResponse(BaseModel):
    """Acknowledgement after accepting tree samples."""

    status: str
    audit_id: str
    estimated_seconds: int = 60


class ZoneResponse(BaseModel):
    """A single sampling zone returned to the mobile app."""

    zone_id: str
    label: str = Field(..., description="Human label e.g. A, B, C")
    centre_gps: GPSPoint
    radius_metres: float
    zone_type: str = Field(..., description="NDVI category: high / medium / low")
    sequence_order: int
    gedi_available: bool


class AuditZonesResponse(BaseModel):
    """Response containing all sampling zones for an audit."""

    audit_id: str
    zones: List[ZoneResponse]
    walking_path_metres: float
    min_trees_required: int = 9
