"""Credits router for balance and history lookup through authenticated user context."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from web3 import Web3

from app.config import settings
from app.database import supabase_client
from app.dependencies import get_current_user
from models.blockchain import BalanceResponse, CreditHistory
from services.ipfs_service import to_gateway_url
from services.minting_service import load_contract_abi

logger = logging.getLogger("terratrust.credits")

router = APIRouter()

CARBON_CREDIT_TOKEN_ID = 1  # ERC-1155 token id for fungible credits


def _get_contract():
    """Lazily load and return the TerraTrust token contract instance."""
    alchemy_url = settings.ALCHEMY_POLYGON_AMOY_URL
    if not alchemy_url:
        raise RuntimeError("ALCHEMY_POLYGON_AMOY_URL is not configured.")
    if not settings.CONTRACT_ADDRESS or not Web3.is_address(settings.CONTRACT_ADDRESS):
        raise RuntimeError("CONTRACT_ADDRESS is not configured or is invalid.")

    w3 = Web3(Web3.HTTPProvider(alchemy_url))
    if not w3.is_connected():
        raise ConnectionError("Cannot connect to Polygon RPC.")

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(settings.CONTRACT_ADDRESS),
        abi=load_contract_abi(),
    )
    return contract


# ---------------------------------------------------------------------------
# GET /balance
# ---------------------------------------------------------------------------
@router.get("/balance", response_model=BalanceResponse)
def get_balance(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """Return the on-chain CTT balance and audit history for the current farmer.

    Combines the ERC-1155 ``balanceOf`` call with the Supabase
    ``carbon_audits`` table to build a unified view.
    """
    wallet_address = current_user.get("wallet_address")
    if not wallet_address:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wallet address has not been registered for this user yet.",
        )

    # --- On-chain balance ---------------------------------------------------
    try:
        contract = _get_contract()
        checksum = Web3.to_checksum_address(wallet_address)
        balance = contract.functions.balanceOf(checksum, CARBON_CREDIT_TOKEN_ID).call()
        balance_ctt = float(balance)
    except Exception as exc:
        logger.error("balanceOf call failed for %s: %s", wallet_address, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Blockchain balance is temporarily unavailable.",
        ) from exc

    # --- Audit history from Supabase ----------------------------------------
    history = []
    try:
        audits_resp = (
            supabase_client.table("carbon_audits")
            .select(
                "audit_year, status, credits_issued, land_id, tx_hash, ipfs_metadata_cid, ipfs_url, minted_at"
            )
            .eq("user_id", current_user["id"])
            .order("audit_year", desc=True)
            .execute()
        )

        for audit in audits_resp.data or []:
            if audit.get("status") not in {"MINTED", "COMPLETE_NO_CREDITS"}:
                continue

            credits_issued = float(audit.get("credits_issued") or 0)
            land_name = ""
            try:
                land_resp = (
                    supabase_client.table("land_parcels")
                    .select("farm_name")
                    .eq("id", audit["land_id"])
                    .single()
                    .execute()
                )
                land_name = land_resp.data.get("farm_name", "")
            except Exception:
                pass

            history.append(
                CreditHistory(
                    audit_year=audit.get("audit_year", 0),
                    credits_issued=credits_issued,
                    land_name=land_name,
                    tx_hash=audit.get("tx_hash"),
                    ipfs_certificate_url=to_gateway_url(
                        audit.get("ipfs_metadata_cid") or audit.get("ipfs_url")
                    ),
                    minted_at=audit.get("minted_at"),
                )
            )
    except Exception as exc:
        logger.error("Failed to fetch audit history: %s", exc)

    total = len(history)
    start_index = (page - 1) * limit
    return BalanceResponse(
        balance_ctt=balance_ctt,
        history=history[start_index : start_index + limit],
        page=page,
        limit=limit,
        total=total,
        has_more=start_index + limit < total,
    )
