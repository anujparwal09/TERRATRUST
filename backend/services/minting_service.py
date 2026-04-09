"""Minting service for Pinata metadata packaging and Polygon minting."""

import hashlib
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
# Contract ABI paths
# ---------------------------------------------------------------------------
ABI_FILENAMES = (
    "TerraTrustToken_ABI.json",
    "TerraToken_ABI.json",
)


def _coerce_credit_amount(credit_amount: float) -> int:
    """Convert a precise credit quantity into whole ERC-1155 token units."""
    try:
        precise_amount = float(credit_amount)
    except (TypeError, ValueError) as exc:
        raise ValueError("credit_amount must be numeric.") from exc

    coerced_amount = max(0, int(round(precise_amount)))
    if abs(precise_amount - coerced_amount) > 1e-9:
        logger.warning(
            "Coercing calculated credits %.4f to %d whole CTT for ERC-1155 minting.",
            precise_amount,
            coerced_amount,
        )
    return coerced_amount


def _resolve_abi_path() -> str:
    """Return the first available ABI path for the deployed token contract."""
    artifacts_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "contracts",
        "artifacts",
    )
    candidate_paths = [os.path.join(artifacts_dir, filename) for filename in ABI_FILENAMES]

    for candidate in candidate_paths:
        if os.path.exists(candidate):
            return candidate

    expected_paths = ", ".join(candidate_paths)
    raise FileNotFoundError(
        "Contract ABI not found. Expected one of: " + expected_paths
    )


