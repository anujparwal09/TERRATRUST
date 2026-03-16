"""
Pydantic schemas for land parcels, document OCR, and boundary data.
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class DocumentUploadResponse(BaseModel):
    """Response returned after OCR extraction from a land document."""

    survey_number: str
    owner_name: str
    village: Optional[str] = None
    taluka: Optional[str] = None
    district: Optional[str] = None
    state: Optional[str] = None
    extraction_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Fraction of fields successfully extracted"
    )


class BoundaryFetchResponse(BaseModel):
    """Response for a boundary-fetch attempt."""

    status: str = Field(..., description="'success' or 'manual_required'")
    boundary_source: Optional[str] = None
    geojson: Optional[Dict[str, Any]] = None
    area_hectares: Optional[float] = None


class LandRegisterRequest(BaseModel):
    """Request body to register a verified land parcel."""

    farm_name: str = Field(..., min_length=1, max_length=300)
    survey_number: str
    district: str
    taluka: str
    village: str
    state: str
    boundary_source: str = Field(
        ..., description="How boundary was obtained: WMS_AUTO | SCRAPE | MANUAL"
    )
    geojson: Dict[str, Any] = Field(..., description="GeoJSON geometry of the parcel boundary")
    ocr_owner_name: str = Field(..., description="Owner name as extracted by OCR")


class LandRegisterResponse(BaseModel):
    """Response after successful land registration."""

    land_id: str
    area_hectares: float
    status: str = "verified"
