"""
Credits router — on-chain balance and history.

GET /api/v1/credits/balance?wallet_address=0x...
"""

import json
import logging
import os

from fastapi import APIRouter, HTTPException, Query, status
from web3 import Web3

from app.config import settings
from app.database import supabase_client
from models.blockchain import BalanceResponse, CreditHistory

logger = logging.getLogger("terratrust.credits")

router = APIRouter()

# ---------------------------------------------------------------------------
# Contract ABI
# ---------------------------------------------------------------------------
ABI_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "contracts",
    "artifacts",
    "TerraToken_ABI.json",
)

CARBON_CREDIT_TOKEN_ID = 1  # ERC-1155 token id for fungible credits


def _get_contract():
    """Lazily load and return the TerraToken contract instance."""
    alchemy_url = settings.ALCHEMY_POLYGON_AMOY_URL or settings.ALCHEMY_POLYGON_MAINNET_URL
    w3 = Web3(Web3.HTTPProvider(alchemy_url))
    if not w3.is_connected():
        raise ConnectionError("Cannot connect to Polygon RPC.")

    if not os.path.exists(ABI_PATH):
        raise FileNotFoundError(
            f"Contract ABI not found at {ABI_PATH}. Deploy the contract first."
        )
    with open(ABI_PATH, "r", encoding="utf-8") as f:
        abi = json.load(f)

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(settings.CONTRACT_ADDRESS),
        abi=abi,
    )
    return contract


# ---------------------------------------------------------------------------
# GET /balance
# ---------------------------------------------------------------------------
@router.get("/balance", response_model=BalanceResponse)
async def get_balance(
    wallet_address: str = Query(..., description="Farmer's Polygon wallet address"),
):
    """Return the on-chain CTT balance and audit history for a wallet.

    Combines the ERC-1155 ``balanceOf`` call with the Supabase
    ``carbon_audits`` table to build a unified view.
    """
    # --- On-chain balance ---------------------------------------------------
    try:
        contract = _get_contract()
        checksum = Web3.to_checksum_address(wallet_address)
        balance = contract.functions.balanceOf(checksum, CARBON_CREDIT_TOKEN_ID).call()
        balance_ctt = float(balance)
    except FileNotFoundError:
        # ABI not deployed yet — fall back to Supabase-only data
        logger.warning("Contract ABI not found; returning Supabase-only balance.")
        balance_ctt = 0.0
    except Exception as exc:
        logger.error("balanceOf call failed for %s: %s", wallet_address, exc)
        balance_ctt = 0.0

    # --- Audit history from Supabase ----------------------------------------
    history = []
    try:
        # Look up user by wallet address
        user_resp = (
            supabase_client.table("users")
            .select("id")
            .eq("wallet_address", wallet_address)
            .limit(1)
            .execute()
        )
        if user_resp.data:
            user_id = user_resp.data[0]["id"]

            audits_resp = (
                supabase_client.table("carbon_audits")
                .select(
                    "audit_year, credits_issued, land_id, tx_hash, "
                    "ipfs_url, minted_at"
                )
                .eq("user_id", user_id)
                .eq("status", "MINTED")
                .order("audit_year", desc=True)
                .execute()
            )

            for a in audits_resp.data or []:
                # Resolve land name
                land_name = ""
                try:
                    l = (
                        supabase_client.table("land_parcels")
                        .select("farm_name")
                        .eq("id", a["land_id"])
                        .single()
                        .execute()
                    )
                    land_name = l.data.get("farm_name", "")
                except Exception:
                    pass

                history.append(
                    CreditHistory(
                        audit_year=a.get("audit_year", 0),
                        credits_issued=a.get("credits_issued", 0),
                        land_name=land_name,
                        tx_hash=a.get("tx_hash"),
                        ipfs_certificate_url=a.get("ipfs_url"),
                        minted_at=a.get("minted_at"),
                    )
                )
    except Exception as exc:
        logger.error("Failed to fetch audit history: %s", exc)

    return BalanceResponse(balance_ctt=balance_ctt, history=history)
