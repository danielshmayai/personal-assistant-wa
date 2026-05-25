"""LangChain tools so danidin can log meals and report daily nutrition from chat."""

from __future__ import annotations

from langchain_core.tools import tool


def _fmt_micros(micros: dict) -> list[str]:
    """Render micro keys like 'iron_mg' → 'iron: 2.1 mg' as bullet lines."""
    lines = []
    for key, val in sorted(micros.items()):
        parts = key.rsplit("_", 1)
        if len(parts) == 2 and parts[1] in ("g", "mg", "ug", "kcal"):
            name, unit = parts[0].replace("_", " "), parts[1]
            unit = "µg" if unit == "ug" else unit
        else:
            name, unit = key.replace("_", " "), ""
        lines.append(f"  • {name}: {val} {unit}".rstrip())
    return lines


def get_nutrition_tools(chat_id: str) -> list:
    """Return nutrition tools bound to *chat_id*."""

    @tool
    async def log_meal(description: str) -> str:
        """Log a meal the user ate and estimate its nutrition (protein, carbs, calories + micronutrients).

        Use whenever the user reports eating something: "אכלתי חזה עוף עם אורז",
        "log my lunch: tuna salad", "תרשום שאכלתי 2 ביצים וטוסט".

        description: free-text description of the meal (any language).
        """
        from app import nutrition

        text = (description or "").strip()
        if not text:
            return "מה אכלת? צריך תיאור של הארוחה כדי לרשום אותה."
        try:
            parsed = await nutrition.parse_meal_text(text)
        except Exception as exc:
            return f"לא הצלחתי לנתח את הארוחה: {exc}"

        nutrition.insert_log(
            chat_id, parsed["meal_description"], parsed["protein"], parsed["carbs"],
            parsed["calories"], parsed["micros"], source="text",
        )
        totals = nutrition.list_today(chat_id)["totals"]
        return (
            f"✅ נרשם: {parsed['meal_description']}\n"
            f"חלבון {parsed['protein']:.0f} ג' · פחמימות {parsed['carbs']:.0f} ג' · "
            f"{parsed['calories']:.0f} קל'\n"
            f"— סה\"כ היום: חלבון {totals['protein']:.0f}/"
            f"{nutrition.PROTEIN_TARGET_G} ג', {totals['calories']:.0f} קל'."
        )

    @tool
    async def log_water(amount_ml: int) -> str:
        """Log water the user drank. Daily target is 2000 ml (2 litres).

        Use whenever the user mentions drinking water: "שתיתי כוס מים",
        "drank 500ml", "תרשום שתיית מים 300 מ\"ל".

        amount_ml: amount drunk in millilitres (e.g. 250 for a glass, 500 for a bottle).
        """
        from app import nutrition

        ml = max(1, int(amount_ml or 0))
        nutrition.log_water(chat_id, ml)
        total = nutrition.water_today(chat_id)
        remaining = max(0, nutrition.WATER_TARGET_ML - total)
        pct = min(100, round(total / nutrition.WATER_TARGET_ML * 100))
        status = "🎉 יעד המים הושג!" if remaining == 0 else f"נותרו {remaining} מ\"ל"
        return (
            f"💧 נרשם: {ml} מ\"ל מים\n"
            f"סה\"כ היום: {total} / {nutrition.WATER_TARGET_ML} מ\"ל ({pct}%) — {status}"
        )

    @tool
    async def nutrition_today() -> str:
        """Report how much the user has consumed today so far — protein vs the 110g target,
        carbs, calories, and every micronutrient logged (vitamins, minerals, fat, fiber, …).

        Use for "כמה חלבון אכלתי היום?", "how much iron did I have today?",
        "מה התזונה שלי היום?", "what have I eaten today?".
        """
        from app import nutrition

        data = nutrition.list_today(chat_id)
        meals = data["meals"]
        if not meals:
            return "עדיין לא רשמת ארוחות היום."

        t = data["totals"]
        target = data["protein_target"]
        remaining = max(0, target - t["protein"])
        lines = [f"*ארוחות היום ({len(meals)}):*"]
        for i, m in enumerate(meals, 1):
            lines.append(
                f"{i}. {m['meal_description']} "
                f"(חלבון {m['protein']:.0f}ג' · פחמימות {m['carbs']:.0f}ג' · {m['calories']:.0f} קל')"
            )
        water_ml = data.get("water_ml", 0)
        water_target = data.get("water_target_ml", 2000)
        water_remaining = max(0, water_target - water_ml)
        lines += [
            "",
            "*סה\"כ היום:*",
            f"• חלבון: {t['protein']:.0f} / {target} ג'  (נותרו {remaining:.0f} ג')",
            f"• פחמימות: {t['carbs']:.0f} ג'",
            f"• קלוריות: {t['calories']:.0f} קל'",
            f"• 💧 מים: {water_ml} / {water_target} מ\"ל  (נותרו {water_remaining} מ\"ל)",
        ]
        micro_lines = _fmt_micros(t["micros"])
        if micro_lines:
            lines.append("*מיקרו-נוטריינטים:*")
            lines.extend(micro_lines)
        return "\n".join(lines)

    return [log_meal, log_water, nutrition_today]
