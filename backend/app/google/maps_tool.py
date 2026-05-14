"""
Google My Maps tool — create and update editable maps on the user's Google account.

Flow:
  1. Geocode each place name via Nominatim (no API key needed)
  2. Build KML document with Placemarks
  3. Upload to Google Drive as application/vnd.google-apps.map → editable My Maps
  4. Save map metadata to vault (Maps/{title}.md) for future edits
  5. Return the editable My Maps link
"""
from __future__ import annotations

import io
import json
import logging
from typing import Any

import httpx
from langchain_core.tools import tool

logger = logging.getLogger("pa.google.maps_tool")

_UA = "personal-assistant-danidin/1.0"


# ── Geocoding ─────────────────────────────────────────────────────────────────

async def _geocode(place: str, city: str, country: str) -> dict | None:
    """Geocode a place name using Nominatim. Returns dict(name, lat, lon, display) or None."""
    queries = [f"{place}, {city}, {country}", f"{place}, {city}", f"{place}, {country}"]
    async with httpx.AsyncClient(timeout=8.0) as client:
        for q in queries:
            try:
                r = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": q, "format": "json", "limit": 1, "accept-language": "he,en"},
                    headers={"User-Agent": _UA},
                )
                results = r.json()
                if results:
                    return {
                        "name": place,
                        "lat": float(results[0]["lat"]),
                        "lon": float(results[0]["lon"]),
                        "display": results[0].get("display_name", place).split(",")[0].strip(),
                    }
            except Exception:
                pass
    return None


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


# ── Drive upload helpers ───────────────────────────────────────────────────────

def _upload_map(svc, title: str, kml: bytes, folder_id: str) -> tuple[str, str]:
    """Upload KML to Drive and convert to My Maps. Returns (file_id, edit_link)."""
    from googleapiclient.http import MediaIoBaseUpload
    f = svc.files().create(
        body={
            "name": title,
            "mimeType": "application/vnd.google-apps.map",
            "parents": [folder_id],
        },
        media_body=MediaIoBaseUpload(
            io.BytesIO(kml),
            mimetype="application/vnd.google-earth.kml+xml",
        ),
        fields="id",
    ).execute()
    fid = f["id"]
    return fid, f"https://www.google.com/maps/d/edit?mid={fid}"


def _update_map(svc, file_id: str, kml: bytes) -> None:
    """Replace KML content of an existing My Maps file."""
    from googleapiclient.http import MediaIoBaseUpload
    svc.files().update(
        fileId=file_id,
        media_body=MediaIoBaseUpload(
            io.BytesIO(kml),
            mimetype="application/vnd.google-earth.kml+xml",
        ),
    ).execute()


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
        """Create an editable Google My Maps with specific places marked as pins, saved to the user's Google account.

        Use when the user wants to mark places to visit in a city, create a trip map, or pin locations.
        Example triggers: "צור לי מפה של מקומות", "תסמן לי את המקומות האלה על מפה", "create a map with these places".

        title: descriptive map name, e.g. "מקומות לביקור בירושלים"
        city: city name in English, e.g. "Jerusalem"
        country: country name in English, e.g. "Israel"
        places: list of place names to geocode and add as pins
        """
        from app.google.auth import get_credentials
        from app.google.drive import _svc, _get_or_create_folder

        creds = get_credentials(chat_id)
        if not creds or not creds.valid:
            return "Google Drive לא מחובר. אנא קרא ל-google_connect."
        if creds.scopes and not any("drive" in s for s in creds.scopes):
            return "הרשאת Drive חסרה. אנא קרא ל-google_connect כדי להוסיף אותה."

        # Geocode all places
        geocoded: list[dict] = []
        not_found: list[str] = []
        for p in places:
            result = await _geocode(p, city, country)
            if result:
                geocoded.append(result)
            else:
                not_found.append(p)

        if not geocoded:
            return f"❌ לא הצלחתי למצוא מיקומים עבור: {', '.join(places)}"

        kml = _build_kml(title, geocoded)

        try:
            svc = _svc(creds)
            pa_id = _get_or_create_folder(svc, "PA")
            folder_id = _get_or_create_folder(svc, "Maps", pa_id)
            file_id, edit_link = _upload_map(svc, title, kml, folder_id)
        except Exception as e:
            logger.exception("create_google_map: upload failed")
            return f"❌ שגיאה ביצירת המפה: {e}"

        _save_meta(title, {
            "file_id": file_id,
            "title": title,
            "city": city,
            "country": country,
            "places": geocoded,
        })

        lines = [
            f"✅ מפה נוצרה: **{title}**",
            "",
            f"🗺️ **[פתח וערוך ב-Google My Maps]({edit_link})**",
            "",
            f"📍 {len(geocoded)} מקומות סומנו:",
        ] + [f"  • {p['name']}" for p in geocoded]

        if not_found:
            lines += ["", f"⚠️ לא נמצאו: {', '.join(not_found)}"]
        lines += ["", "💡 ניתן להוסיף מקומות נוספים ישירות ב-My Maps או לבקש ממני להוסיף."]
        return "\n".join(lines)

    @tool
    async def add_places_to_map(
        map_title: str,
        new_places: list[str],
        city: str = "",
        country: str = "",
    ) -> str:
        """Add more places to an existing Google My Maps that was previously created.

        Use when the user says "הוסף למפה", "תוסיף עוד מקומות למפה שלי", "add more places to the map".
        map_title: exact title of the previously created map
        new_places: list of new place names to add
        city/country: override if the new places are in a different location (otherwise uses original)
        """
        from app.google.auth import get_credentials
        from app.google.drive import _svc

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
        not_found: list[str] = []
        for p in new_places:
            result = await _geocode(p, use_city, use_country)
            if result:
                new_geocoded.append(result)
            else:
                not_found.append(p)

        if not new_geocoded:
            return f"❌ לא הצלחתי למצוא מיקומים עבור: {', '.join(new_places)}"

        all_places = existing + new_geocoded
        kml = _build_kml(meta["title"], all_places)

        creds = get_credentials(chat_id)
        if not creds or not creds.valid:
            return "Google Drive לא מחובר."

        try:
            svc = _svc(creds)
            _update_map(svc, meta["file_id"], kml)
        except Exception as e:
            logger.exception("add_places_to_map: update failed")
            return f"❌ שגיאה בעדכון המפה: {e}"

        meta["places"] = all_places
        _save_meta(map_title, meta)

        edit_link = f"https://www.google.com/maps/d/edit?mid={meta['file_id']}"
        lines = [
            f"✅ המפה עודכנה: **{meta['title']}**",
            "",
            f"🗺️ **[פתח ב-Google My Maps]({edit_link})**",
            "",
            f"➕ נוספו {len(new_geocoded)} מקומות:",
        ] + [f"  • {p['name']}" for p in new_geocoded]

        if not_found:
            lines += ["", f"⚠️ לא נמצאו: {', '.join(not_found)}"]
        lines += ["", f"📍 סה\"כ {len(all_places)} מקומות במפה"]
        return "\n".join(lines)

    return [create_google_map, add_places_to_map]
