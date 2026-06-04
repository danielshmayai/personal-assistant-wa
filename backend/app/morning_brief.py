"""Comprehensive morning brief — weather + calendar + gmail + fitness, all fetched in parallel."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.config import USER_CITY, USER_TIMEZONE

logger = logging.getLogger("pa.morning_brief")

_DAY_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]

_WMO: dict[int, str] = {
    0: "☀️ בהיר", 1: "🌤 בהיר בעיקר", 2: "⛅ מעונן חלקית", 3: "☁️ מעונן",
    45: "🌫 ערפל", 48: "🌫 ערפל קפוא",
    51: "🌦 טפטוף קל", 53: "🌦 טפטוף", 55: "🌧 טפטוף כבד",
    61: "🌧 גשם קל", 63: "🌧 גשם", 65: "🌧 גשם כבד",
    71: "🌨 שלג קל", 73: "🌨 שלג", 75: "❄️ שלג כבד",
    80: "🌦 מקלחות", 81: "🌧 מקלחות", 82: "⛈ מקלחות כבדות",
    95: "⛈ סופת רעמים", 96: "⛈ סופה + ברד",
}


async def _weather() -> str:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            geo = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": USER_CITY, "format": "json", "limit": 1, "accept-language": "en"},
                headers={"User-Agent": "personal-assistant-bot/1.0"},
            )
            results = geo.json()
            if not results:
                return ""
            lat, lon = float(results[0]["lat"]), float(results[0]["lon"])

            fc = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat, "longitude": lon,
                    "current_weather": "true",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
                    "timezone": "auto",
                    "forecast_days": 1,
                },
            )
            data = fc.json()

        cw = data.get("current_weather", {})
        daily = data.get("daily", {})
        t_max = (daily.get("temperature_2m_max") or [None])[0]
        t_min = (daily.get("temperature_2m_min") or [None])[0]
        rain = (daily.get("precipitation_probability_max") or [None])[0]
        wcode = int((daily.get("weathercode") or [0])[0])

        desc = _WMO.get(wcode, "❓")
        temp_now = cw.get("temperature", "?")
        range_str = f" ↑{t_max:.0f}°/↓{t_min:.0f}°" if t_max is not None and t_min is not None else ""
        rain_str = f" | 🌧 {rain:.0f}%" if rain and rain > 20 else ""
        return f"• {desc} {temp_now}°C{range_str}{rain_str}"
    except Exception as exc:
        logger.warning("morning_brief weather failed: %s", exc)
        return ""


async def _calendar(chat_id: str) -> str:
    try:
        from app.google.auth import get_credentials
        from googleapiclient.discovery import build  # type: ignore[import]

        creds = get_credentials(chat_id)
        if not creds or not creds.valid:
            return ""

        tz = ZoneInfo(USER_TIMEZONE)
        now = datetime.now(tz)
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)

        service = build("calendar", "v3", credentials=creds)
        result = await asyncio.to_thread(
            lambda: service.events().list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=end_of_day.isoformat(),
                maxResults=5,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
        )

        events = result.get("items", [])
        if not events:
            return "• אין אירועים היום"

        lines = []
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            try:
                dt = datetime.fromisoformat(start).astimezone(tz)
                time_str = dt.strftime("%H:%M")
            except Exception:
                time_str = ""
            title = e.get("summary", "(ללא כותרת)")
            lines.append(f"• {time_str} — {title}" if time_str else f"• {title}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("morning_brief calendar failed: %s", exc)
        return ""


async def _gmail(chat_id: str) -> str:
    try:
        from app.google.auth import get_credentials
        from googleapiclient.discovery import build  # type: ignore[import]

        creds = get_credentials(chat_id)
        if not creds or not creds.valid:
            return ""

        service = build("gmail", "v1", credentials=creds)
        result = await asyncio.to_thread(
            lambda: service.users().messages().list(
                userId="me", maxResults=5, labelIds=["INBOX", "UNREAD"]
            ).execute()
        )

        messages = result.get("messages", [])
        if not messages:
            return ""

        count = result.get("resultSizeEstimate", len(messages))
        subjects: list[str] = []
        for msg in messages[:3]:
            detail = await asyncio.to_thread(
                lambda m=msg: service.users().messages().get(
                    userId="me", id=m["id"], format="metadata",
                    metadataHeaders=["Subject"],
                ).execute()
            )
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            subj = (headers.get("Subject") or "(ללא נושא)")[:55]
            subjects.append(f"• {subj}")

        prefix = f"{count} שלא נקראו:" if count > 1 else "1 שלא נקראה:"
        return prefix + "\n" + "\n".join(subjects)
    except Exception as exc:
        logger.warning("morning_brief gmail failed: %s", exc)
        return ""


async def _fitness(chat_id: str) -> str:
    try:
        from app.fitness import generate_morning_brief
        return await generate_morning_brief(chat_id)
    except Exception as exc:
        logger.warning("morning_brief fitness failed: %s", exc)
        return ""


async def generate(chat_id: str) -> str:
    """Build the full morning brief. All sections fetched in parallel; missing ones are silently skipped."""
    tz = ZoneInfo(USER_TIMEZONE)
    now = datetime.now(tz)
    day_he = _DAY_HE[now.weekday()]
    header = f"🌅 *בוקר טוב!*  יום {day_he} {now.strftime('%d.%m.%Y')}"

    weather_r, cal_r, gmail_r, fitness_r = await asyncio.gather(
        _weather(),
        _calendar(chat_id),
        _gmail(chat_id),
        _fitness(chat_id),
        return_exceptions=True,
    )

    def _safe(v: object) -> str:
        return v if isinstance(v, str) else ""

    sections = [header]
    if w := _safe(weather_r):
        sections.append(f"\n🌤 *מזג אוויר — {USER_CITY}*\n{w}")
    if c := _safe(cal_r):
        sections.append(f"\n📅 *לוח שנה היום*\n{c}")
    if g := _safe(gmail_r):
        sections.append(f"\n📧 *מייל*\n{g}")
    if f := _safe(fitness_r):
        sections.append(f"\n💪 *כושר*\n{f}")

    return "\n".join(sections)
