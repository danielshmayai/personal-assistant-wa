import { useState } from "react";
import { api, ApiError } from "../lib/api";
import { Button, Card, Spinner } from "./ui";

interface Suggestion {
  name: string;
  description: string;
  est_protein_g: number;
  est_carbs_g: number;
  est_calories_kcal: number;
}

// "What should I eat?" — 2-3 meals that close today's remaining protein gap.
export function MealSuggestions({ onLog }: { onLog: (text: string) => void }) {
  const [data, setData] = useState<{ intro: string; suggestions: Suggestion[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setBusy(true);
    setError("");
    try {
      setData(await api.get("/api/nutrition/meal-suggestion"));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Could not get suggestions");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="font-medium">🍽️ What should I eat?</span>
        <Button variant="secondary" onClick={load} disabled={busy}>
          {busy ? <Spinner className="h-4 w-4" /> : data ? "Refresh" : "Suggest"}
        </Button>
      </div>
      {error && <p className="text-sm text-red-300">{error}</p>}
      {data && (
        <div dir="auto" className="space-y-2">
          {data.intro && <p className="text-sm text-slate-300">{data.intro}</p>}
          {data.suggestions.map((s) => (
            <div key={s.name} className="rounded-lg border border-slate-800 p-3">
              <div className="flex items-start justify-between gap-2">
                <span className="font-medium">{s.name}</span>
                <Button variant="secondary" onClick={() => onLog(`${s.name} — ${s.description}`)}>Ate it</Button>
              </div>
              <p className="mt-1 text-sm text-slate-400">{s.description}</p>
              <p className="mt-1 text-xs tabular-nums text-slate-500">
                ~{Math.round(s.est_protein_g)}g P · {Math.round(s.est_carbs_g)}g C · {Math.round(s.est_calories_kcal)} kcal
              </p>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
