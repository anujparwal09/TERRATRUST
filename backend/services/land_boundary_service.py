"""Land boundary service implementing the documented 3-layer fetch flow."""

import json
import logging
import re
from html import unescape
from typing import Any, Dict, Optional, Sequence

import httpx
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright
from redis import Redis
from shapely.geometry import shape

from app.config import settings
from services import satellite_service

logger = logging.getLogger("terratrust.land_boundary")

_redis_client: Redis | None = None
_redis_initialised = False

# ---------------------------------------------------------------------------
# LGD (Local Government Directory) API helpers
# ---------------------------------------------------------------------------
LGD_BASE_URL = settings.LGD_API_BASE

STATE_WMS_CONFIG = {
    "Maharashtra": {
        "wms_url": "https://mahabhunakasha.mahabhumi.gov.in/WMS",
        "portal_url": "https://mahabhunakasha.mahabhumi.gov.in/27/index.jsp",
        "state_code": "27",
        "prefix": "RVM05",
        "layer": 1,
    },
    "Karnataka": {
        "wms_url": "https://landrecords.karnataka.gov.in/GeoServer/WMS",
        "portal_url": "https://landrecords.karnataka.gov.in/service2/forM16A.aspx",
        "state_code": "29",
        "prefix": "RVM05",
        "layer": 2,
    },
}

DEFAULT_LAYER2_SELECTORS: Dict[str, Sequence[str]] = {
    "district": (
        "select[name*='district']",
        "select[id*='district']",
        "select[name*='District']",
        "select[id*='District']",
    ),
    "taluka": (
        "select[name*='taluka']",
        "select[id*='taluka']",
        "select[name*='tehsil']",
        "select[id*='tehsil']",
        "select[name*='Taluka']",
        "select[id*='Taluka']",
    ),
    "village": (
        "select[name*='village']",
        "select[id*='village']",
        "select[name*='Village']",
        "select[id*='Village']",
    ),
    "survey": (
        "input[name*='survey']",
        "input[id*='survey']",
        "input[name*='surveyno']",
        "input[id*='surveyno']",
        "input[name*='gat']",
        "input[id*='gat']",
        "input[name*='plot']",
        "input[id*='plot']",
    ),
    "search": (
        "button:has-text('Search')",
        "button:has-text('Find')",
        "button:has-text('View')",
        "button:has-text('Show')",
        "input[type='submit'][value*='Search']",
        "input[type='submit'][value*='Find']",
        "input[type='button'][value*='Search']",
        "input[type='button'][value*='Find']",
        "a:has-text('Search')",
    ),
}


def _get_state_config(state: str) -> Optional[Dict[str, str]]:
    """Return per-state WMS configuration when available."""
    return STATE_WMS_CONFIG.get(state)


def _normalise_portal_text(value: str) -> str:
    """Normalise scraped portal text for fuzzy comparison."""
    return re.sub(r"\s+", " ", (value or "")).strip().lower()


def _text_matches(candidate: str, target: str) -> bool:
    """Return whether a portal label is a reasonable match for a target value."""
    left = _normalise_portal_text(candidate)
    right = _normalise_portal_text(target)
    if not left or not right:
        return False
    return left == right or right in left or left in right


def _get_redis_client() -> Redis | None:
    """Return the cached Redis client used for LGD lookups when available."""
    global _redis_client, _redis_initialised

    if _redis_initialised:
        return _redis_client

    _redis_initialised = True
    try:
        _redis_client = Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        _redis_client.ping()
    except Exception as exc:
        logger.warning("Redis unavailable for LGD caching: %s", exc)
        _redis_client = None

    return _redis_client


def _build_lgd_cache_key(district: str, taluka: str, village: str) -> str:
    """Build a stable cache key for an LGD location lookup."""
    return "lgd:{district}:{taluka}:{village}".format(
        district=district.strip().lower(),
        taluka=taluka.strip().lower(),
        village=village.strip().lower(),
    )


async def _select_option_by_text(
    page: Page,
    selectors: Sequence[str],
    value: str,
) -> bool:
    """Select the first matching option from a standard HTML ``select``."""
    for selector in selectors:
        locator = page.locator(selector)
        if await locator.count() == 0:
            continue

        control = locator.first
        try:
            options = await control.locator("option").evaluate_all(
                """
                (elements) => elements.map((element) => ({
                    value: element.value,
                    text: (element.textContent || '').trim(),
                }))
                """
            )
        except Exception:
            continue

        for option in options:
            if option.get("value") and _text_matches(option.get("text", ""), value):
                await control.select_option(value=option["value"])
                await page.wait_for_timeout(750)
                return True

    return False


