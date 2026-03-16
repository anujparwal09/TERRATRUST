"""
Pydantic schemas for blockchain / credit-related responses.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class CreditHistory(BaseModel):
    """A single carbon-credit issuance event."""

    audit_year: int
    credits_issued: float
    land_name: str
    tx_hash: Optional[str] = None
    ipfs_certificate_url: Optional[str] = None
    minted_at: Optional[str] = None


class BalanceResponse(BaseModel):
    """Wallet balance and history of credit issuances."""

    balance_ctt: float = Field(..., description="Current CTT token balance")
    history: List[CreditHistory] = []
