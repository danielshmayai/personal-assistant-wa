"""Garmin sync: daily wellness snapshot, activity import/merge, hydration push."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

import psycopg2

from app.config import DATABASE_URL
from app.garmin import client as gclient

logger = logging.getLogger("pa.garmin")

_WELLNESS_TTL_MIN = 30
_ACTIVITY_LOOKBACK_DAYS = 3
_MERGE_WINDOW_H = 2


def _user_today():
    from app.fitness import _user_today as ut
    return ut()


# ── Wellness ────────────────────────────────────────────────────────────────

def get_wellness(log_date) -> dict | None:
    """Read a wellness row from the DB (no network). Returns None when absent."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT log_date, sleep_score, sleep_min, bb_high, bb_low, bb_latest,
                          stress_avg, resting_hr, steps, synced_at
                   FROM garmin_wellness WHERE log_date = %s""",
                (log_date,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "date": row[0].isoformat(),
            "sleep_score": row[1], "sleep_min": row[2],
            "bb_high": row[3], "bb_low": row[4], "bb_latest": row[5],
            "stress_avg": row[6], "resting_hr": row[7], "steps": row[8],
            "synced_at": row[9].isoformat() if row[9] else None,
        }
    finally:
        conn.close()


def _upsert_wellness(log_date, vals: dict, raw: dict) -> None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO garmin_wellness
                     (log_date, sleep_score, sleep_min, bb_high, bb_low, bb_latest,
                      stress_avg, resting_hr, steps, raw, synced_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NOW())
                   ON CONFLICT (log_date) DO UPDATE SET
                     sleep_score = COALESCE(EXCLUDED.sleep_score, garmin_wellness.sleep_score),
                     sleep_min   = COALESCE(EXCLUDED.sleep_min, garmin_wellness.sleep_min),
                     bb_high     = COALESCE(EXCLUDED.bb_high, garmin_wellness.bb_high),
                     bb_low      = COALESCE(EXCLUDED.bb_low, garmin_wellness.bb_low),
                     bb_latest   = COALESCE(EXCLUDED.bb_latest, garmin_wellness.bb_latest),
                     stress_avg  = COALESCE(EXCLUDED.stress_avg, garmin_wellness.stress_avg),
                     resting_hr  = COALESCE(EXCLUDED.resting_hr, garmin_wellness.resting_hr),
                     steps       = COALESCE(EXCLUDED.steps, garmin_wellness.steps),
                     raw = EXCLUDED.raw, synced_at = NOW()""",
                (log_date, vals.get("sleep_score"), vals.get("sleep_min"),
                 vals.get("bb_high"), vals.get("bb_low"), vals.get("bb_latest"),
                 vals.get("stress_avg"), vals.get("resting_hr"), vals.get("steps"),
                 json.dumps(raw, default=str)[:200000]),
            )
        conn.commit()
    finally:
        conn.close()


def _int_or_none(v):
    try:
        return int(round(float(v))) if v is not None else None
    except (TypeError, ValueError):
        return None