async def _fill_first_input(page: Page, selectors: Sequence[str], value: str) -> bool:
    """Fill the first visible input matching a selector list."""
    for selector in selectors:
        locator = page.locator(selector)
        if await locator.count() == 0:
            continue

        control = locator.first
        try:
            await control.fill("")
            await control.fill(value)
            await page.wait_for_timeout(500)
            return True
        except Exception:
            continue

    return False


async def _click_first(page: Page, selectors: Sequence[str]) -> bool:
    """Click the first actionable element that matches a selector list."""
    for selector in selectors:
        locator = page.locator(selector)
        if await locator.count() == 0:
            continue

        try:
            await locator.first.click()
            await page.wait_for_timeout(1000)
            return True
        except Exception:
            continue

    return False


def _normalise_geojson_candidate(candidate: Any) -> Optional[Dict[str, Any]]:
    """Convert Feature / FeatureCollection payloads into raw geometry dicts."""
    if not isinstance(candidate, dict):
        return None

    if candidate.get("type") == "Feature":
        return _normalise_geojson_candidate(candidate.get("geometry"))

    if candidate.get("type") == "FeatureCollection":
        for feature in candidate.get("features", []):
            geometry = _normalise_geojson_candidate(feature)
            if geometry:
                return geometry
        return None

    if candidate.get("type") in {"Polygon", "MultiPolygon"} and isinstance(
        candidate.get("coordinates"), list
    ):
        return {
            "type": candidate["type"],
            "coordinates": candidate["coordinates"],
        }

    if isinstance(candidate.get("geometry"), dict):
        return _normalise_geojson_candidate(candidate["geometry"])

    return None


def _extract_balanced_json(text: str, start_index: int) -> Optional[str]:
    """Extract a single balanced JSON object starting at ``start_index``."""
    depth = 0
    in_string = False
    escape = False

    for index in range(start_index, len(text)):
        character = text[index]

        if in_string:
            if escape:
                escape = False
            elif character == "\\":
                escape = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
            continue

        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start_index : index + 1]

    return None


