"""LangChain tools so danidin can log workouts and report fitness data from chat."""
from __future__ import annotations

from langchain_core.tools import tool

_DELTA_LABELS = {"weight_kg": "משקל", "lbm_kg": "מסה רזה", "smm_kg": "מסת שריר", "bf_pct": "אחוז שומן"}


def _format_body_deltas(deltas: dict) -> str:
    """Hebrew one-liner of per-metric changes vs the previous measurement,
    with ✅/⚠️ direction markers relative to the goal (muscle up, fat down)."""
    if not deltas:
        return ""
    parts = []
    for k, d in deltas.items():
        arrow = "↑" if d["delta"] > 0 else ("↓" if d["delta"] < 0 else "→")
        mark = ""
        if "good" in d:
            mark = " ✅" if d["good"] else " ⚠️"
        parts.append(f"{_DELTA_LABELS.get(k, k)} {d['from']}→{d['to']} {arrow}{mark}")
    return "📈 מול המדידה הקודמת: " + " | ".join(parts)


def get_fitness_tools(chat_id: str) -> list:
    """Return fitness tools bound to *chat_id*."""

    @tool
    async def log_workout(description: str) -> str:
        """Log a workout the user completed. Parses exercises, estimates volume, and gives progressive-overload targets.

        Use whenever the user reports a workout: "אימון היום: לג פרס 3x12 80 קג, RPE 7",
        "log my workout: bench press 3x8 60kg", "תרשום אימון: ריצה 5 ק\"מ 30 דקות",
        "עשיתי אימון כוח היום", "finished my workout", "סיימתי אימון".

        description: free-text description of the workout (exercises, sets, reps, weight, RPE, duration).
        """
        from app import fitness

        text = (description or "").strip()
        if not text:
            return "מה עשית באימון? צריך תיאור כדי לרשום אותו."
        try:
            parsed = await fitness.parse_workout_text(text, chat_id)
        except Exception as exc:
            return f"לא הצלחתי לנתח את האימון: {exc}"

        workout_id = fitness.insert_workout(
            chat_id=chat_id,
            workout_type=parsed["workout_type"],
            title=parsed["title"],
            duration_min=parsed["duration_min"],
            avg_rpe=parsed["avg_rpe"],
            exercises=parsed["exercises"],
            metrics=parsed["metrics"],
            source="text",
        )
        try:
            evaluation = await fitness.evaluate_workout(parsed, chat_id)
            fitness.update_workout_ai(workout_id, evaluation["ai_summary"], evaluation["ai_next_rec"])
        except Exception as exc:
            evaluation = {"ai_summary": "", "ai_next_rec": {}}
            import logging
            logging.getLogger("pa.fitness").warning("evaluate_workout failed: %s", exc)

        today = fitness.list_today(chat_id)
        return fitness.format_workout_log_message(parsed, evaluation, today)

    @tool
    async def suggest_workout(minutes: int = 45, workout_type: str = "strength") -> str:
        """Suggest a workout plan without logging it. Adapts to history and progressive-overload targets.

        Use when the user asks for a workout suggestion: "תציע לי אימון של 30 דקות",
        "suggest a workout", "מה כדאי לעשות היום?", "give me a 45-min strength workout",
        "what should I train today?", "תכנן לי אימון כוח".

        minutes: desired duration (default 45).
        workout_type: strength | hiit | cardio | running | swimming | other (default strength).
        """
        from app import fitness

        try:
            plan = await fitness.suggest_workout(chat_id, minutes=minutes, workout_type=workout_type)
        except Exception as exc:
            return f"לא הצלחתי לתכנן אימון: {exc}"

        lines = [
            f"🏋️ *{plan['title']}* ({plan['duration_min']} דקות)",
        ]
        if plan.get("rationale"):
            lines.append(f"_{plan['rationale']}_")
        lines.append("")
        supersets = plan.get("supersets") or []
        superset_map: dict[str, str] = {}
        for i, pair in enumerate(supersets, 1):
            label = f"Superset {i}"
            for name in pair:
                superset_map[name.lower()] = label

        for ex in plan.get("exercises") or []:
            name = ex["name"]
            tag = superset_map.get(name.lower(), "")
            tag_str = f" [{tag}]" if tag else ""
            if ex.get("duration_sec") and ex["duration_sec"] > 0:
                lines.append(f"• {name}{tag_str}: {ex['sets']} × {ex['duration_sec']}שנ' @ RPE {ex['rpe']:.0f}")
            else:
                weight_str = f" {ex['weight_kg']} קג" if ex.get("weight_kg") else ""
                lines.append(f"• {name}{tag_str}: {ex['sets']}×{ex['reps']}{weight_str} @ RPE {ex['rpe']:.0f}")
        lines.append(f"\nנפח מוערך: {plan['total_volume']:.0f} קג")
        lines.append("_שלח 'תרשום את האימון הזה' אחרי שתסיים._")
        return "\n".join(lines)

    @tool
    async def fitness_today() -> str:
        """Report today's workout summary — sessions, volume, duration, and recovery status.

        Use for: "כמה אימנתי היום?", "what did I train today?", "הצג אימוני היום",
        "show today's fitness", "מה הנפח שלי היום?".
        """
        from app import fitness

        data = fitness.list_today(chat_id)
        if not data["workouts"]:
            return "לא רשמת אימון היום עדיין. 💪"

        lines = [f"*אימוני היום ({len(data['workouts'])}):*"]
        for w in data["workouts"]:
            lines.append(
                f"• {w['title']} ({w['workout_type']}) — {w['duration_min']:.0f} דקות, "
                f"RPE {w['avg_rpe']:.1f}, נפח {w['total_volume']:.0f} קג"
            )
            if w.get("ai_summary"):
                lines.append(f"  💡 {w['ai_summary']}")
        lines += [
            "",
            f"*סה\"כ:* {data['total_duration_min']:.0f} דקות | נפח {data['total_volume']:.0f} קג",
        ]
        return "\n".join(lines)

    @tool
    async def fitness_history(days: int = 14) -> str:
        """Report past workout sessions (not just today) — for questions about a specific
        previous session, weekly summaries, or workouts auto-imported from a Garmin watch.

        Use for: "מה דעתך על האימון אופניים אחרון שעשיתי?", "how was my last workout?",
        "תסכם לי את האימונים מהשבוע שעבר", "מתי התאמנתי לאחרונה?",
        "when did I last train legs?", "show my workout history",
        "מה היה קצב הלב שלי באימון האחרון?".

        days: how many days back to look (default 14, max 60).
        """
        from app import fitness

        days = max(1, min(int(days or 14), 60))
        day_rows = fitness.history(chat_id, days=days)
        sessions = []
        for day in day_rows:
            for s in (day.get("sessions") or []):
                s = dict(s)
                s["date"] = day["date"]
                sessions.append(s)
        if not sessions:
            return f"לא נמצאו אימונים ב-{days} הימים האחרונים."

        sessions.sort(key=lambda s: s["date"], reverse=True)
        lines = [f"*אימונים ב-{days} הימים האחרונים ({len(sessions)}):*"]
        for s in sessions:
            m = s.get("metrics") or {}
            src_tag = " ⌚" if s.get("source") == "garmin" else ""
            detail_parts = [f"{s.get('duration_min', 0):.0f} דקות"]
            if s.get("exercises"):
                detail_parts.append(f"נפח {s.get('total_volume', 0):.0f} קג")
            if s.get("avg_rpe"):
                detail_parts.append(f"RPE {s['avg_rpe']:.1f}")
            if m.get("distance_km"):
                detail_parts.append(f"{m['distance_km']:.1f} ק\"מ")
            if m.get("hr_avg"):
                detail_parts.append(f"דופק ממוצע {m['hr_avg']:.0f}")
            if m.get("calories"):
                detail_parts.append(f"{m['calories']:.0f} קלוריות")
            lines.append(f"\n📅 {s['date']} — *{s.get('title', '')}*{src_tag} ({s.get('workout_type', '')})")
            lines.append("• " + " | ".join(detail_parts))
            if s.get("ai_summary"):
                lines.append(f"💡 {s['ai_summary']}")
        return "\n".join(lines)

    @tool
    async def log_body_metrics(
        weight_kg: float | None = None,
        lbm_kg: float | None = None,
        smm_kg: float | None = None,
        bf_pct: float | None = None,
    ) -> str:
        """Log body composition metrics (weight, lean body mass, skeletal muscle mass, body fat %).

        Use when the user reports body measurements: "שקלתי היום 72 קג",
        "log my weight: 71.5 kg", "אחוז שומן 21%", "המסה הרזה שלי 32 קג",
        "update my body metrics: weight 70kg, BF 20%".

        All parameters are optional — pass only what the user mentioned.
        weight_kg: total body weight in kg.
        lbm_kg: lean body mass in kg.
        smm_kg: skeletal muscle mass in kg.
        bf_pct: body fat percentage (0-100).
        """
        from app import fitness

        if all(v is None for v in [weight_kg, lbm_kg, smm_kg, bf_pct]):
            return "לא ציינת אף מדד. אנא ציין משקל, מסה רזה, SMM, או אחוז שומן."

        metric_id = fitness.insert_body_metric(
            chat_id=chat_id,
            weight_kg=weight_kg,
            lbm_kg=lbm_kg,
            smm_kg=smm_kg,
            bf_pct=bf_pct,
            source="manual",
        )
        parts = []
        if weight_kg is not None:
            parts.append(f"משקל: {weight_kg} קג")
        if lbm_kg is not None:
            parts.append(f"מסה רזה: {lbm_kg} קג")
        if smm_kg is not None:
            parts.append(f"SMM: {smm_kg} קג")
        if bf_pct is not None:
            parts.append(f"אחוז שומן: {bf_pct}%")
        lines = ["✅ נרשם: " + " | ".join(parts)]
        try:
            analysis = await fitness.evaluate_body_metrics(
                {"weight_kg": weight_kg, "lbm_kg": lbm_kg, "smm_kg": smm_kg,
                 "bf_pct": bf_pct, "extras": {}},
                chat_id,
            )
            fitness.update_body_metric_ai(metric_id, analysis["ai_summary"])
            lines.append(_format_body_deltas(analysis.get("deltas") or {}))
            if analysis.get("ai_summary"):
                lines.append(f"\n💡 {analysis['ai_summary']}")
        except Exception:
            import logging
            logging.getLogger("pa.fitness").warning("body metrics evaluation failed", exc_info=True)
        return "\n".join(l for l in lines if l)

    @tool
    async def analyze_body_scan(message_id: str) -> str:
        """Analyze a photo of a computerized body-composition summary sheet (InBody / Tanita /
        gym scale report) the user sent in WhatsApp. Extracts ALL readable metrics (weight,
        lean mass, muscle mass, BF%, BMR, visceral fat…), saves them as a NEW measurement
        (old values are kept for trend tracking), and returns an AI trend analysis vs the
        previous measurement.

        Use when a [MEDIA type=image …] tag arrives and the caption/context suggests a body
        scan, weight report, InBody sheet, "דף סיכום", "מדדים", "שקילה", "הרכב גוף".
        message_id comes from the [MEDIA ...] context tag.
        """
        from app import fitness
        from app.google.drive_tools import _download_from_waha

        try:
            data, mime = await _download_from_waha(message_id)
        except Exception as exc:
            return f"לא הצלחתי להוריד את התמונה: {exc}"
        try:
            parsed = await fitness.parse_body_scan_image(data, mime, chat_id)
        except Exception as exc:
            return f"לא הצלחתי לקרוא נתונים מדף הסיכום: {exc}"

        metric_id = fitness.insert_body_metric(
            chat_id=chat_id,
            weight_kg=parsed["weight_kg"],
            lbm_kg=parsed["lbm_kg"],
            smm_kg=parsed["smm_kg"],
            bf_pct=parsed["bf_pct"],
            source="scan",
            note="imported from body-scan image",
            log_date=parsed.get("measured_date"),
            extras=parsed.get("extras") or {},
        )

        labels = [("weight_kg", "משקל", "קג"), ("lbm_kg", "מסה רזה", "קג"),
                  ("smm_kg", "מסת שריר", "קג"), ("bf_pct", "אחוז שומן", "%")]
        vals = [f"{name}: {parsed[k]}{unit}" for k, name, unit in labels if parsed.get(k) is not None]
        extras = parsed.get("extras") or {}
        extra_labels = {"bmi": "BMI", "bmr_kcal": "BMR (קק\"ל)", "visceral_fat_level": "שומן ויסצרלי",
                        "total_body_water_l": "מים (ל')", "protein_kg": "חלבון (קג)",
                        "minerals_kg": "מינרלים (קג)", "inbody_score": "ציון InBody",
                        "waist_hip_ratio": "יחס מותן-ירך", "body_fat_kg": "מסת שומן (קג)",
                        "target_weight_kg": "משקל יעד (קג)", "smi": "SMI",
                        "recommended_kcal": "צריכה מומלצת (קק\"ל)"}
        extra_vals = [f"{extra_labels.get(k, k)}: {v}" for k, v in extras.items() if k in extra_labels]

        lines = ["📊 *דף הסיכום נקרא ונשמר כמדידה חדשה*", "• " + " | ".join(vals)]
        if extra_vals:
            lines.append("• " + " | ".join(extra_vals))
        if extras.get("segmental_notes"):
            lines.append(f"• {extras['segmental_notes']}")

        try:
            analysis = await fitness.evaluate_body_metrics(parsed, chat_id)
            fitness.update_body_metric_ai(metric_id, analysis["ai_summary"])
            lines.append(_format_body_deltas(analysis.get("deltas") or {}))
            if analysis.get("ai_summary"):
                lines.append(f"\n💡 {analysis['ai_summary']}")
        except Exception:
            import logging
            logging.getLogger("pa.fitness").warning("body scan evaluation failed", exc_info=True)
        return "\n".join(l for l in lines if l)

    @tool
    async def fitness_morning_brief() -> str:
        """Generate today's personalized fitness morning brief in Hebrew — weekly compliance, recovery, today's recommendation.

        Use for: "תן לי תדרוך בוקר לכושר", "מה האימון המומלץ היום?",
        "fitness morning brief", "כמה אימנתי השבוע?", "תסכם את האימונים שלי השבוע".
        """
        from app import fitness

        try:
            brief = await fitness.generate_morning_brief(chat_id)
            return brief
        except Exception as exc:
            return f"לא הצלחתי לייצר תדרוך בוקר: {exc}"

    return [log_workout, suggest_workout, fitness_today, fitness_history, log_body_metrics, analyze_body_scan, fitness_morning_brief]