async def sync_wellness(force: bool = False) -> dict | None:
    """Pull today's wellness from Garmin (30-min staleness guard) and upsert."""
    today = _user_today()
    cached = await asyncio.to_thread(get_wellness, today)
    if cached and not force and cached.get("synced_at"):
        age = datetime.now().astimezone() - datetime.fromisoformat(cached["synced_at"])
        if age < timedelta(minutes=_WELLNESS_TTL_MIN):
            return cached

    iso = today.isoformat()
    vals: dict = {}
    raw: dict = {}

    # Daily summary: steps, resting HR, Body Battery, stress (one call covers most fields).
    try:
        stats = await gclient.call("get_stats", iso)
        raw["stats"] = stats
        vals.update({
            "steps": _int_or_none(stats.get("totalSteps")),
            "resting_hr": _int_or_none(stats.get("restingHeartRate")),
            "bb_high": _int_or_none(stats.get("bodyBatteryHighestValue")),
            "bb_low": _int_or_none(stats.get("bodyBatteryLowestValue")),
            "bb_latest": _int_or_none(stats.get("bodyBatteryMostRecentValue")),
            "stress_avg": _int_or_none(stats.get("averageStressLevel")),
        })
        if stats.get("sleepingSeconds"):
            vals["sleep_min"] = _int_or_none(stats["sleepingSeconds"] / 60)
    except gclient.GarminNotConnected:
        raise
    except Exception:
        logger.warning("garmin get_stats failed", exc_info=True)

    # Sleep score (not part of the daily summary).
    try:
        sleep = await gclient.call("get_sleep_data", iso)
        raw["sleep"] = {k: sleep.get(k) for k in ("dailySleepDTO",)} if isinstance(sleep, dict) else None
        dto = (sleep or {}).get("dailySleepDTO") or {}
        score = ((dto.get("sleepScores") or {}).get("overall") or {}).get("value")
        vals["sleep_score"] = _int_or_none(score)
        if dto.get("sleepTimeSeconds"):
            vals["sleep_min"] = _int_or_none(dto["sleepTimeSeconds"] / 60)
    except gclient.GarminNotConnected:
        raise
    except Exception:
        logger.warning("garmin get_sleep_data failed", exc_info=True)

    if any(v is not None for v in vals.values()):
        await asyncio.to_thread(_upsert_wellness, today, vals, raw)
    return await asyncio.to_thread(get_wellness, today)


# ── Activity import / merge ─────────────────────────────────────────────────

