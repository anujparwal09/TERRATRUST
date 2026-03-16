"""
Fusion Celery task — runs the data fusion + credit calculation
pipeline in the background and triggers minting if credits > 0.
"""

import logging
from datetime import datetime, timezone

from tasks.celery_app import celery_app
from app.database import supabase_client
from services import fusion_engine

logger = logging.getLogger("terratrust.tasks.fusion")


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def run_audit_fusion(self, audit_id: str) -> dict:
    """Execute the full fusion pipeline for a single audit.

    Workflow
    --------
    1. Mark audit status → ``CALCULATING``.
    2. Fetch audit + land data from Supabase.
    3. Run ``fusion_engine.run_fusion()``.
    4. Run ``fusion_engine.calculate_credits()``.
    5. Persist results in ``carbon_audits``.
    6. If credits > 0 → trigger ``minting_task``.

    On exception the task retries up to 2 times with a 30 s delay,
    then marks the audit ``FAILED``.
    """
    try:
        # --- 1. Update status -----------------------------------------------
        supabase_client.table("carbon_audits").update(
            {"status": "CALCULATING"}
        ).eq("id", audit_id).execute()

        # --- 2. Fetch audit data --------------------------------------------
        audit_resp = (
            supabase_client.table("carbon_audits")
            .select("*")
            .eq("id", audit_id)
            .single()
            .execute()
        )
        audit_data = audit_resp.data

        land_id = audit_data["land_id"]
        audit_year = audit_data.get("audit_year", datetime.now(timezone.utc).year)

        # Fetch land boundary
        land_resp = (
            supabase_client.table("land_parcels")
            .select("geojson, survey_number, district")
            .eq("id", land_id)
            .single()
            .execute()
        )
        land_data = land_resp.data
        boundary_geojson = land_data["geojson"]

        # Fetch tree scans
        scans_resp = (
            supabase_client.table("ar_tree_scans")
            .select("*")
            .eq("audit_id", audit_id)
            .execute()
        )
        tree_scans = scans_resp.data or []

        # --- 3. Run fusion --------------------------------------------------
        fusion_result = fusion_engine.run_fusion(
            audit_id=audit_id,
            land_id=land_id,
            tree_scans=tree_scans,
            land_boundary_geojson=boundary_geojson,
            audit_year=audit_year,
        )

        # --- 4. Calculate credits -------------------------------------------
        credit_result = fusion_engine.calculate_credits(
            total_biomass_tonnes=fusion_result["total_biomass_tonnes"],
            land_id=land_id,
            audit_year=audit_year,
        )

        # --- 5. Persist results ---------------------------------------------
        update_payload = {
            "total_biomass_tonnes": fusion_result["total_biomass_tonnes"],
            "credits_issued": credit_result["credits_issued"],
            "delta_biomass": credit_result["delta_biomass"],
            "carbon_tonnes": credit_result["carbon_tonnes"],
            "co2_equivalent": credit_result["co2_equivalent"],
            "satellite_features": fusion_result.get("satellite_features"),
            "status": "CALCULATED",
            "calculated_at": datetime.now(timezone.utc).isoformat(),
        }

        supabase_client.table("carbon_audits").update(
            update_payload
        ).eq("id", audit_id).execute()

        # --- 6. Trigger minting if credits > 0 -----------------------------
        if credit_result["credits_issued"] > 0:
            from tasks.minting_task import run_minting

            run_minting.delay(
                audit_id=audit_id,
                land_id=land_id,
                audit_year=audit_year,
            )
            logger.info(
                "Fusion done for audit %s — %.2f credits, minting enqueued.",
                audit_id,
                credit_result["credits_issued"],
            )
        else:
            # No credits — mark as MINTED (nothing to mint)
            supabase_client.table("carbon_audits").update(
                {"status": "MINTED"}
            ).eq("id", audit_id).execute()
            logger.info(
                "Fusion done for audit %s — 0 credits (no biomass increase).",
                audit_id,
            )

        return {
            "audit_id": audit_id,
            "credits_issued": credit_result["credits_issued"],
        }

    except Exception as exc:
        logger.error("Fusion task failed for audit %s: %s", audit_id, exc)
        # Mark as FAILED on final retry
        if self.request.retries >= self.max_retries:
            supabase_client.table("carbon_audits").update(
                {"status": "FAILED", "error": str(exc)[:500]}
            ).eq("id", audit_id).execute()
            raise
        # Retry
        raise self.retry(exc=exc)
