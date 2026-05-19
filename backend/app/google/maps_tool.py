"""
Google My Maps tool — create and update editable maps on the user's Google account.

Flow:
  1. Geocode each place name via Nominatim (no API key needed)
  2. If Nominatim fails, fall back to web search to find the address, then retry Nominatim
  3. Build KML document with Placemarks
  4. Upload to Google Drive as application/vnd.google-apps.map → editable My Maps
  5. Save map metadata to vault (Maps/{title}.md) for future edits
  6. Return the editable My Maps link
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import urllib.parse

import httpx
from langchain_core.tools import tool

logger = logging.getLogger("pa.google.maps_tool")

_UA = "personal-assistant-danidin/1.0"


# ── Geocoding ─────────────────────────────────────────────────────────────────

async def _nominatim_query(q: str, place_name: str, client: httpx.AsyncClient) -> dict | None:
    """Single Nominatim lookup. Returns geocoded dict or None."""
    try:
        r = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q, "format": "json", "limit": 1, "accept-language": "he,en"},
            headers={"User-Agent": _UA},
        )
        results = r.json()
        if results:
            return {
                "name": place_name,
                "lat": float(results[0]["lat"]),
                "lon": float(results[0]["lon"]),
                "display": results[0].get("display_name", place_name).split(",")[0].strip(),
            }
    except Exception:
        pass
    return None


def _extract_address_candidates(search_text: str, city: str, country: str) -> list[str]:
    """Extract address-like candidates from a web search result string."""
    candidates = []
    for line in search_text.splitlines():
        line = line.strip()
        if not line or len(line) < 5:
            continue
        # Lines that mention the city or country are likely address-bearing
        if city.lower() in line.lower() or country.lower() in line.lower():
            # Truncate to a reasonable length for geocoding
            candidates.append(line[:200])
        # Lines with number + word pattern typical of street addresses
        elif re.search(r'\d+\s+\w', line):
            candidates.append(line[:200])
        if len(candidates) >= 5:
            break
    return candidates


_PLACE_STRIP = re.compile(
    r'^(הכתובת של|המיקום של|כתובת|מיקום|the address of|the location of|address of|location of)\s+',
    re.IGNORECASE,
)


def _clean_place(place: str) -> str:
    """Strip common descriptive prefixes so the geocoder gets a clean name."""
    return _PLACE_STRIP.sub("", place.strip())


async def _geocode_with_fallback(place: str, city: str, country: str) -> tuple[dict | None, str]:
    """Geocode a place via Nominatim, falling back to web search if not found.

    Returns (result_dict | None, status) where status is one of:
      'nominatim' — found directly
      'web'       — found after web search fallback
      'not_found' — could not locate even after web search
    """
    place = _clean_place(place)

    async with httpx.AsyncClient(timeout=8.0) as client:
        # Pass 1: try Nominatim with progressively looser queries
        for q in [f"{place}, {city}, {country}", f"{place}, {city}", f"{place}, {country}", place]:
            result = await _nominatim_query(q, place, client)
            if result:
                return result, "nominatim"

    # Pass 2: web search fallback
    try:
        from app.web.tools import web_search as _web_search
        query = f"{place} {city} {country} address location"
        search_text = await asyncio.to_thread(_web_search, query)

        candidates = _extract_address_candidates(search_text, city, country)

        async with httpx.AsyncClient(timeout=8.0) as client:
            for candidate in candidates:
                # Try the candidate alone, then with city/country appended
                for q in [f"{candidate}, {city}, {country}", f"{candidate}, {city}", candidate]:
                    result = await _nominatim_query(q, place, client)
                    if result:
                        logger.info("geocode fallback via web search succeeded for '%s'", place)
                        return result, "web"
    except Exception:
        logger.exception("geocode web search fallback failed for '%s'", place)

    return None, "not_found"


# ── KML builder ───────────────────────────────────────────────────────────────

def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_kml(title: str, places: list[dict]) -> bytes:
    marks = ""
    for p in places:
        marks += (
            f"\n  <Placemark>"
            f"<name>{_esc(p['name'])}</name>"
            f"<description>{_esc(p.get('display', p['name']))}</description>"
            f"<Point><coordinates>{p['lon']},{p['lat']},0</coordinates></Point>"
            f"</Placemark>"
        )
    kml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2">'
        f"<Document><name>{_esc(title)}</name>"
        f"<description>Created by Danidin</description>"
        f"{marks}"
        "</Document></kml>"
    )
    return kml.encode("utf-8")


# ── Google Maps URL builder ────────────────────────────────────────────────────

def _maps_url(places: list[dict]) -> str:
    """Build a Google Maps URL that shows all places as pins.

    Single place  → /maps?q=lat,lon  (opens location card)
    Multiple      → /maps/dir/lat1,lon1/lat2,lon2/…  (shows all as waypoints/pins)
    """
    if not places:
        return "https://www.google.com/maps"
    if len(places) == 1:
        p = places[0]
        name = urllib.parse.quote(p["name"])
        return f"https://www.google.com/maps/search/?api=1&query={p['lat']},{p['lon']}&query_place_id={name}"
    parts = "/".join(f"{p['lat']},{p['lon']}" for p in places)
    return f"https://www.google.com/maps/dir/{parts}"


# ── Vault helpers ─────────────────────────────────────────────────────────────

_MAPS_CAT = "Maps"


def _save_meta(title: str, meta: dict) -> None:
    try:
        from app.memory.manager import save_fact
        save_fact(_MAPS_CAT, title, json.dumps(meta, ensure_ascii=False))
    except Exception:
        pass


def _load_meta(title: str) -> dict | None:
    try:
        from app.memory.manager import read_note
        raw = read_note(f"{_MAPS_CAT}/{title}.md")
        if not raw:
            return None
        # The vault file may have markdown frontmatter — grab the JSON block
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        return None
    except Exception:
        return None


# ── Tools ─────────────────────────────────────────────────────────────────────

def get_maps_tools(chat_id: str) -> list:

    @tool
    async def create_google_map(
        title: str,
        city: str,
        country: str,
        places: list[str],
    ) -> str:
        """Create a Google Maps link with specific places marked as pins.

        Use when the user wants to mark places to visit in a city, create a trip map, or pin locations.
        Example triggers: "צור לי מפה של מקומות", "תסמן לי את המקומות האלה על מפה", "הצג במפה", "create a map with these places", "show on map".

        title: descriptive map name, e.g. "מקומות לביקור בירושלים"
        city: city name in English, e.g. "Jerusalem"
        country: country name in English, e.g. "Israel"
        places: list of CLEAN place names only — strip any "הכתובת של", "המיקום של", "the address of" prefixes
        """
        # Geocode all places (with web search fallback) — no Drive needed
        geocoded: list[dict] = []
        via_web: list[str] = []
        needs_clarification: list[str] = []
        for p in places:
            result, status = await _geocode_with_fallback(p, city, country)
            if result:
                geocoded.append(result)
                if status == "web":
                    via_web.append(p)
            else:
                needs_clarification.append(p)

        if not geocoded:
            clarify_hint = (
                f"\n\nאנא ספק פרטים נוספים עבור: {', '.join(needs_clarification)}"
                " (למשל: שם רחוב, שכונה, או מזהה מדויק יותר)."
                if needs_clarification else ""
            )
            return f"❌ לא הצלחתי למצוא מיקומים עבור: {', '.join(places)}{clarify_hint}"

        map_url = _maps_url(geocoded)

        _save_meta(title, {
            "map_url": map_url,
            "title": title,
            "city": city,
            "country": country,
            "places": geocoded,
        })

        lines = [
            f"✅ {title}",
            "",
            f"🗺️ **[פתח ב-Google Maps]({map_url})**",
            "",
            f"📍 {len(geocoded)} מקומות:",
        ] + [f"  • {p['name']}" for p in geocoded]

        if via_web:
            lines += ["", f"🔍 נמצא דרך חיפוש ברשת: {', '.join(via_web)}"]
        if needs_clarification:
            lines += [
                "",
                f"❓ לא הצלחתי לאתר: {', '.join(needs_clarification)}",
                "אנא ספק פרטים מדויקים יותר (שם רחוב, שכונה, או מזהה מפורט).",
            ]
        else:
            lines += ["", "💡 אפשר לבקש ממני להוסיף מקומות נוספים."]
        return "\n".join(lines)

    @tool
    async def add_places_to_map(
        map_title: str,
        new_places: list[str],
        city: str = "",
        country: str = "",
    ) -> str:
        """Add more places to an existing map that was previously created.

        Use when the user says "הוסף למפה", "תוסיף עוד מקומות למפה שלי", "add more places to the map".
        map_title: exact title of the previously created map
        new_places: list of new place names to add
        city/country: override if the new places are in a different location (otherwise uses original)
        """
        meta = _load_meta(map_title)
        if not meta:
            return (
                f"לא מצאתי מפה בשם '{map_title}'. "
                "אנא בדוק את השם או צור מפה חדשה."
            )

        use_city = city or meta.get("city", "")
        use_country = country or meta.get("country", "")
        existing: list[dict] = meta.get("places", [])

        new_geocoded: list[dict] = []
        via_web: list[str] = []
        needs_clarification: list[str] = []
        for p in new_places:
            result, status = await _geocode_with_fallback(p, use_city, use_country)
            if result:
                new_geocoded.append(result)
                if status == "web":
                    via_web.append(p)
            else:
                needs_clarification.append(p)

        if not new_geocoded:
            clarify_hint = (
                f"\n\nאנא ספק פרטים נוספים עבור: {', '.join(needs_clarification)}"
                " (למשל: שם רחוב, שכונה, או מזהה מדויק יותר)."
                if needs_clarification else ""
            )
            return f"❌ לא הצלחתי למצוא מיקומים עבור: {', '.join(new_places)}{clarify_hint}"

        all_places = existing + new_geocoded
        map_url = _maps_url(all_places)

        meta["places"] = all_places
        meta["map_url"] = map_url
        _save_meta(map_title, meta)

        lines = [
            f"✅ המפה עודכנה: **{meta['title']}**",
            "",
            f"🗺️ **[פתח ב-Google Maps]({map_url})**",
            "",
            f"➕ נוספו {len(new_geocoded)} מקומות:",
        ] + [f"  • {p['name']}" for p in new_geocoded]

        if via_web:
            lines += ["", f"🔍 נמצא דרך חיפוש ברשת: {', '.join(via_web)}"]
        if needs_clarification:
            lines += [
                "",
                f"❓ לא הצלחתי לאתר: {', '.join(needs_clarification)}",
                "אנא ספק פרטים מדויקים יותר (שם רחוב, שכונה, או תיאור מפורט).",
            ]
        lines += ["", f"📍 סה\"כ {len(all_places)} מקומות"]
        return "\n".join(lines)

    return [create_google_map, add_places_to_map]
