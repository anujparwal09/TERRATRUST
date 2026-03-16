"""
Pydantic schemas for user / KYC data.
"""

from pydantic import BaseModel, Field


class KYCRequest(BaseModel):
    """Request body for KYC verification."""

    full_name: str = Field(..., min_length=2, max_length=200, description="Legal full name")
    aadhaar_number: str = Field(
        ...,
        min_length=12,
        max_length=12,
        pattern=r"^\d{12}$",
        description="12-digit Aadhaar number",
    )


class UserResponse(BaseModel):
    """Public user representation."""

    id: str
    phone: str | None = None
    wallet_address: str | None = None
    kyc_completed: bool = False
