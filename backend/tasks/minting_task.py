"""
Minting Celery task — mints carbon credits on Polygon and updates
the audit record with the transaction hash.
"""

import asyncio
import logging
from datetime import datetime, timezone

from tasks.celery_app import celery_app
from app.database import supabase_client
from services import fusion_engine, minting_service

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

        land_resp = (
            supabase_client.table("land_parcels")
            .select("*")
            .eq("id", land_id)
            .single()
            .execute()
        )
        land_data = land_resp.data

        scans_resp = (
            supabase_client.table("ar_tree_scans")
            .select("*")
            .eq("audit_id", audit_id)
            .execute()
        )
        tree_scans = scans_resp.data or []

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
            "current_biomass": audit_data.get("total_biomass_tonnes", 0),
            "satellite_features": audit_data.get("satellite_features", {}),
        }

        metadata = minting_service.build_audit_metadata(
            audit_data={
                "land_id": land_id,
                "survey_number": land_data.get("survey_number"),
                "district": land_data.get("district"),
                "audit_year": audit_year,
            },
            tree_scans=tree_scans,
            credit_result=credit_result,
        )

        # --- Mint on-chain --------------------------------------------------
        # minting_service.mint_carbon_credits is async; run it in an event loop
        loop = asyncio.new_event_loop()
        try:
            mint_result = loop.run_until_complete(
                minting_service.mint_carbon_credits(
                    farmer_address=farmer_address,
                    audit_id_int=audit_data.get("audit_id_int", hash(audit_id) % (10**8)),
                    credit_amount=audit_data.get("credits_issued", 0),
                    metadata=metadata,
                    land_id=land_id,
                    audit_year=audit_year,
                )
            )
        finally:
            loop.close()

        # --- Update audit record --------------------------------------------
        supabase_client.table("carbon_audits").update(
            {
                "status": "MINTED",
                "tx_hash": mint_result["tx_hash"],
                "ipfs_url": mint_result["ipfs_url"],
                "block_number": mint_result["block_number"],
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
