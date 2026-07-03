"""Push an AI workout plan to Garmin as a structured strength workout (best-effort).

Uses the unofficial /workout-service/workout endpoint. Exercise names are matched to
Garmin FIT exercise categories from the English part of the plan's exercise names;
unmatched exercises still push as generic strength steps (sets/reps/rest intact).
"""
from __future__ import annotations

import logging
import re

from app.garmin import client as gclient

logger = logging.getLogger("pa.garmin")

_REST_SEC_DEFAULT = 60

# Order matters: more specific patterns first (e.g. "leg curl" before "curl").
_EX_MAP: list[tuple[re.Pattern, str, str | None]] = [
    (re.compile(r"leg.?press|hack.?squat", re.I),            "SQUAT", "LEG_PRESS"),
    (re.compile(r"leg.?curl|hamstring.?curl", re.I),          "LEG_CURL", None),
    (re.compile(r"leg.?extension", re.I),                     "SQUAT", None),
    (re.compile(r"squat|goblet", re.I),                       "SQUAT", None),
    (re.compile(r"romanian|rdl", re.I),                       "DEADLIFT", "ROMANIAN_DEADLIFT"),
    (re.compile(r"deadlift", re.I),                           "DEADLIFT", None),
    (re.compile(r"bench.?press|chest.?press", re.I),          "BENCH_PRESS", None),
    (re.compile(r"push.?up", re.I),                           "PUSH_UP", None),
    (re.compile(r"pull.?up|chin.?up|pull.?down", re.I),       "PULL_UP", None),
    (re.compile(r"face.?pull|row", re.I),                     "ROW", None),
    (re.compile(r"shoulder.?press|overhead.?press|arnold|military", re.I), "SHOULDER_PRESS", None),
    (re.compile(r"lateral.?raise|side.?raise", re.I),         "LATERAL_RAISE", None),
    (re.compile(r"tricep|pushdown|skull", re.I),              "TRICEPS_EXTENSION", None),
    (re.compile(r"hammer.?curl|bicep|curl", re.I),            "CURL", None),
    (re.compile(r"lunge|split.?squat|bulgarian|step.?up", re.I), "LUNGE", None),
    (re.compile(r"calf", re.I),                               "CALF_RAISE", None),
    (re.compile(r"plank", re.I),                              "PLANK", None),
    (re.compile(r"crunch|sit.?up", re.I),                     "CRUNCH", None),
    (re.compile(r"fly|flye|pec.?deck|crossover", re.I),       "FLYE", None),
    (re.compile(r"hip.?thrust|glute.?bridge|hip.?raise", re.I), "HIP_RAISE", None),
    (re.compile(r"shrug", re.I),                              "SHRUG", None),
    (re.compile(r"hyper.?extension|back.?extension", re.I),   "HYPEREXTENSION", None),
    (re.compile(r"farmer|carry", re.I),                       "CARRY", None),
    (re.compile(r"dead.?bug|bird.?dog|hollow|core", re.I),    "CORE", None),
]

_STRENGTH_SPORT = {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 9}
_STEP_INTERVAL = {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3}
_STEP_REST = {"stepTypeId": 5, "stepTypeKey": "rest", "displayOrder": 5}
_STEP_REPEAT = {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6}
_END_REPS = {"conditionTypeId": 10, "conditionTypeKey": "reps", "displayOrder": 10, "displayable": True}
_END_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
_END_LAP = {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True}
_UNIT_KG = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}


def _english_part(name: str) -> str:
    """All latin words from a possibly Hebrew+parenthesized exercise name.

    English can sit inside the parentheses ("לג פרס (Leg Press)") or outside them
    ("Face Pull (Cable)"), so keep latin text from the whole string."""
    return re.sub(r"[^A-Za-z0-9\s-]", " ", name or "")


def _match_exercise(name: str) -> tuple[str | None, str | None]:
    eng = _english_part(name)
    for pat, category, ex_name in _EX_MAP:
        if pat.search(eng):
            return category, ex_name
    return None, None


def build_payload(plan: dict) -> dict:
    """Map a suggest-workout plan to a Garmin workout-service payload."""
    steps: list[dict] = []
    order = 1
    child_id = 1

    for ex in plan.get("exercises") or []:
        name = str(ex.get("name") or "")
        sets = max(1, int(ex.get("sets") or 3))
        reps = int(ex.get("reps") or 10)
        dur = int(ex.get("duration_sec") or 0)
        weight_kg = float(ex.get("weight_kg") or 0)
        category, ex_name = _match_exercise(name)

        work_step: dict = {
            "type": "ExecutableStepDTO",
            "stepOrder": order + 1,
            "stepType": dict(_STEP_INTERVAL),
            "childStepId": child_id,
            "description": name[:512],
        }
        if dur > 0:
            work_step["endCondition"] = dict(_END_TIME)
            work_step["endConditionValue"] = float(dur)
        else:
            work_step["endCondition"] = dict(_END_REPS)
            work_step["endConditionValue"] = float(reps)
        if category:
            work_step["category"] = category
            if ex_name:
                work_step["exerciseName"] = ex_name
        if weight_kg > 0:
            work_step["weightValue"] = weight_kg * 1000.0  # grams
            work_step["weightUnit"] = dict(_UNIT_KG)

        rest_step = {
            "type": "ExecutableStepDTO",
            "stepOrder": order + 2,
            "stepType": dict(_STEP_REST),
            "childStepId": child_id,
            "endCondition": dict(_END_TIME),
            "endConditionValue": float(_REST_SEC_DEFAULT),
        }

        steps.append({
            "type": "RepeatGroupDTO",
            "stepOrder": order,
            "stepType": dict(_STEP_REPEAT),
            "childStepId": child_id,
            "numberOfIterations": sets,
            "smartRepeat": False,
            "workoutSteps": [work_step, rest_step],
        })
        order += 3
        child_id += 1

    title = str(plan.get("title") or "אימון danidin")[:80]
    return {
        "workoutName": title,
        "description": str(plan.get("rationale") or "")[:1024],
        "sportType": dict(_STRENGTH_SPORT),
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": dict(_STRENGTH_SPORT),
            "workoutSteps": steps,
        }],
    }


async def push_workout(plan: dict, schedule_today: bool = True) -> dict:
    """Create the workout in Garmin Connect and schedule it for today."""
    if not (plan.get("exercises") or []):
        raise ValueError("Plan has no exercises")
    payload = build_payload(plan)
    created = await gclient.call("connectapi", "/workout-service/workout", method="POST", json=payload)
    workout_id = (created or {}).get("workoutId")
    if not workout_id:
        raise RuntimeError(f"Garmin did not return a workoutId: {str(created)[:300]}")

    scheduled = False
    if schedule_today:
        try:
            from app.fitness import _user_today
            await gclient.call("schedule_workout", str(workout_id), _user_today().isoformat())
            scheduled = True
        except Exception:
            logger.warning("garmin schedule_workout failed (workout still in library)", exc_info=True)

    return {"workoutId": workout_id, "scheduled": scheduled,
            "matched": sum(1 for ex in plan["exercises"] if _match_exercise(str(ex.get("name") or ""))[0])}