def _load_abi() -> list:
    """Load the TerraTrust token ABI from the artifacts directory."""
    abi_path = _resolve_abi_path()
    with open(abi_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _require_setting(name: str, value: str) -> str:
    """Require a non-empty runtime setting value."""
    if not value:
        raise RuntimeError(f"{name} is not configured.")
    return value


def _get_polygon_client() -> tuple[Web3, Any]:
    """Return the configured Web3 client and admin account."""
    rpc_url = _require_setting(
        "ALCHEMY_POLYGON_AMOY_URL",
        settings.ALCHEMY_POLYGON_AMOY_URL,
    )
    contract_address = _require_setting("CONTRACT_ADDRESS", settings.CONTRACT_ADDRESS)
    private_key = _require_setting("ADMIN_WALLET_PRIVATE_KEY", settings.ADMIN_WALLET_PRIVATE_KEY)

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise ConnectionError("Cannot connect to Polygon RPC.")
    if not Web3.is_address(contract_address):
        raise RuntimeError("CONTRACT_ADDRESS is not a valid EVM address.")

    return w3, Account.from_key(private_key)


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
    boundary_geojson = audit_data.get("boundary_geojson") or {}
    boundary_hash = None
    if boundary_geojson:
        boundary_hash = hashlib.sha256(
            json.dumps(boundary_geojson, sort_keys=True).encode("utf-8")
        ).hexdigest()

    satellite_data = credit_result.get("satellite_features", {})
    return {
        "name": (
            f"TerraTrust Audit - Farm {audit_data.get('land_id')}, "
            f"Year {audit_data.get('audit_year')}"
        ),
        "land_id": audit_data.get("land_id"),
        "survey_number": audit_data.get("survey_number"),
        "district": audit_data.get("district"),
        "taluka": audit_data.get("taluka"),
        "village": audit_data.get("village"),
        "land_boundary_hash": boundary_hash,
        "audit_year": audit_data.get("audit_year"),
        "measurement_date": datetime.now(timezone.utc).date().isoformat(),
        "total_biomass_tonnes": credit_result.get("current_biomass", 0),
        "prev_biomass_tonnes": credit_result.get("prev_year_biomass", 0),
        "delta_biomass": credit_result.get("delta_biomass", 0),
        "carbon_tonnes": credit_result.get("carbon_tonnes", 0),
        "co2_equivalent": credit_result.get("co2_equivalent", 0),
        "credits_issued": credit_result.get("credits_issued", 0),
        "satellite_data": {
            "sentinel1_vh_mean_db": satellite_data.get("s1_vh_mean_db", 0),
            "sentinel1_vv_mean_db": satellite_data.get("s1_vv_mean_db", 0),
            "sentinel1_vh_vv_ratio_mean": satellite_data.get("s1_vh_vv_ratio_mean", 0),
            "sentinel2_ndvi_mean": satellite_data.get("s2_ndvi_mean", 0),
            "sentinel2_evi_mean": satellite_data.get("s2_evi_mean", 0),
            "sentinel2_red_edge_mean": satellite_data.get("s2_red_edge_mean", 0),
            "gedi_height_mean": satellite_data.get("gedi_height_mean", 0),
            "srtm_elevation_mean": satellite_data.get("srtm_elevation_mean", 0),
            "srtm_slope_mean": satellite_data.get("srtm_slope_mean", 0),
            "nisar_used": satellite_data.get("nisar_used", False),
            "features_count": satellite_data.get("features_count", 0),
            "processing_method": satellite_data.get("processing_method", "S1_S2_GEDI_SRTM_XGBoost_v3.1"),
        },
        "tree_samples": [
            {
                "species": t.get("species"),
                "dbh_cm": t.get("dbh_cm"),
                "height_source": t.get("height_source") or ("GEDI" if t.get("gedi_height_m") else "AR_FALLBACK"),
                "height_m": t.get("gedi_height_m") or t.get("height_m"),
                "agb_kg": t.get("agb_kg"),
                "scan_timestamp": t.get("scan_timestamp"),
                "measurement_tier": t.get("ar_tier_used"),
                "gps": t.get("gps"),
                "evidence_photo_hash": t.get("evidence_photo_hash"),
            }
            for t in tree_scans
        ],
        "calculation_method": satellite_data.get("processing_method", "S1_S2_GEDI_SRTM_XGBoost_v3.1"),
        "boundary_verification_method": audit_data.get("boundary_source"),
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
    3. Call ``TerraTrustToken.mintAudit()`` with the admin account.
    4. Wait for the transaction receipt.

    Parameters
    ----------
    farmer_address : str
        Farmer's Polygon wallet address.
    audit_id_int : int
        Numeric audit ID for the ERC-1155 NFT token id.
    credit_amount : float
        Calculated carbon credit quantity. The on-chain mint uses whole-token units.
    metadata : dict
        Full audit evidence metadata.
    land_id : str
        UUID of the land parcel (used for double-mint key).
    audit_year : int
        Calendar year of the audit.

    Returns
    -------
    dict
        ``{tx_hash, ipfs_url, block_number, credit_amount}``
    """
    credit_amount_int = _coerce_credit_amount(credit_amount)

    metadata_payload = dict(metadata)
    metadata_payload["minted_credit_amount"] = credit_amount_int
    if abs(float(metadata_payload.get("credits_issued", 0) or 0) - credit_amount_int) > 1e-9:
        metadata_payload["minting_note"] = (
            "CTT are minted as whole ERC-1155 token units on-chain."
        )

    # 1. Pin metadata to IPFS
    ipfs_url = await upload_to_ipfs(metadata_payload, audit_id=str(audit_id_int))

    # 2. Connect to Polygon
    w3, admin_account = _get_polygon_client()

    # 3. Prepare contract
    abi = _load_abi()
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(settings.CONTRACT_ADDRESS),
        abi=abi,
    )

    nonce = w3.eth.get_transaction_count(admin_account.address)

    # 4. Build the transaction
    tx = contract.functions.mintAudit(
        Web3.to_checksum_address(farmer_address),
        audit_id_int,
        credit_amount_int,
        ipfs_url,
        land_id,
        audit_year,
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
    if receipt.status != 1:
        raise RuntimeError(f"Polygon mint transaction failed: {receipt.transactionHash.hex()}")

    result = {
        "tx_hash": receipt.transactionHash.hex(),
        "ipfs_url": ipfs_url,
        "block_number": receipt.blockNumber,
        "credit_amount": credit_amount_int,
    }
    logger.info("Minted %d credits → tx %s", credit_amount_int, result["tx_hash"])
    return result
