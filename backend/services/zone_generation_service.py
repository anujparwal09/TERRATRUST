"""
Zone generation service — NDVI-based sampling zone placement using GEE.

Generates 3 representative sampling zones (high / medium / low NDVI)
within a land parcel so the farmer walks a stratified path.
"""

import logging
import math
import os
import uuid
from typing import Any, Dict, List

import ee

from app.config import settings

logger = logging.getLogger("terratrust.zones")


def _ensure_gee_initialised() -> None:
    """Initialise GEE if not already done (idempotent)."""
    try:
        ee.Number(1).getInfo()
    except Exception:
        key_path = settings.GEE_SERVICE_ACCOUNT_KEY_PATH
        email = settings.GEE_SERVICE_ACCOUNT_EMAIL
        if key_path and os.path.exists(key_path):
            credentials = ee.ServiceAccountCredentials(email, key_path)
            ee.Initialize(credentials)
        else:
            raise RuntimeError(
                "GEE service-account key file not found. "
                "Cannot generate sampling zones."
            )


def generate_sampling_zones(
    land_id: str,
    boundary_geojson: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Generate 3 NDVI-stratified sampling zones inside the land boundary.

    Parameters
    ----------
    land_id : str
        UUID of the registered land parcel.
    boundary_geojson : dict
        GeoJSON geometry (``Polygon`` or ``MultiPolygon``).

    Returns
    -------
    list[dict]
        Each dict contains: zone_id, label, centre_gps,
        radius_metres, zone_type, sequence_order, gedi_available.
    """
    _ensure_gee_initialised()

    # --- Build EE geometry -------------------------------------------------
    coords = boundary_geojson.get("coordinates", [])
    geom_type = boundary_geojson.get("type", "Polygon")
    if geom_type == "Polygon":
        region = ee.Geometry.Polygon(coords)
    elif geom_type == "MultiPolygon":
        region = ee.Geometry.MultiPolygon(coords)
    else:
        raise ValueError(f"Unsupported geometry type: {geom_type}")

    # --- Fetch Sentinel-2 median composite (last 6 months) ----------------
    from datetime import datetime, timedelta

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=180)

    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
        .median()
    )

    # --- NDVI ---------------------------------------------------------------
    ndvi = s2.normalizedDifference(["B8", "B4"]).rename("NDVI")

    # --- Get NDVI percentiles (25th, 50th, 75th) --------------------------
    percentiles = ndvi.reduceRegion(
        reducer=ee.Reducer.percentile([25, 50, 75]),
        geometry=region,
        scale=10,
        maxPixels=1e8,
    ).getInfo()

    p25 = percentiles.get("NDVI_p25", 0.2)
    p50 = percentiles.get("NDVI_p50", 0.4)
    p75 = percentiles.get("NDVI_p75", 0.6)

    # --- Identify zone centroids ------------------------------------------
    def _zone_centroid(ndvi_img: ee.Image, low: float, high: float) -> Dict[str, float]:
        """Mask NDVI to [low, high) and return centroid coordinates."""
        mask = ndvi_img.gte(low).And(ndvi_img.lt(high))
        masked = ndvi_img.updateMask(mask)
        centroid = masked.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=region,
            scale=10,
            maxPixels=1e8,
        )
        # Use geometry centroid of the masked area as the zone centre
        zone_geom = mask.selfMask().reduceToVectors(
            geometry=region, scale=10, maxPixels=1e8, bestEffort=True
        )
        try:
            centre = zone_geom.geometry().centroid(1).coordinates().getInfo()
            return {"lng": centre[0], "lat": centre[1]}
        except Exception:
            # Fallback: use overall region centroid
            c = region.centroid(1).coordinates().getInfo()
            return {"lng": c[0], "lat": c[1]}

    zone_defs = [
        {"label": "A", "low": p75, "high": 1.0, "zone_type": "high"},
        {"label": "B", "low": p25, "high": p75, "zone_type": "medium"},
        {"label": "C", "low": -1.0, "high": p25, "zone_type": "low"},
    ]

    # --- Determine radius based on area -----------------------------------
    area_ha = region.area(1).getInfo() / 10_000  # m² → ha
    if area_ha <= 0.4:
        radius_m = 7.0
    elif area_ha <= 1.2:
        radius_m = 9.0
    else:
        radius_m = 11.0

    # --- Check GEDI availability ------------------------------------------
    gedi_available = False
    try:
        gedi = (
            ee.ImageCollection("LARSE/GEDI/GEDI02_A_002_MONTHLY")
            .filterBounds(region)
            .select(["rh98"])
            .mean()
        )
        gedi_stats = gedi.reduceRegion(
            reducer=ee.Reducer.count(),
            geometry=region,
            scale=25,
            maxPixels=1e8,
        ).getInfo()
        gedi_available = (gedi_stats.get("rh98", 0) or 0) > 0
    except Exception:
        logger.warning("GEDI availability check failed — assuming unavailable.")

    # --- Assemble zones ---------------------------------------------------
    zones: List[Dict[str, Any]] = []
    for idx, zd in enumerate(zone_defs):
        try:
            centre = _zone_centroid(ndvi, zd["low"], zd["high"])
        except Exception:
            c = region.centroid(1).coordinates().getInfo()
            centre = {"lng": c[0], "lat": c[1]}

        zones.append(
            {
                "zone_id": str(uuid.uuid4()),
                "label": zd["label"],
                "centre_gps": {"lat": centre["lat"], "lng": centre["lng"]},
                "radius_metres": radius_m,
                "zone_type": zd["zone_type"],
                "sequence_order": idx + 1,
                "gedi_available": gedi_available,
            }
        )

    # --- Walking path estimate (straight-line between zone centroids) -----
    total_walk = 0.0
    for i in range(len(zones) - 1):
        c1 = zones[i]["centre_gps"]
        c2 = zones[i + 1]["centre_gps"]
        dlat = (c2["lat"] - c1["lat"]) * 111_320
        dlng = (c2["lng"] - c1["lng"]) * 111_320 * math.cos(
            math.radians((c1["lat"] + c2["lat"]) / 2)
        )
        total_walk += math.sqrt(dlat**2 + dlng**2)

    for z in zones:
        z["walking_path_metres"] = round(total_walk, 1)

    logger.info(
        "Generated %d zones for land %s (area=%.2f ha, radius=%.0f m, gedi=%s)",
        len(zones),
        land_id,
        area_ha,
        radius_m,
        gedi_available,
    )
    return zones
