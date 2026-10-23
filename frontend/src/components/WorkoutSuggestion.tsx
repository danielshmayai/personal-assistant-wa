import { useEffect, useState } from "react";
import type { Exercise } from "../lib/types";
import { api, ApiError } from "../lib/api";
import { Button, Card, Spinner } from "./ui";

interface Plan {
  title: string;
  workout_type: string;
  duration_min: number;
  rationale: string;
  exercises: Exercise[];
}

const TYPES = ["strength", "hiit", "cardio", "running", "swimming"];

function ExerciseGif({ name }: { name: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    api.get<{ gifUrl: string | null }>(`/api/fitness/exercise-gif?name=${encodeURIComponent(name)}`)
      .then((r) => setUrl(r.gifUrl))
      .catch(() => {});
  }, [name]);
  if (!url) return null;
  return <img src={url} alt={name} loading="lazy" className="h-16 w-16 shrink-0 rounded-md bg-white object-contain" />;
}

// Plan a workout (not logged), see how each exercise is done, then log it as
// done or send it to the Garmin watch.
export function WorkoutSuggestion({ garminConnected, onLogged }: { garminConnected: boolean; onLogged: () => void }) {
  const [minutes, setMinutes] = useState(45);
  const [type, setType] = useState("strength");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setNote("");
    try {
      await fn();
    } catch (e) {
      setNote(e instanceof ApiError ? e.detail : "Something went wrong");
    } finally {
      setBusy("");
    }
  };

  const suggest = () => run("suggest", async () => {
    setPlan(await api.post<Plan>("/api/fitness/suggest", { minutes, workout_type: type }));
  });

  const logDone = () => run("log", async () => {
    await api.post("/api/fitness/log-structured", {
      title: plan!.title, workout_type: plan!.workout_type, duration_min: plan!.duration_min,
      exercises: plan!.exercises, source: "structured",
    });
    setNote("Logged ✓");
    onLogged();
  });

  const pushToGarmin = () => run("garmin", async () => {
    const r = await api.post<{ scheduled: boolean }>("/api/garmin/push-workout", { plan });
    setNote(r.scheduled ? "Sent to Garmin and scheduled for today ⌚" : "Saved to your Garmin workout library ⌚");
  });

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-auto font-medium">🏋️ Plan a workout</span>
        <select value={type} onChange={(e) => setType(e.target.value)}
          className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm">
          {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}
          className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm">
          {[20, 30, 45, 60, 75].map((m) => <option key={m} value={m}>{m} min</option>)}
        </select>
        <Button onClick={suggest} disabled={!!busy}>
          {busy === "suggest" ? <Spinner className="h-4 w-4" /> : plan ? "Another" : "Suggest"}
        </Button>
      </div>

      {plan && (
        <div dir="auto" className="space-y-2">
          <div className="font-medium">{plan.title}</div>
          {plan.rationale && <p className="text-sm text-slate-400">{plan.rationale}</p>}
          {plan.exercises.map((ex, i) => (
            <div key={`${ex.name}-${i}`} className="flex items-center gap-3 rounded-lg border border-slate-800 p-2">
              <ExerciseGif name={ex.name} />
              <div className="text-sm">
                <div className="font-medium">{ex.name}</div>
                <div className="tabular-nums text-slate-400">
                  {ex.duration_sec
                    ? `${ex.sets ?? 1} × ${ex.duration_sec}s`
                    : `${ex.sets ?? ""}×${ex.reps ?? ""}${ex.weight_kg ? ` · ${ex.weight_kg} kg` : ""}`}
                  {ex.rpe ? ` · RPE ${ex.rpe}` : ""}
                </div>
                {ex.notes && <div className="text-xs text-slate-500">{ex.notes}</div>}
              </div>
            </div>
          ))}
          <div className="flex gap-2">
            <Button variant="secondary" onClick={logDone} disabled={!!busy}>✓ I did it — log</Button>
            {garminConnected && (
              <Button variant="secondary" onClick={pushToGarmin} disabled={!!busy}>⌚ Send to Garmin</Button>
            )}
          </div>
        </div>
      )}
      {note && <p className="text-sm text-slate-400">{note}</p>}
    </Card>
  );
}
