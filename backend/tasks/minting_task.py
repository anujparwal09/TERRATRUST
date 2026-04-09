"""Background minting task for Polygon settlement and audit updates."""

import asyncio
import logging
from datetime import datetime, timezone
import uuid

from tasks.celery_app import celery_app
from app.database import fetch_land_parcel_record, list_tree_scans_for_audit, supabase_client
from services import minting_service

logger = logging.getLogger("terratrust.tasks.minting")


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def run_minting(self, audit_id: str, land_id: str, audit_year: int) -> dict:
    """Mint carbon credits on-chain for a completed audit.

    Workflow
    --------
    1. Fetch audit + land + tree-scan data.
    2. Build the evidence metadata.
    3. Call ``minting_service.mint_carbon_credits()``.
    4. Update the audit record with tx_hash and IPFS URL.

    Retries up to 2 times on failure; marks audit ``FAILED`` if
    all retries are exhausted.
    """
    try:
        # --- Fetch data -----------------------------------------------------
        audit_resp = (
            supabase_client.table("carbon_audits")
            .select("*")
            .eq("id", audit_id)
            .single()
            .execute()
        )
        audit_data = audit_resp.data

        if float(audit_data.get("credits_issued") or 0) <= 0:
            supabase_client.table("carbon_audits").update(
                {
                    "status": "COMPLETE_NO_CREDITS",
                    "reason": audit_data.get("reason") or "No eligible credits were generated for this audit.",
                }
            ).eq("id", audit_id).execute()
            return {"audit_id": audit_id, "status": "COMPLETE_NO_CREDITS"}

        land_data = asyncio.run(fetch_land_parcel_record(land_id))
        tree_scans = asyncio.run(list_tree_scans_for_audit(audit_id))

        # Fetch user wallet address
        user_resp = (
            supabase_client.table("users")
            .select("wallet_address")
            .eq("id", audit_data["user_id"])
            .single()
            .execute()
        )
        farmer_address = user_resp.data.get("wallet_address")
        if not farmer_address:
            raise ValueError(
                f"User {audit_data['user_id']} does not have a wallet address."
            )

        # --- Build metadata -------------------------------------------------
        credit_result = {
            "credits_issued": audit_data.get("credits_issued", 0),
            "prev_year_biomass": audit_data.get("prev_year_biomass", 0),
            "current_biomass": audit_data.get("total_biomass_tonnes", 0),
            "delta_biomass": audit_data.get("delta_biomass", 0),
            "carbon_tonnes": audit_data.get("carbon_tonnes", 0),
            "co2_equivalent": audit_data.get("co2_equivalent", 0),
            "satellite_features": audit_data.get("satellite_features", {}),
        }

        metadata = minting_service.build_audit_metadata(
            audit_data={
                "land_id": land_id,
                "survey_number": land_data.get("survey_number"),
                "district": land_data.get("district"),
                "taluka": land_data.get("taluka"),
                "village": land_data.get("village"),
                "boundary_source": land_data.get("boundary_source"),
                "boundary_geojson": land_data.get("boundary_geojson") or land_data.get("geojson"),
                "audit_year": audit_year,
            },
            tree_scans=tree_scans,
            credit_result=credit_result,
        )

        # --- Mint on-chain --------------------------------------------------
        mint_result = asyncio.run(
            minting_service.mint_carbon_credits(
                farmer_address=farmer_address,
                audit_id_int=uuid.UUID(audit_id).int,
                credit_amount=audit_data.get("credits_issued", 0),
                metadata=metadata,
                land_id=land_id,
                audit_year=audit_year,
            )
        )

        # --- Update audit record --------------------------------------------
        ipfs_uri = mint_result["ipfs_url"]
        supabase_client.table("carbon_audits").update(
            {
                "status": "MINTED",
                "tx_hash": mint_result["tx_hash"],
                "ipfs_metadata_cid": ipfs_uri.removeprefix("ipfs://"),
                "ipfs_url": mint_result["ipfs_url"],
                "block_number": mint_result["block_number"],
                "token_id": uuid.UUID(audit_id).int,
                "minted_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", audit_id).execute()

        logger.info(
            "Minting complete for audit %s — tx=%s",
            audit_id,
            mint_result["tx_hash"],
        )
        return {"audit_id": audit_id, **mint_result}

    except Exception as exc:
        logger.error("Minting task failed for audit %s: %s", audit_id, exc)
        if self.request.retries >= self.max_retries:
            supabase_client.table("carbon_audits").update(
                {"status": "FAILED", "error": str(exc)[:500]}
            ).eq("id", audit_id).execute()
            raise
        raise self.retry(exc=exc)
