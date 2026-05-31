"""Fixed physiological profile and clinical constraints for fitness module.

CLINICAL_CONSTRAINTS are hardcoded and never overridden by user input.
Body metrics (weight/LBM/SMM/BF%) are tracked in fitness_body_metrics table.
"""
from __future__ import annotations

CLINICAL_CONSTRAINTS = """\
CLINICAL CONSTRAINTS (mandatory — never override):
1. Gilbert's Syndrome: requires constant hydration (≥2.5L water/day); NO extended fasting (>14h), NO extreme caloric deficit; hepatic stress from overexertion must be avoided.
2. Right neck-to-scapula pain: AVOID heavy axial/compressive loading — no heavy barbell back squat, no heavy conventional deadlift, no military press/overhead press with weight. Emphasize scapular stabilizer work (face pulls, band pull-aparts, serratus wall slides, prone Y/T/W). Maintain neutral neck throughout all exercises. Prefer machine/cable work over heavy free-bar axial loading.
3. Recovery: allow at least 48h between sessions targeting the same muscle group. Monitor RPE — if consecutive sessions average RPE ≥8.5 suggest a deload week."""

PHYSIO_BASELINE = {
    "sex": "Male",
    "age_years": 48,
    "height_cm": 177,
    "hba1c_pct": 4.4,
    "vo2_base": "high (CPET-confirmed)",
    "phenotype": "skinny-fat",
    "ref_weight_kg": 71.1,
    "ref_lbm_kg": 31.8,
    "ref_smm_kg": 17.9,
    "ref_bf_pct": 22.0,
}

GOAL = "Build lean muscle mass, reduce body fat %, preserve aerobic base. Primary metric: SMM (skeletal muscle mass) gain. Secondary: BF% reduction. Aerobic base: maintain VO2 capacity, do not sacrifice for hypertrophy."


def render_profile_block(latest_metrics: dict | None = None) -> str:
    """Render the full profile block prepended into every fitness LLM call."""
    m = latest_metrics or {}

    weight = m.get("weight_kg") or PHYSIO_BASELINE["ref_weight_kg"]
    lbm    = m.get("lbm_kg")    or PHYSIO_BASELINE["ref_lbm_kg"]
    smm    = m.get("smm_kg")    or PHYSIO_BASELINE["ref_smm_kg"]
    bf     = m.get("bf_pct")    or PHYSIO_BASELINE["ref_bf_pct"]
    source = "(current)" if latest_metrics else "(baseline — no recent measurement)"

    return f"""\
=== ATHLETE PROFILE ===
Sex: {PHYSIO_BASELINE['sex']} | Age: {PHYSIO_BASELINE['age_years']}y | Height: {PHYSIO_BASELINE['height_cm']} cm
HbA1c: {PHYSIO_BASELINE['hba1c_pct']}% | VO2 base: {PHYSIO_BASELINE['vo2_base']} | Phenotype: {PHYSIO_BASELINE['phenotype']}

Body composition {source}:
  Weight: {weight} kg | LBM: {lbm} kg | SMM: {smm} kg | BF: {bf}%

Goal: {GOAL}

{CLINICAL_CONSTRAINTS}
=== END PROFILE ==="""
