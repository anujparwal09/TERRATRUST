"""
Land boundary service — 3-layer boundary fetching system.

Layer 1 : BhuNaksha WMS GetFeatureInfo (Maharashtra)
Layer 2 : Playwright scraping (stub — Phase 2)
Layer 3 : Manual fallback
"""

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("terratrust.land_boundary")

# ---------------------------------------------------------------------------
# LGD (Local Government Directory) API helpers
# ---------------------------------------------------------------------------
LGD_BASE_URL = "http://115.124.105.220/API"


async def get_lgd_codes(district: str, taluka: str, village: str) -> Dict[str, int]:
    """Resolve district / taluka / village names to numeric LGD codes.

    Uses the public LGD API endpoints maintained by MoRD.

    Returns
    -------
    dict
        ``{dist_code, taluka_code, village_code}``

    Raises
    ------
    ValueError
        If any of the names cannot be resolved.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Get district code
        resp = await client.post(f"{LGD_BASE_URL}/GetAllDistricts")
        resp.raise_for_status()
        districts = resp.json()
        dist_code: Optional[int] = None
        for d in districts:
            d_name = d.get("districtName", d.get("name", ""))
            if d_name.strip().lower() == district.strip().lower():
                dist_code = d.get("districtCode", d.get("code"))
                break
        if dist_code is None:
            raise ValueError(f"District '{district}' not found in LGD directory.")

        # 2. Get taluka code
        resp = await client.post(
            f"{LGD_BASE_URL}/GetTalukasOfDistrict",
            json={"districtCode": dist_code},
        )
        resp.raise_for_status()
        talukas = resp.json()
        taluka_code: Optional[int] = None
        for t in talukas:
            t_name = t.get("talukaName", t.get("name", ""))
            if t_name.strip().lower() == taluka.strip().lower():
                taluka_code = t.get("talukaCode", t.get("code"))
                break
        if taluka_code is None:
            raise ValueError(f"Taluka '{taluka}' not found under district code {dist_code}.")

        # 3. Get village code
        resp = await client.post(
            f"{LGD_BASE_URL}/GetVillagesOfDistrictAndTaluka",
            json={"districtCode": dist_code, "talukaCode": taluka_code},
        )
        resp.raise_for_status()
        villages = resp.json()
        village_code: Optional[int] = None
        for v in villages:
            v_name = v.get("villageName", v.get("name", ""))
            if v_name.strip().lower() == village.strip().lower():
                village_code = v.get("villageCode", v.get("code"))
                break
        if village_code is None:
            raise ValueError(
                f"Village '{village}' not found under taluka code {taluka_code}."
            )

    return {
        "dist_code": dist_code,
        "taluka_code": taluka_code,
        "village_code": village_code,
    }


def construct_gis_code(lgd_codes: Dict[str, int], survey_number: str) -> str:
    """Build a GIS code from LGD codes + zero-padded survey number.

    Format: ``{dist_code}{taluka_code}{village_code}{survey_padded}``
    """
    survey_padded = survey_number.replace("/", "").zfill(6)
    return (
        f"{lgd_codes['dist_code']}"
        f"{lgd_codes['taluka_code']}"
        f"{lgd_codes['village_code']}"
        f"{survey_padded}"
    )


# ---------------------------------------------------------------------------
# Layer 1 — BhuNaksha WMS GetFeatureInfo
# ---------------------------------------------------------------------------
BHUNAKSHA_WMS_URL = "https://mahabhunakasha.mahabhumi.gov.in/WMS"


async def fetch_boundary_layer1(
    survey_number: str,
    district: str,
    taluka: str,
    village: str,
    user_lat: float,
    user_lng: float,
) -> Optional[Dict[str, Any]]:
    """Attempt to fetch the parcel boundary from BhuNaksha WMS (Layer 1).

    Parameters
    ----------
    survey_number, district, taluka, village : str
        Human-readable identifiers for the plot.
    user_lat, user_lng : float
        Approximate centre of the parcel used for the BBOX.

    Returns
    -------
    dict | None
        GeoJSON geometry dict if the WMS response contains a feature,
        otherwise ``None``.
    """
    try:
        lgd_codes = await get_lgd_codes(district, taluka, village)
        gis_code = construct_gis_code(lgd_codes, survey_number)

        # Construct a small BBOX around the user's GPS position
        delta = 0.005  # ~500 m
        bbox = f"{user_lng - delta},{user_lat - delta},{user_lng + delta},{user_lat + delta}"

        params = {
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetFeatureInfo",
            "INFO_FORMAT": "application/json",
            "QUERY_LAYERS": "PLOT_BOUNDARY",
            "LAYERS": "PLOT_BOUNDARY",
            "gis_code": gis_code,
            "BBOX": bbox,
            "CRS": "EPSG:4326",
            "WIDTH": 800,
            "HEIGHT": 600,
            "I": 400,
            "J": 300,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(BHUNAKSHA_WMS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        features = data.get("features", [])
        if features and features[0].get("geometry"):
            logger.info("Layer 1 (WMS) boundary found for gis_code=%s", gis_code)
            return features[0]["geometry"]

        logger.info("Layer 1 (WMS) returned no features for gis_code=%s", gis_code)
        return None

    except Exception as exc:
        logger.warning("Layer 1 (WMS) failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Layer 2 — Playwright scraping (Phase 2 stub)
# ---------------------------------------------------------------------------
async def fetch_boundary_layer2(
    survey_number: str,
    district: str,
    taluka: str,
    village: str,
    user_lat: float,
    user_lng: float,
) -> Optional[Dict[str, Any]]:
    """Attempt to fetch boundary via browser scraping (Layer 2).

    .. note::
        Playwright scraping — implement in Phase 2.
        Currently returns ``None`` so the system falls through
        to manual boundary entry.
    """
    logger.info("Layer 2 (Playwright scrape) not yet implemented — skipping.")
    return None


# ---------------------------------------------------------------------------
# Unified boundary fetcher
# ---------------------------------------------------------------------------
async def fetch_land_boundary(
    survey_number: str,
    district: str,
    taluka: str,
    village: str,
    state: str,
    user_lat: float,
    user_lng: float,
) -> Dict[str, Any]:
    """Try all boundary layers in order and return the first success.

    Returns
    -------
    dict
        If a boundary is found:
        ``{status: "success", boundary_source, geojson, area_hectares}``

        If all layers fail:
        ``{status: "manual_required"}``
    """
    # Layer 1 — BhuNaksha WMS
    geojson = await fetch_boundary_layer1(
        survey_number, district, taluka, village, user_lat, user_lng
    )
    if geojson:
        return {
            "status": "success",
            "boundary_source": "WMS_AUTO",
            "geojson": geojson,
        }

    # Layer 2 — Playwright scraping
    geojson = await fetch_boundary_layer2(
        survey_number, district, taluka, village, user_lat, user_lng
    )
    if geojson:
        return {
            "status": "success",
            "boundary_source": "SCRAPE",
            "geojson": geojson,
        }

    # All layers exhausted
    logger.warning(
        "All boundary layers failed for survey %s, %s, %s",
        survey_number,
        village,
        district,
    )
    return {"status": "manual_required"}
