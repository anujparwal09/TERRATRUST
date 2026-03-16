"""
Fusion engine — the scientific core of TerraTrust-AR.

Combines Sentinel-1 SAR, Sentinel-2 optical, GEDI LiDAR,
SRTM terrain data, and field AR-scanned tree measurements to
estimate above-ground biomass (AGB) using the Chave allometric
equation, then trains an XGBoost regressor on GEE to extrapolate
across the entire parcel.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import ee

from app.config import settings
from app.database import supabase_client

logger = logging.getLogger("terratrust.fusion")

# ---------------------------------------------------------------------------
# Species wood density (g/cm³ → t/m³ equivalent)
# ---------------------------------------------------------------------------
SPECIES_WOOD_DENSITY: Dict[str, float] = {
    "Teak": 0.60,
    "Eucalyptus": 0.55,
    "Neem": 0.56,
    "Mango": 0.54,
    "Bamboo": 0.70,
    "Pongamia": 0.67,
    "Subabul": 0.56,
    "Casuarina": 0.69,
    "Indian Rosewood": 0.75,
    "Drumstick": 0.39,
    "Amla": 0.74,
}

# Default for unknown species
DEFAULT_WOOD_DENSITY: float = 0.58


def _ensure_gee() -> None:
    """Initialise GEE if not already done."""
    try:
        ee.Number(1).getInfo()
    except Exception:
        kp = settings.GEE_SERVICE_ACCOUNT_KEY_PATH
        email = settings.GEE_SERVICE_ACCOUNT_EMAIL
        if kp and os.path.exists(kp):
            credentials = ee.ServiceAccountCredentials(email, kp)
            ee.Initialize(credentials)
        else:
            raise RuntimeError("GEE service-account key not found.")


# ---------------------------------------------------------------------------
# Main fusion function
# ---------------------------------------------------------------------------
def run_fusion(
    audit_id: str,
    land_id: str,
    tree_scans: List[Dict[str, Any]],
    land_boundary_geojson: Dict[str, Any],
    audit_year: int,
) -> Dict[str, Any]:
    """Run the multi-source data fusion and biomass estimation.

    Parameters
    ----------
    audit_id : str
        UUID of the current audit.
    land_id : str
        UUID of the land parcel.
    tree_scans : list[dict]
        List of AR tree scan records with keys:
        species, dbh_cm, height_m (optional), gps:{lat,lng},
        gedi_height_m (optional).
    land_boundary_geojson : dict
        GeoJSON Polygon / MultiPolygon of the parcel.
    audit_year : int
        Calendar year of the audit.

    Returns
    -------
    dict
        ``{total_biomass_tonnes, satellite_features, training_points_count}``
    """
    _ensure_gee()

    # --- EE geometry -------------------------------------------------------
    coords = land_boundary_geojson.get("coordinates", [])
    geom_type = land_boundary_geojson.get("type", "Polygon")
    if geom_type == "Polygon":
        region = ee.Geometry.Polygon(coords)
    else:
        region = ee.Geometry.MultiPolygon(coords)

    from datetime import datetime, timedelta

    end = datetime.utcnow()
    start = end - timedelta(days=180)
    date_start = start.strftime("%Y-%m-%d")
    date_end = end.strftime("%Y-%m-%d")

    # -----------------------------------------------------------------------
    # 1. Sentinel-1 (VH, VV) — speckle filtered
    # -----------------------------------------------------------------------
    s1 = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(region)
        .filterDate(date_start, date_end)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .select(["VH", "VV"])
        .median()
    )
    # Speckle filter — focal median 3×3
    s1_filtered = s1.focal_median(3, "square", "pixels")

    # -----------------------------------------------------------------------
    # 2. Sentinel-2 optical — cloud-filtered median composite
    # -----------------------------------------------------------------------
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(date_start, date_end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
        .median()
    )

    # -----------------------------------------------------------------------
    # 3. Vegetation indices
    # -----------------------------------------------------------------------
    ndvi = s2.normalizedDifference(["B8", "B4"]).rename("NDVI")

    evi = s2.expression(
        "2.5 * ((NIR - RED) / (NIR + 6*RED - 7.5*BLUE + 1))",
        {
            "NIR": s2.select("B8"),
            "RED": s2.select("B4"),
            "BLUE": s2.select("B2"),
        },
    ).rename("EVI")

    red_edge = s2.select("B7").rename("RED_EDGE")

    # -----------------------------------------------------------------------
    # 4. GEDI canopy height
    # -----------------------------------------------------------------------
    gedi = (
        ee.ImageCollection("LARSE/GEDI/GEDI02_A_002_MONTHLY")
        .filterBounds(region)
        .select(["rh98"])
        .mean()
        .rename("GEDI_RH98")
    )

    # -----------------------------------------------------------------------
    # 5. SRTM elevation & slope
    # -----------------------------------------------------------------------
    srtm = ee.Image("USGS/SRTMGL1_003").select("elevation").rename("ELEVATION")
    slope = ee.Terrain.slope(srtm).rename("SLOPE")

    # -----------------------------------------------------------------------
    # 6. Stack all bands into a single feature image
    # -----------------------------------------------------------------------
    feature_stack = ee.Image.cat(
        [s1_filtered, ndvi, evi, red_edge, gedi, srtm, slope]
    ).clip(region)

    # -----------------------------------------------------------------------
    # 7. Compute per-tree AGB using Chave allometric equation
    # -----------------------------------------------------------------------
    training_points: List[ee.Feature] = []
    satellite_vh_accum = 0.0
    ndvi_accum = 0.0
    gedi_height_accum = 0.0
    n_trees = len(tree_scans)

    for scan in tree_scans:
        species = scan.get("species", "Unknown")
        dbh_cm = float(scan["dbh_cm"])
        height_m = scan.get("gedi_height_m") or scan.get("height_m") or 10.0
        wood_density = SPECIES_WOOD_DENSITY.get(species, DEFAULT_WOOD_DENSITY)

        # Chave et al. (2014) pan-tropical equation
        agb_kg = 0.0673 * (wood_density * (dbh_cm**2) * height_m) ** 0.976
        agb_t_ha = (agb_kg / 1000) / 0.01  # per-tree → tonnes per hectare (0.01 ha plot)

        gps = scan.get("gps", {})
        lat = gps.get("lat", 0)
        lng = gps.get("lng", 0)

        point = ee.Feature(
            ee.Geometry.Point([lng, lat]),
            {"AGB_THA": agb_t_ha},
        )
        training_points.append(point)

    # -----------------------------------------------------------------------
    # 8. Train XGBoost regressor on GEE
    # -----------------------------------------------------------------------
    training_fc = ee.FeatureCollection(training_points)

    band_names = [
        "VH", "VV", "NDVI", "EVI", "RED_EDGE", "GEDI_RH98",
        "ELEVATION", "SLOPE",
    ]

    training_data = feature_stack.sampleRegions(
        collection=training_fc,
        properties=["AGB_THA"],
        scale=10,
    )

    classifier = (
        ee.Classifier.smileGradientTreeBoost(
            numberOfTrees=100,
            shrinkage=0.05,
            samplingRate=0.7,
        )
        .setOutputMode("REGRESSION")
        .train(
            features=training_data,
            classProperty="AGB_THA",
            inputProperties=band_names,
        )
    )

    # -----------------------------------------------------------------------
    # 9. Classify entire parcel → pixel-level biomass map
    # -----------------------------------------------------------------------
    biomass_map = feature_stack.classify(classifier).rename("BIOMASS_THA")

    # -----------------------------------------------------------------------
    # 10. Sum all pixels to get total biomass (tonnes)
    # -----------------------------------------------------------------------
    # Each Sentinel-2 pixel at 10 m is 0.01 ha
    pixel_area_ha = 0.01
    biomass_sum = (
        biomass_map.multiply(pixel_area_ha)
        .reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=region,
            scale=10,
            maxPixels=1e9,
        )
        .getInfo()
    )

    total_biomass = biomass_sum.get("BIOMASS_THA", 0) or 0

    # Satellite feature statistics (for audit metadata)
    sat_stats = feature_stack.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=10,
        maxPixels=1e8,
    ).getInfo()

    logger.info(
        "Fusion complete for audit %s: total_biomass=%.2f tonnes, %d training points",
        audit_id,
        total_biomass,
        n_trees,
    )

    return {
        "total_biomass_tonnes": round(total_biomass, 4),
        "training_points_count": n_trees,
        "satellite_features": {
            "s1_vh_mean": sat_stats.get("VH", 0),
            "ndvi_mean": sat_stats.get("NDVI", 0),
            "gedi_height_mean": sat_stats.get("GEDI_RH98", 0),
            "evi_mean": sat_stats.get("EVI", 0),
            "elevation_mean": sat_stats.get("ELEVATION", 0),
            "slope_mean": sat_stats.get("SLOPE", 0),
        },
    }


# ---------------------------------------------------------------------------
# Credit calculation
# ---------------------------------------------------------------------------
def calculate_credits(
    total_biomass_tonnes: float,
    land_id: str,
    audit_year: int,
) -> Dict[str, Any]:
    """Calculate carbon credits from biomass change.

    Parameters
    ----------
    total_biomass_tonnes : float
        Current-year total above-ground biomass.
    land_id : str
        UUID of the land parcel.
    audit_year : int
        Calendar year of the current audit.

    Returns
    -------
    dict
        ``{credits_issued, delta_biomass, carbon_tonnes,
           co2_equivalent, previous_biomass, current_biomass}``
    """
    # --- Look up previous year's biomass -----------------------------------
    previous_biomass: float = 0.0
    try:
        prev_resp = (
            supabase_client.table("carbon_audits")
            .select("total_biomass_tonnes")
            .eq("land_id", land_id)
            .eq("audit_year", audit_year - 1)
            .eq("status", "MINTED")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if prev_resp.data:
            previous_biomass = float(prev_resp.data[0].get("total_biomass_tonnes", 0))
    except Exception as exc:
        logger.warning("Could not fetch previous biomass for land %s: %s", land_id, exc)

    delta = total_biomass_tonnes - previous_biomass

    if delta <= 0:
        return {
            "credits_issued": 0,
            "delta_biomass": round(delta, 4),
            "carbon_tonnes": 0,
            "co2_equivalent": 0,
            "previous_biomass": previous_biomass,
            "current_biomass": total_biomass_tonnes,
        }

    # IPCC conversion factors
    carbon_tonnes = delta * 0.47
    co2_equivalent = carbon_tonnes * 3.667
    credits_issued = co2_equivalent

    logger.info(
        "Credit calculation: delta=%.2f t, carbon=%.2f t, CO2e=%.2f t → %d credits",
        delta,
        carbon_tonnes,
        co2_equivalent,
        int(credits_issued),
    )

    return {
        "credits_issued": round(credits_issued, 4),
        "delta_biomass": round(delta, 4),
        "carbon_tonnes": round(carbon_tonnes, 4),
        "co2_equivalent": round(co2_equivalent, 4),
        "previous_biomass": previous_biomass,
        "current_biomass": total_biomass_tonnes,
    }