def _existing_garmin_ids(since_date) -> set:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT metrics->>'garmin_activity_id' FROM fitness_workouts
                   WHERE log_date >= %s AND metrics ? 'garmin_activity_id'""",
                (since_date,),
            )
            return {r[0] for r in cur.fetchall() if r[0]}
    finally:
        conn.close()


def _find_merge_candidate(log_date, start_dt) -> int | None:
    """An app-logged workout on the same date, near the activity start, without a garmin id."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, created_at FROM fitness_workouts
                   WHERE log_date = %s AND source != 'garmin'
                     AND NOT (metrics ? 'garmin_activity_id')
                   ORDER BY created_at DESC""",
                (log_date,),
            )
            rows = cur.fetchall()
        for wid, created_at in rows:
            if created_at is None:
                continue
            delta = abs((created_at.replace(tzinfo=None) - start_dt).total_seconds())
            # created_at ≈ session end; allow a generous window around the activity start.
            if delta <= _MERGE_WINDOW_H * 3600 * 1.5:
                return wid
        return None
    finally:
        conn.close()


def _float_or_none(v):
    try:
        return round(float(v), 2) if v is not None else None
    except (TypeError, ValueError):
        return None


def _extract_activity_metrics(act: dict, type_key: str, duration_min: float) -> dict:
    """Pull the richest available fields from a Garmin activity summary so evaluate_workout
    has enough to actually analyze the session (not just HR/calories)."""
    distance_km = _float_or_none((act.get("distance") or 0) / 1000) or None
    is_run_walk = any(k in type_key for k in ("running", "walking", "hiking"))
    is_cycling = "cycling" in type_key or "biking" in type_key
    m = {
        "garmin_activity_id": str(act.get("activityId") or ""),
        "hr_avg": _int_or_none(act.get("averageHR")),
        "hr_max": _int_or_none(act.get("maxHR")),
        "calories": _int_or_none(act.get("calories")),
        "distance_km": distance_km,
        "elevation_gain_m": _int_or_none(act.get("elevationGain")),
        "training_effect_aerobic": _float_or_none(act.get("aerobicTrainingEffect")),
        "training_effect_anaerobic": _float_or_none(act.get("anaerobicTrainingEffect")),
        "steps": _int_or_none(act.get("steps")),
    }
    if is_run_walk and distance_km:
        m["pace_min_km"] = _float_or_none(duration_min / distance_km)
    elif is_cycling and act.get("averageSpeed"):
        m["avg_speed_kmh"] = _float_or_none(act["averageSpeed"] * 3.6)
    return {k: v for k, v in m.items() if v is not None}


async def _evaluate_and_save(workout_id: int, workout_for_eval: dict) -> None:
    """Run the same AI-evaluation every other logging path gets, so Garmin-imported
    and Garmin-enriched workouts also get an ai_summary + next-session targets."""
    from app import fitness
    try:
        evaluation = await fitness.evaluate_workout(workout_for_eval, "default")
        await asyncio.to_thread(
            fitness.update_workout_ai, workout_id,
            evaluation["ai_summary"], evaluation["ai_next_rec"],
        )
    except Exception:
        logger.warning("garmin activity evaluate_workout failed for id=%s", workout_id, exc_info=True)


async def sync_activities() -> dict:
    """Import watch-recorded activities from the last few days; merge or insert.

    Every imported/enriched workout is also run through evaluate_workout so it gets an
    ai_summary and next-session targets, same as manually logged sessions.
    """
    from app import fitness

    today = _user_today()
    start = today - timedelta(days=_ACTIVITY_LOOKBACK_DAYS)
    try:
        activities = await gclient.call(
            "get_activities_by_date", start.isoformat(), today.isoformat()
        )
    except gclient.GarminNotConnected:
        raise
    except Exception:
        logger.warning("garmin get_activities_by_date failed", exc_info=True)
        return {"imported": 0, "merged": 0}

    known = await asyncio.to_thread(_existing_garmin_ids, start)
    imported = merged = 0

    for act in activities or []:
        try:
            aid = str(act.get("activityId") or "")
            if not aid or aid in known:
                continue
            start_local = str(act.get("startTimeLocal") or "")[:19]
            try:
                start_dt = datetime.fromisoformat(start_local)
            except ValueError:
                continue
            act_date = start_dt.date()
            type_key = ((act.get("activityType") or {}).get("typeKey") or "").lower()
            workout_type = "strength" if "strength" in type_key else "cardio"
            duration_min = round((act.get("duration") or 0) / 60, 1)
            title = act.get("activityName") or "אימון מהשעון"
            garmin_metrics = _extract_activity_metrics(act, type_key, duration_min)

            wid = await asyncio.to_thread(_find_merge_candidate, act_date, start_dt)
            if wid:
                await asyncio.to_thread(fitness.merge_garmin_metrics, wid, garmin_metrics)
                merged += 1
                merged_workout = await asyncio.to_thread(fitness.get_workout, wid, "default")
                if merged_workout:
                    await _evaluate_and_save(wid, merged_workout)
            else:
                new_id = await asyncio.to_thread(
                    fitness.insert_workout,
                    "default", workout_type, title, duration_min,
                    0, [], garmin_metrics, "garmin", act_date,
                )
                imported += 1
                await _evaluate_and_save(new_id, {
                    "title": title, "workout_type": workout_type,
                    "duration_min": duration_min, "avg_rpe": 0,
                    "exercises": [], "metrics": garmin_metrics,
                })
            known.add(aid)
        except Exception:
            logger.warning("garmin activity import failed for one activity", exc_info=True)

    if imported or merged:
        logger.info("garmin activities: imported=%d merged=%d", imported, merged)
    return {"imported": imported, "merged": merged}


# ── Hydration push ──────────────────────────────────────────────────────────

async def push_hydration(ml: int) -> None:
    """Fire-and-forget: mirror app-logged water into Garmin hydration tracking."""
    if ml <= 0 or not gclient.is_connected():
        return
    try:
        await gclient.call(
            "add_hydration_data", value_in_ml=ml, cdate=_user_today().isoformat()
        )
    except Exception:
        logger.debug("garmin hydration push failed", exc_info=True)
