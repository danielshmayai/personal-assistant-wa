import { useCallback, useEffect, useRef, useState } from "react";
import type { NutritionToday, NutritionDay } from "../lib/types";
import { api, ApiError } from "../lib/api";
import { Button, Card, Input, Spinner } from "../components/ui";

// ── SVG progress ring ────────────────────────────────────────────────────────
function Ring({ value, max, label, unit, color }: {
  value: number; max: number; label: string; unit: string; color: string;
}) {
  const R = 42;
  const C = 2 * Math.PI * R;
  const pct = max > 0 ? Math.min(1, value / max) : 0;
  return (
    <div className="relative flex h-28 w-28 items-center justify-center">
      <svg viewBox="0 0 100 100" className="h-28 w-28 -rotate-90">
        <circle cx="50" cy="50" r={R} fill="none" strokeWidth="9" className="stroke-slate-800" />
        <circle
          cx="50" cy="50" r={R} fill="none" strokeWidth="9" strokeLinecap="round"
          stroke={color} strokeDasharray={C} strokeDashoffset={C * (1 - pct)}
          style={{ transition: "stroke-dashoffset .5s ease" }}
        />
      </svg>
      <div className="absolute flex flex-col items-center">
        <span className="text-xl font-semibold tabular-nums">{Math.round(value)}</span>
        <span className="text-[11px] text-slate-400">{unit}</span>
        <span className="mt-0.5 text-xs font-medium text-slate-300">{label}</span>
      </div>
    </div>
  );
}

const SOURCE_ICON: Record<string, string> = { image: "📷", text: "✍️", water: "💧" };
const WATER_STEPS = [200, 350, 500, 1000];

