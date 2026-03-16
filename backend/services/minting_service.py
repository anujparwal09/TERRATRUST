"""
Minting service — build audit evidence metadata and mint carbon
credits as ERC-1155 tokens on Polygon via the TerraToken contract.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from eth_account import Account
from web3 import Web3

from app.config import settings
from services.ipfs_service import upload_to_ipfs

logger = logging.getLogger("terratrust.minting")

# ---------------------------------------------------------------------------
# Contract ABI path
# ---------------------------------------------------------------------------
ABI_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "contracts",
    "artifacts",
    "TerraToken_ABI.json",
)


def _load_abi() -> list:
    """Load the TerraToken ABI from the artifacts directory."""
    if not os.path.exists(ABI_PATH):
        raise FileNotFoundError(
            f"Contract ABI not found at {ABI_PATH}. "
            "Deploy the contract first and copy the ABI."
        )
    with open(ABI_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Build evidence metadata
# ---------------------------------------------------------------------------
def build_audit_metadata(
    audit_data: Dict[str, Any],
    tree_scans: List[Dict[str, Any]],
    credit_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble the complete on-chain evidence package.

    This metadata is pinned to IPFS and referenced in the NFT
    certificate minted alongside the fungible credit tokens.

    Parameters
    ----------
    audit_data : dict
        Core audit record (land_id, survey_number, district, etc.).
    tree_scans : list[dict]
        AR tree scan records.
    credit_result : dict
        Output of ``fusion_engine.calculate_credits()``.

    Returns
    -------
    dict
        Full evidence metadata ready for IPFS pinning.
    """
    return {
        "land_id": audit_data.get("land_id"),
        "survey_number": audit_data.get("survey_number"),
        "district": audit_data.get("district"),
        "audit_year": audit_data.get("audit_year"),
        "measurement_date": datetime.now(timezone.utc).isoformat(),
        "total_biomass_tonnes": credit_result.get("current_biomass", 0),
        "credits_issued": credit_result.get("credits_issued", 0),
        "satellite_data": credit_result.get("satellite_features", {
            "s1_vh_mean": 0,
            "ndvi_mean": 0,
            "gedi_height_mean": 0,
        }),
        "tree_samples": [
            {
                "species": t.get("species"),
                "dbh_cm": t.get("dbh_cm"),
                "height_m": t.get("height_m"),
                "gps": t.get("gps"),
                "photo_hash": t.get("evidence_photo_hash"),
            }
            for t in tree_scans
        ],
        "calculation_method": "S1_S2_GEDI_SRTM_XGBoost_v3",
    }


# ---------------------------------------------------------------------------
# Mint on Polygon
# ---------------------------------------------------------------------------
async def mint_carbon_credits(
    farmer_address: str,
    audit_id_int: int,
    credit_amount: float,
    metadata: Dict[str, Any],
    land_id: str,
    audit_year: int,
) -> Dict[str, Any]:
    """Mint carbon credits and an NFT certificate on Polygon.

    Steps
    -----
    1. Upload evidence metadata to IPFS via Pinata.
    2. Connect to Polygon via Alchemy RPC.
    3. Call ``TerraToken.mintAudit()`` with admin account.
    4. Wait for the transaction receipt.

    Parameters
    ----------
    farmer_address : str
        Farmer's Polygon wallet address.
    audit_id_int : int
        Numeric audit ID for the ERC-1155 NFT token id.
    credit_amount : float
        Number of carbon credit tokens to mint (no decimals).
    metadata : dict
        Full audit evidence metadata.
    land_id : str
        UUID of the land parcel (used for double-mint key).
    audit_year : int
        Calendar year of the audit.

    Returns
    -------
    dict
        ``{tx_hash, ipfs_url, block_number}``
    """
    # 1. Pin metadata to IPFS
    ipfs_url = await upload_to_ipfs(metadata, audit_id=str(audit_id_int))

    # 2. Connect to Polygon
    alchemy_url = settings.ALCHEMY_POLYGON_AMOY_URL or settings.ALCHEMY_POLYGON_MAINNET_URL
    w3 = Web3(Web3.HTTPProvider(alchemy_url))
    if not w3.is_connected():
        raise ConnectionError("Cannot connect to Polygon RPC.")

    # 3. Prepare contract
    abi = _load_abi()
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(settings.CONTRACT_ADDRESS),
        abi=abi,
    )

    admin_account = Account.from_key(settings.ADMIN_WALLET_PRIVATE_KEY)
    nonce = w3.eth.get_transaction_count(admin_account.address)

    # 4. Build the transaction
    credit_amount_int = int(credit_amount)
    tx = contract.functions.mintAudit(
        Web3.to_checksum_address(farmer_address),
        audit_id_int,
        credit_amount_int,
        land_id,
        audit_year,
        ipfs_url,
    ).build_transaction(
        {
            "from": admin_account.address,
            "nonce": nonce,
            "gas": 500_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": w3.eth.chain_id,
        }
    )

    # 5. Sign + send
    signed = admin_account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

    # 6. Wait for receipt
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    result = {
        "tx_hash": receipt.transactionHash.hex(),
        "ipfs_url": ipfs_url,
        "block_number": receipt.blockNumber,
    }
    logger.info("Minted %d credits → tx %s", credit_amount_int, result["tx_hash"])
    return result