def _extract_geojson_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Find the first GeoJSON-like object embedded in page markup."""
    content = unescape(text)
    anchors = (
        '"type":"FeatureCollection"',
        '"type": "FeatureCollection"',
        '"type":"Feature"',
        '"type": "Feature"',
        '"type":"Polygon"',
        '"type": "Polygon"',
        '"type":"MultiPolygon"',
        '"type": "MultiPolygon"',
    )

    for anchor in anchors:
        search_from = 0
        while True:
            anchor_index = content.find(anchor, search_from)
            if anchor_index == -1:
                break

            start_index = content.rfind("{", 0, anchor_index)
            if start_index == -1:
                search_from = anchor_index + len(anchor)
                continue

            candidate_text = _extract_balanced_json(content, start_index)
            if candidate_text:
                try:
                    candidate = json.loads(candidate_text)
                except json.JSONDecodeError:
                    search_from = anchor_index + len(anchor)
                    continue

                geometry = _normalise_geojson_candidate(candidate)
                if geometry:
                    return geometry

            search_from = anchor_index + len(anchor)

    return None


async def _extract_geojson_from_page(page: Page) -> Optional[Dict[str, Any]]:
    """Inspect common global objects and markup for GeoJSON payloads."""
    try:
        candidate = await page.evaluate(
            """
            () => {
              const normalize = (value) => {
                if (!value || typeof value !== 'object') {
                  return null;
                }

                if (value.type === 'Feature' && value.geometry) {
                  return normalize(value.geometry);
                }

                if (value.type === 'FeatureCollection' && Array.isArray(value.features)) {
                  for (const feature of value.features) {
                    const geometry = normalize(feature);
                    if (geometry) {
                      return geometry;
                    }
                  }
                  return null;
                }

                if ((value.type === 'Polygon' || value.type === 'MultiPolygon') && Array.isArray(value.coordinates)) {
                  return { type: value.type, coordinates: value.coordinates };
                }

                if (value.geometry && typeof value.geometry === 'object') {
                  return normalize(value.geometry);
                }

                return null;
              };

              const globalNames = [
                'geojson',
                'GeoJSON',
                'selectedFeature',
                'parcelGeoJSON',
                'plotGeoJSON',
                'boundaryGeoJSON',
                'currentFeature',
                'featureInfoResponse'
              ];

              for (const name of globalNames) {
                try {
                  const geometry = normalize(window[name]);
                  if (geometry) {
                    return geometry;
                  }
                } catch (error) {
                  // ignore and continue checking other globals
                }
              }

              const mapCandidates = [window.map, window.leafletMap, window._map];
              for (const mapCandidate of mapCandidates) {
                try {
                  if (mapCandidate && typeof mapCandidate.eachLayer === 'function') {
                    let found = null;
                    mapCandidate.eachLayer((layer) => {
                      if (found || typeof layer.toGeoJSON !== 'function') {
                        return;
                      }
                      const geometry = normalize(layer.toGeoJSON());
                      if (geometry) {
                        found = geometry;
                      }
                    });
                    if (found) {
                      return found;
                    }
                  }
                } catch (error) {
                  // ignore and continue checking other maps
                }
              }

              return null;
            }
            """
        )
    except Exception:
        candidate = None

    geometry = _normalise_geojson_candidate(candidate)
    if geometry:
        return geometry

    return _extract_geojson_from_text(await page.content())


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
    redis_client = _get_redis_client()
    cache_key = _build_lgd_cache_key(district, taluka, village)

    if redis_client is not None:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception as exc:
            logger.warning("Failed to read LGD cache %s: %s", cache_key, exc)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Get district code
        resp = await client.post(f"{LGD_BASE_URL}/GetAllDistricts")
        resp.raise_for_status()
        districts = resp.json()
        dist_code: Optional[int] = None
        for d in districts:
            d_name = d.get("distname_eng", d.get("districtName", d.get("name", "")))
            if d_name.strip().lower() == district.strip().lower():
                dist_code = d.get("distcode", d.get("districtCode", d.get("code")))
                break
        if dist_code is None:
            raise ValueError(f"District '{district}' not found in LGD directory.")

        # 2. Get taluka code
        resp = await client.post(
            f"{LGD_BASE_URL}/GetTalukasOfDistrict",
            json={"distcode": dist_code},
        )
        resp.raise_for_status()
        talukas = resp.json()
        taluka_code: Optional[int] = None
        for t in talukas:
            t_name = t.get("talukaname_eng", t.get("talukaName", t.get("name", "")))
            if t_name.strip().lower() == taluka.strip().lower():
                taluka_code = t.get("talukacode", t.get("talukaCode", t.get("code")))
                break
        if taluka_code is None:
            raise ValueError(f"Taluka '{taluka}' not found under district code {dist_code}.")

        # 3. Get village code
        resp = await client.post(
            f"{LGD_BASE_URL}/GetVillagesOfDistrictAndTaluka",
            json={"distcode": dist_code, "talukacode": taluka_code},
        )
        resp.raise_for_status()
        villages = resp.json()
        village_code: Optional[int] = None
        for v in villages:
            v_name = v.get("villagename_eng", v.get("villageName", v.get("name", "")))
            if v_name.strip().lower() == village.strip().lower():
                village_code = v.get("villagecode", v.get("villageCode", v.get("code")))
                break
        if village_code is None:
            raise ValueError(
                f"Village '{village}' not found under taluka code {taluka_code}."
            )

    result = {
        "dist_code": dist_code,
        "taluka_code": taluka_code,
        "village_code": village_code,
    }

    if redis_client is not None:
        try:
            redis_client.setex(cache_key, 30 * 24 * 60 * 60, json.dumps(result))
        except Exception as exc:
            logger.warning("Failed to write LGD cache %s: %s", cache_key, exc)

    return result


def construct_gis_code(lgd_codes: Dict[str, int], survey_number: str, state: str) -> str:
    """Build the NIC GIS code described in the SRS for supported states."""
    state_config = _get_state_config(state)
    if state_config is None:
        raise ValueError(f"State '{state}' is not configured for Layer 1 boundary fetch.")

    survey_padded = survey_number.replace("/", "").zfill(6)
    taluka_padded = str(lgd_codes["taluka_code"]).zfill(4)
    village_padded = str(lgd_codes["village_code"]).zfill(6)
    state_code = state_config["state_code"]
    prefix = state_config["prefix"]
    return f"{prefix}{state_code}{taluka_padded}{village_padded}{survey_padded}"


# ---------------------------------------------------------------------------
# Layer 1 — BhuNaksha WMS GetFeatureInfo
# ---------------------------------------------------------------------------
async def fetch_boundary_layer1(
    survey_number: str,
    district: str,
    taluka: str,
    village: str,
    state: str,
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
        state_config = _get_state_config(state)
        if state_config is None:
            logger.info("Layer 1 (WMS) is not configured for state '%s'.", state)
            return None
        if state_config.get("layer") != 1:
            logger.info("State '%s' is configured for a later boundary layer, not Layer 1.", state)
            return None

        lgd_codes = await get_lgd_codes(district, taluka, village)
        gis_code = construct_gis_code(lgd_codes, survey_number, state)

        # Construct a small BBOX around the user's GPS position.
        delta = 0.01
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
            resp = await client.get(state_config["wms_url"], params=params)
            resp.raise_for_status()
            data = resp.json()

        features = data.get("features", [])
        if features and features[0].get("geometry"):
            logger.info("Layer 1 (WMS) boundary found for gis_code=%s", gis_code)
            return {
                "geojson": features[0]["geometry"],
                "lgd_district_code": str(lgd_codes["dist_code"]),
                "lgd_taluka_code": str(lgd_codes["taluka_code"]),
                "lgd_village_code": str(lgd_codes["village_code"]),
                "gis_code": gis_code,
            }

        logger.info("Layer 1 (WMS) returned no features for gis_code=%s", gis_code)
        return None

    except Exception as exc:
        logger.warning("Layer 1 (WMS) failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Layer 2 — Playwright scraping
# ---------------------------------------------------------------------------
async def fetch_boundary_layer2(
    survey_number: str,
    district: str,
    taluka: str,
    village: str,
    state: str,
    user_lat: float,
    user_lng: float,
) -> Optional[Dict[str, Any]]:
    """Attempt to fetch boundary via browser scraping (Layer 2).

    Uses a best-effort Playwright flow against state-configured land
    record portals, then extracts the first GeoJSON polygon found in
    the page state or markup.
    """
    state_config = _get_state_config(state)
    if state_config is None:
        logger.info("Layer 2 (scrape) is not configured for state '%s'.", state)
        return None

    portal_url = state_config.get("portal_url")
    if not portal_url:
        logger.info("Layer 2 (scrape) has no portal URL configured for state '%s'.", state)
        return None

    selectors = {
        key: tuple(state_config.get("selectors", {}).get(key, DEFAULT_LAYER2_SELECTORS[key]))
        for key in DEFAULT_LAYER2_SELECTORS
    }

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(ignore_https_errors=True)
            page = await context.new_page()

            try:
                await page.goto(portal_url, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(2_000)

                await _select_option_by_text(page, selectors["district"], district)
                await _select_option_by_text(page, selectors["taluka"], taluka)
                await _select_option_by_text(page, selectors["village"], village)
                await _fill_first_input(page, selectors["survey"], survey_number)
                await _click_first(page, selectors["search"])

                try:
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeoutError:
                    logger.debug(
                        "Layer 2 portal '%s' did not reach networkidle; continuing with page inspection.",
                        portal_url,
                    )

                geometry = await _extract_geojson_from_page(page)
                if geometry:
                    logger.info(
                        "Layer 2 (Playwright scrape) boundary found for survey %s in %s.",
                        survey_number,
                        state,
                    )
                    return {"geojson": geometry}
            finally:
                await context.close()
                await browser.close()

    except Exception as exc:
        logger.warning(
            "Layer 2 (Playwright scrape) failed for survey %s in %s: %s",
            survey_number,
            state,
            exc,
        )

    return None


def _estimate_area_hectares(geojson: Dict[str, Any]) -> Optional[float]:
    """Estimate polygon area in hectares for response payloads."""
    try:
        geom = shape(geojson)
        return round(geom.area * 111320 * 111320 / 10000, 4)
    except Exception:
        return None


def _safe_satellite_thumbnail_url(geojson: Dict[str, Any]) -> Optional[str]:
    """Best-effort thumbnail generation for boundary confirmation screens."""
    try:
        return satellite_service.generate_true_color_thumbnail_url(geojson)
    except Exception as exc:
        logger.warning("Failed to generate boundary thumbnail: %s", exc)
        return None


def _build_boundary_success_response(
    boundary_source: str,
    boundary_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a boundary response compatible with both documented field names."""
    geojson = boundary_payload["geojson"]
    satellite_url = _safe_satellite_thumbnail_url(geojson)
    response = {
        "status": "success",
        "boundary_source": boundary_source,
        "satellite_png_url": satellite_url,
        "satellite_thumbnail_url": satellite_url,
        "geojson": geojson,
        "area_hectares": _estimate_area_hectares(geojson),
    }
    response.update(
        {
            field: boundary_payload.get(field)
            for field in (
                "lgd_district_code",
                "lgd_taluka_code",
                "lgd_village_code",
                "gis_code",
            )
            if boundary_payload.get(field) is not None
        }
    )
    return response


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
    boundary_payload = await fetch_boundary_layer1(
        survey_number, district, taluka, village, state, user_lat, user_lng
    )
    if boundary_payload:
        return _build_boundary_success_response("WMS_AUTO", boundary_payload)

    # Layer 2 — Playwright scraping
    boundary_payload = await fetch_boundary_layer2(
        survey_number, district, taluka, village, state, user_lat, user_lng
    )
    if boundary_payload:
        return _build_boundary_success_response("SCRAPE", boundary_payload)

    # All layers exhausted
    logger.warning(
        "All boundary layers failed for survey %s, %s, %s",
        survey_number,
        village,
        district,
    )
    return {
        "status": "manual_required",
        "message": "Automatic boundary fetch failed. Please upload the government land-map image manually.",
    }