export default function Nutrition() {
  const [tab, setTab] = useState<"today" | "history">("today");
  const [today, setToday] = useState<NutritionToday | null>(null);
  const [history, setHistory] = useState<NutritionDay[] | null>(null);
  const [days, setDays] = useState(7);
  const [meal, setMeal] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const galleryRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);

  const loadToday = useCallback(async () => {
    setToday(await api.get<NutritionToday>("/api/nutrition/today"));
  }, []);

  useEffect(() => { void loadToday().catch(() => setError("Failed to load")); }, [loadToday]);
  useEffect(() => {
    if (tab === "history") {
      api.get<{ days: NutritionDay[] }>(`/api/nutrition/history?days=${days}`)
        .then((r) => setHistory(r.days)).catch(() => setError("Failed to load history"));
    }
  }, [tab, days]);

  const withBusy = async (fn: () => Promise<unknown>, working: string) => {
    setBusy(true); setError(""); setNote(working);
    try { await fn(); await loadToday(); }
    catch (e) { setError(e instanceof ApiError ? e.detail : "Something went wrong"); }
    finally { setBusy(false); setNote(""); }
  };

  const logText = () => {
    const text = meal.trim();
    if (!text) return;
    void withBusy(async () => { await api.post("/api/nutrition/log-text", { text }); setMeal(""); }, "Analyzing meal…");
  };

  const logImage = (file?: File) => {
    if (!file) return;
    void withBusy(() => api.upload("/api/nutrition/log-image", file), "Analyzing photo… (up to 30s)");
  };

  const logWater = (ml: number) =>
    void withBusy(() => api.post("/api/nutrition/log-water", { amount_ml: ml }), "Logging water…");

  const deleteMeal = (id: number) =>
    void withBusy(() => api.delete(`/api/nutrition/${id}`), "Removing…");

  if (!today) return <div className="flex h-full items-center justify-center"><Spinner className="h-8 w-8" /></div>;

  const micros = Object.entries(today.totals.micros || {}).filter(([k]) => k !== "water_ml");

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">🥗 Nutrition</h2>
        <div className="flex rounded-lg border border-slate-800 p-0.5 text-sm">
          {(["today", "history"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)}
              className={`rounded-md px-3 py-1 capitalize ${tab === t ? "bg-slate-800 text-white" : "text-slate-400"}`}>
              {t}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>
      )}

      {tab === "today" ? (
        <>
          <Card className="flex justify-around">
            <Ring value={today.totals.protein} max={today.protein_target} label="Protein" unit={`/ ${today.protein_target} g`} color="#34d399" />
            <Ring value={today.totals.carbs} max={250} label="Carbs" unit="g carbs" color="#818cf8" />
            <Ring value={today.totals.calories} max={2200} label="Calories" unit="kcal" color="#a78bfa" />
          </Card>

          <Card className="space-y-3">
            <div className="text-xs font-medium uppercase tracking-wide text-slate-400">Log a meal</div>
            <div className="grid grid-cols-2 gap-2">
              <button disabled={busy} onClick={() => galleryRef.current?.click()}
                className="flex flex-col items-center gap-1 rounded-xl border border-slate-700 bg-slate-900 py-4 text-sm hover:bg-slate-800 disabled:opacity-50">
                <span className="text-2xl">🖼️</span> Gallery
              </button>
              <button disabled={busy} onClick={() => cameraRef.current?.click()}
                className="flex flex-col items-center gap-1 rounded-xl border border-slate-700 bg-slate-900 py-4 text-sm hover:bg-slate-800 disabled:opacity-50">
                <span className="text-2xl">📷</span> Camera
              </button>
            </div>
            <input ref={galleryRef} type="file" accept="image/*" hidden
              onChange={(e) => { logImage(e.target.files?.[0]); e.target.value = ""; }} />
            <input ref={cameraRef} type="file" accept="image/*" capture="environment" hidden
              onChange={(e) => { logImage(e.target.files?.[0]); e.target.value = ""; }} />
            <div className="flex gap-2">
              <Input dir="auto" placeholder="Or type a meal…" value={meal} disabled={busy}
                onChange={(e) => setMeal(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") logText(); }} />
              <Button onClick={logText} disabled={busy || !meal.trim()}>+ Log</Button>
            </div>
            {note && <div className="flex items-center gap-2 text-sm text-slate-400"><Spinner className="h-4 w-4" /> {note}</div>}
          </Card>

          <Card className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium">💧 Water</span>
              <span className="text-slate-400 tabular-nums">{today.water_ml} / {today.water_target_ml} ml</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-800">
              <div className="h-full rounded-full bg-sky-400"
                style={{ width: `${Math.min(100, (today.water_ml / today.water_target_ml) * 100)}%`, transition: "width .4s" }} />
            </div>
            <div className="grid grid-cols-4 gap-2">
              {WATER_STEPS.map((ml) => (
                <Button key={ml} variant="secondary" disabled={busy} onClick={() => logWater(ml)}>
                  {ml >= 1000 ? "+1 L" : `+${ml}`}
                </Button>
              ))}
            </div>
          </Card>

          <div>
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Today's meals</div>
            {today.meals.length === 0 ? (
              <Card className="text-center text-sm text-slate-500">No meals logged yet.</Card>
            ) : (
              <div className="space-y-2">
                {today.meals.map((m) => (
                  <Card key={m.id} className="flex items-center justify-between gap-3 py-3">
                    <div className="min-w-0">
                      <div dir="auto" className="truncate text-sm font-medium">
                        <span className="mr-1">{SOURCE_ICON[m.source] ?? "🍽️"}</span>{m.meal_description}
                      </div>
                      <div className="mt-0.5 text-xs text-slate-400 tabular-nums">
                        {Math.round(m.protein)}g protein · {Math.round(m.carbs)}g carbs · {Math.round(m.calories)} kcal
                      </div>
                    </div>
                    <button aria-label="delete" onClick={() => deleteMeal(m.id)} disabled={busy}
                      className="shrink-0 rounded-md px-2 py-1 text-slate-500 hover:bg-slate-800 hover:text-red-400 disabled:opacity-50">✕</button>
                  </Card>
                ))}
              </div>
            )}
          </div>

          {micros.length > 0 && (
            <div>
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Nutrients so far</div>
              <div className="flex flex-wrap gap-2">
                {micros.map(([k, v]) => (
                  <span key={k} className="rounded-full border border-slate-800 bg-slate-900 px-3 py-1 text-xs text-slate-300">
                    {k.replace(/_/g, " ")}: <span className="tabular-nums">{typeof v === "number" ? Math.round(v * 10) / 10 : v}</span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        <>
          <div className="flex gap-2">
            {[7, 14, 30].map((d) => (
              <Button key={d} variant={days === d ? "primary" : "secondary"} onClick={() => setDays(d)}>{d}d</Button>
            ))}
          </div>
          {!history ? (
            <div className="flex justify-center py-8"><Spinner className="h-6 w-6" /></div>
          ) : history.length === 0 ? (
            <Card className="text-center text-sm text-slate-500">No history yet.</Card>
          ) : (
            <div className="space-y-2">
              {history.map((d) => (
                <Card key={d.date} className="flex items-center justify-between py-3 text-sm">
                  <span className="font-medium tabular-nums">{d.date}</span>
                  <span className="text-slate-400 tabular-nums">
                    {Math.round(d.protein)}g P · {Math.round(d.carbs)}g C · {Math.round(d.calories)} kcal · {d.meals} meals
                  </span>
                </Card>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
