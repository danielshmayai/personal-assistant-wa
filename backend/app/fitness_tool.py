"""LangChain tools so danidin can log workouts and report fitness data from chat."""
from __future__ import annotations

from langchain_core.tools import tool


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
        lines = [
            f"✅ *{parsed['title']}* נרשם",
            f"• סוג: {parsed['workout_type']} | {parsed['duration_min']:.0f} דקות | RPE {parsed['avg_rpe']:.1f}",
            f"• נפח: {parsed.get('total_volume', fitness._total_volume(parsed['exercises'])):.0f} קג",
        ]
        if evaluation["ai_summary"]:
            lines.append(f"\n💡 {evaluation['ai_summary']}")
        targets = (evaluation.get("ai_next_rec") or {}).get("targets") or []
        if targets:
            lines.append("\n*יעדים לאימון הבא:*")
            for t in targets[:3]:
                lines.append(f"• {t['name']}: {t.get('target_weight_kg', '?')} קג × {t.get('target_reps', '?')}")
        lines.append(f"\n— סה\"כ היום: {today['total_duration_min']:.0f} דקות, נפח {today['total_volume']:.0f} קג")
        return "\n".join(lines)

    @tool
    async def suggest_workout(minutes: int = 45, workout_type: str = "strength") -> str:
        """Suggest a workout plan without logging it. Adapts to history and progressive-overload targets.

        Use when the user asks for a workout suggestion: "תציע לי אימון של 30 דקות",
        "suggest a workout", "מה כדאי לעשות היום?", "give me a 45-min strength workout",
        "what should I train today?", "תכנן לי אימון כוח".

        minutes: desired duration (default 45).
        workout_type: strength | hiit | cardio | running | other (default strength).
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

        fitness.insert_body_metric(
            chat_id=chat_id,
            weight_kg=weight_kg,
            lbm_kg=lbm_kg,
            smm_kg=smm_kg,
            bf_pct=bf_pct,
            source="manual",
        )
        latest = fitness.latest_body_metrics(chat_id) or {}
        parts = []
        if weight_kg is not None:
            parts.append(f"משקל: {weight_kg} קג")
        if lbm_kg is not None:
            parts.append(f"מסה רזה: {lbm_kg} קג")
        if smm_kg is not None:
            parts.append(f"SMM: {smm_kg} קג")
        if bf_pct is not None:
            parts.append(f"אחוז שומן: {bf_pct}%")
        return "✅ נרשם: " + " | ".join(parts)

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

    return [log_workout, suggest_workout, fitness_today, log_body_metrics, fitness_morning_brief]
