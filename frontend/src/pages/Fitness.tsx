import { useCallback, useEffect, useRef, useState } from "react";
import type {
  BodyMetricsResponse, DailyRec, FitnessDay, FitnessToday, GarminStatus, ProgressionPoint,
} from "../lib/types";
import { api, ApiError } from "../lib/api";
import { Badge, Button, Card, Input, Spinner } from "../components/ui";

// ── SVG progress ring (mirrors Nutrition.tsx) ───────────────────────────────
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

// ── Minimal hand-rolled sparkline (no chart library in this project) ───────
function Sparkline({ points, color }: { points: { x: number; y: number }[]; color: string }) {
  if (points.length < 2) {
    return <div className="flex h-24 items-center justify-center text-xs text-slate-500">Not enough data yet</div>;
  }
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs) || 1;
  const minY = Math.min(...ys), maxY = Math.max(...ys) || 1;
  const norm = points.map((p) => ({
    x: maxX > minX ? ((p.x - minX) / (maxX - minX)) * 100 : 50,
    y: maxY > minY ? 100 - ((p.y - minY) / (maxY - minY)) * 100 : 50,
  }));
  const path = norm.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");
  return (
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-24 w-full">
      <path d={path} fill="none" stroke={color} strokeWidth="2.5" vectorEffect="non-scaling-stroke" />
      {norm.map((p, i) => (
        <circle key={i} cx={p.x} cy={p.y} r="1.5" fill={color} />
      ))}
    </svg>
  );
}

const READINESS_TONE: Record<string, "ok" | "warn" | "info"> = {
  ready: "ok", light: "warn", rest: "info",
};
const SOURCE_ICON: Record<string, string> = { image: "📷", text: "✍️", structured: "📋", garmin: "⌚" };

export default function Fitness() {
  const [tab, setTab] = useState<"today" | "history" | "body">("today");
  const [today, setToday] = useState<FitnessToday | null>(null);
  const [dailyRec, setDailyRec] = useState<DailyRec | null>(null);
  const [history, setHistory] = useState<FitnessDay[] | null>(null);
  const [days, setDays] = useState(14);
  const [bodyData, setBodyData] = useState<BodyMetricsResponse | null>(null);
  const [bodyForm, setBodyForm] = useState({ weight_kg: "", lbm_kg: "", smm_kg: "", bf_pct: "" });
  const [progExercise, setProgExercise] = useState("");
  const [progression, setProgression] = useState<ProgressionPoint[] | null>(null);
  const [garmin, setGarmin] = useState<GarminStatus | null>(null);
  const [mfaCode, setMfaCode] = useState("");
  const [mfaPending, setMfaPending] = useState(false);

  const [workoutText, setWorkoutText] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  const galleryRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);
  const scanGalleryRef = useRef<HTMLInputElement>(null);
  const scanCameraRef = useRef<HTMLInputElement>(null);

  const loadToday = useCallback(async () => {
    setToday(await api.get<FitnessToday>("/api/fitness/today"));
  }, []);
  const loadDailyRec = useCallback(async () => {
    try { setDailyRec(await api.get<DailyRec>("/api/fitness/daily-rec")); }
    catch { /* non-critical widget */ }
  }, []);
  const loadBody = useCallback(async () => {
    setBodyData(await api.get<BodyMetricsResponse>("/api/fitness/body-metrics"));
  }, []);
  const loadGarmin = useCallback(async () => {
    try { setGarmin(await api.get<GarminStatus>("/api/garmin/status")); }
    catch { /* module may not expose garmin yet */ }
  }, []);

  useEffect(() => {
    void loadToday().catch(() => setError("Failed to load"));
    void loadDailyRec();
    void loadGarmin();
  }, [loadToday, loadDailyRec, loadGarmin]);

  useEffect(() => {
    if (tab === "history") {
      api.get<{ days: FitnessDay[] }>(`/api/fitness/history?days=${days}`)
        .then((r) => setHistory(r.days)).catch(() => setError("Failed to load history"));
    }
    if (tab === "body") void loadBody().catch(() => setError("Failed to load body metrics"));
  }, [tab, days, loadBody]);

  const withBusy = async (fn: () => Promise<unknown>, working: string, reload: "today" | "body" = "today") => {
    setBusy(true); setError(""); setNote(working);
    try {
      await fn();
      if (reload === "today") await loadToday(); else await loadBody();
    } catch (e) { setError(e instanceof ApiError ? e.detail : "Something went wrong"); }
    finally { setBusy(false); setNote(""); }
  };

  const logText = () => {
    const text = workoutText.trim();
    if (!text) return;
    void withBusy(async () => { await api.post("/api/fitness/log-text", { text }); setWorkoutText(""); }, "Analyzing workout…");
  };
  const logImage = (file?: File) => {
    if (!file) return;
    void withBusy(() => api.upload("/api/fitness/log-image", file), "Analyzing photo… (up to 30s)");
  };
  const deleteWorkout = (id: number) => void withBusy(() => api.delete(`/api/fitness/${id}`), "Removing…");

  const logBodyManual = () => {
    const body: Record<string, number> = {};
    for (const [k, v] of Object.entries(bodyForm)) {
      const n = parseFloat(v);
      if (!Number.isNaN(n)) body[k] = n;
    }
    if (Object.keys(body).length === 0) return;
    void withBusy(async () => {
      await api.post("/api/fitness/body-metrics", body);
      setBodyForm({ weight_kg: "", lbm_kg: "", smm_kg: "", bf_pct: "" });
    }, "Saving measurement…", "body");
  };
  const logBodyScan = (file?: File) => {
    if (!file) return;
    void withBusy(() => api.upload("/api/fitness/body-metrics-image", file), "Reading scan… (up to 30s)", "body");
  };
  const deleteMetric = (id?: number) => {
    if (id == null) return;
    void withBusy(() => api.delete(`/api/fitness/body-metrics/${id}`), "Removing…", "body");
  };

  const loadProgression = () => {
    const ex = progExercise.trim();
    if (!ex) return;
    setBusy(true); setError("");
    api.get<{ progression: ProgressionPoint[] }>(`/api/fitness/progression?exercise=${encodeURIComponent(ex)}&days=180`)
      .then((r) => setProgression(r.progression))
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Failed to load progression"))
      .finally(() => setBusy(false));
  };

  const connectGarmin = () => {
    setBusy(true); setError("");
    api.post<{ connected?: boolean; mfa_required?: boolean }>("/api/garmin/connect")
      .then((r) => { if (r.mfa_required) setMfaPending(true); else void loadGarmin(); })
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Garmin connect failed"))
      .finally(() => setBusy(false));
  };
  const submitMfa = () => {
    if (!mfaCode.trim()) return;
    setBusy(true); setError("");
    api.post("/api/garmin/mfa", { code: mfaCode.trim() })
      .then(() => { setMfaPending(false); setMfaCode(""); void loadGarmin(); })
      .catch((e) => setError(e instanceof ApiError ? e.detail : "MFA failed"))
      .finally(() => setBusy(false));
  };
  const disconnectGarmin = () => {
    setBusy(true); setError("");
    api.post("/api/garmin/disconnect").then(() => loadGarmin()).finally(() => setBusy(false));
  };

  if (!today) return <div className="flex h-full items-center justify-center"><Spinner className="h-8 w-8" /></div>;

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">💪 Fitness</h2>
        <div className="flex rounded-lg border border-slate-800 p-0.5 text-sm">
          {(["today", "history", "body"] as const).map((t) => (
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

      {tab === "today" && (
        <>
          <Card className="flex justify-around">
            <Ring value={today.total_volume} max={5000} label="Volume" unit="kg lifted" color="#34d399" />
            <Ring value={today.total_duration_min} max={90} label="Duration" unit="min" color="#818cf8" />
          </Card>

          {dailyRec && (
            <Card className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="text-xs font-medium uppercase tracking-wide text-slate-400">Today's recommendation</div>
                <Badge tone={READINESS_TONE[dailyRec.readiness] ?? "default"}>{dailyRec.readiness_he}</Badge>
              </div>
              <div className="text-sm font-medium">{dailyRec.title} · {dailyRec.duration_min} min</div>
              <div dir="auto" className="text-sm text-slate-400">{dailyRec.rationale}</div>
              {dailyRec.key_exercises.length > 0 && (
                <ul className="mt-1 space-y-0.5 text-xs text-slate-400">
                  {dailyRec.key_exercises.map((ex, i) => (
                    <li key={i}>• {ex.name}{ex.sets ? ` — ${ex.sets}×${ex.reps ?? "?"}${ex.weight_kg ? ` @ ${ex.weight_kg}kg` : ""}` : ""}</li>
                  ))}
                </ul>
              )}
              <div className="text-xs text-slate-500 tabular-nums">
                {dailyRec.weekly_done}/{dailyRec.weekly_target} sessions this week
                {dailyRec.garmin?.sleep_score != null && ` · sleep ${dailyRec.garmin.sleep_score}`}
                {dailyRec.garmin?.steps != null && ` · ${dailyRec.garmin.steps} steps`}
              </div>
            </Card>
          )}

          <Card className="space-y-3">
            <div className="text-xs font-medium uppercase tracking-wide text-slate-400">Log a workout</div>
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
              <Input dir="auto" placeholder="Or describe your workout…" value={workoutText} disabled={busy}
                onChange={(e) => setWorkoutText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") logText(); }} />
              <Button onClick={logText} disabled={busy || !workoutText.trim()}>+ Log</Button>
            </div>
            {note && <div className="flex items-center gap-2 text-sm text-slate-400"><Spinner className="h-4 w-4" /> {note}</div>}
          </Card>

          <Card className="space-y-2">
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium">⌚ Garmin</span>
              {garmin?.connected ? (
                <Badge tone="ok">Connected</Badge>
              ) : (
                <Badge tone="default">Not connected</Badge>
              )}
            </div>
            {garmin?.connected ? (
              <>
                {garmin.today && (
                  <div className="text-xs text-slate-400 tabular-nums">
                    {garmin.today.sleep_score != null && `sleep ${garmin.today.sleep_score} · `}
                    {garmin.today.resting_hr != null && `RHR ${garmin.today.resting_hr} · `}
                    {garmin.today.steps != null && `${garmin.today.steps} steps`}
                  </div>
                )}
                <Button variant="secondary" disabled={busy} onClick={disconnectGarmin}>Disconnect</Button>
              </>
            ) : mfaPending ? (
              <div className="flex gap-2">
                <Input placeholder="MFA code" value={mfaCode} disabled={busy}
                  onChange={(e) => setMfaCode(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") submitMfa(); }} />
                <Button onClick={submitMfa} disabled={busy || !mfaCode.trim()}>Verify</Button>
              </div>
            ) : (
              <>
                <div className="text-xs text-slate-500">
                  Add your Garmin email + password under Settings → Secrets, then connect.
                </div>
                <Button variant="secondary" disabled={busy} onClick={connectGarmin}>Connect Garmin</Button>
              </>
            )}
          </Card>

          <div>
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Today's sessions</div>
            {today.workouts.length === 0 ? (
              <Card className="text-center text-sm text-slate-500">No workouts logged yet.</Card>
            ) : (
              <div className="space-y-2">
                {today.workouts.map((w) => (
                  <Card key={w.id} className="flex items-start justify-between gap-3 py-3">
                    <div className="min-w-0">
                      <div dir="auto" className="truncate text-sm font-medium">
                        <span className="mr-1">{SOURCE_ICON[w.source] ?? "🏋️"}</span>{w.title || w.workout_type}
                      </div>
                      <div className="mt-0.5 text-xs text-slate-400 tabular-nums">
                        {w.duration_min}min · {Math.round(w.total_volume)}kg volume{w.avg_rpe ? ` · RPE ${w.avg_rpe}` : ""}
                      </div>
                      {w.ai_summary && <div dir="auto" className="mt-1 text-xs text-slate-500">{w.ai_summary}</div>}
                    </div>
                    <button aria-label="delete" onClick={() => deleteWorkout(w.id)} disabled={busy}
                      className="shrink-0 rounded-md px-2 py-1 text-slate-500 hover:bg-slate-800 hover:text-red-400 disabled:opacity-50">✕</button>
                  </Card>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {tab === "history" && (
        <>
          <div className="flex gap-2">
            {[7, 14, 30, 90].map((d) => (
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
                    {Math.round(d.total_volume)}kg · {Math.round(d.total_duration)}min · {d.session_count} session{d.session_count === 1 ? "" : "s"}
                    {d.avg_rpe ? ` · RPE ${d.avg_rpe}` : ""}
                  </span>
                </Card>
              ))}
            </div>
          )}

          <Card className="space-y-2">
            <div className="text-xs font-medium uppercase tracking-wide text-slate-400">Exercise progression</div>
            <div className="flex gap-2">
              <Input placeholder="Exercise name (e.g. bench press)" value={progExercise}
                onChange={(e) => setProgExercise(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") loadProgression(); }} />
              <Button onClick={loadProgression} disabled={busy || !progExercise.trim()}>Go</Button>
            </div>
            {progression && (
              <>
                <div className="text-xs text-slate-500">Top weight over time</div>
                <Sparkline color="#a78bfa" points={progression.map((p, i) => ({ x: i, y: p.top_weight }))} />
              </>
            )}
          </Card>
        </>
      )}

      {tab === "body" && bodyData && (
        <>
          {bodyData.latest && (
            <Card className="space-y-1">
              <div className="text-xs font-medium uppercase tracking-wide text-slate-400">Latest measurement</div>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm tabular-nums">
                {bodyData.latest.weight_kg != null && <span>⚖️ {bodyData.latest.weight_kg} kg</span>}
                {bodyData.latest.lbm_kg != null && <span>💪 LBM {bodyData.latest.lbm_kg} kg</span>}
                {bodyData.latest.smm_kg != null && <span>🦾 SMM {bodyData.latest.smm_kg} kg</span>}
                {bodyData.latest.bf_pct != null && <span>📊 BF {bodyData.latest.bf_pct}%</span>}
              </div>
              {bodyData.latest.ai_summary && <div dir="auto" className="text-xs text-slate-400">{bodyData.latest.ai_summary}</div>}
            </Card>
          )}

          <Card className="space-y-3">
            <div className="text-xs font-medium uppercase tracking-wide text-slate-400">Log a measurement</div>
            <div className="grid grid-cols-2 gap-2">
              <button disabled={busy} onClick={() => scanGalleryRef.current?.click()}
                className="flex flex-col items-center gap-1 rounded-xl border border-slate-700 bg-slate-900 py-4 text-sm hover:bg-slate-800 disabled:opacity-50">
                <span className="text-2xl">🖼️</span> InBody scan (gallery)
              </button>
              <button disabled={busy} onClick={() => scanCameraRef.current?.click()}
                className="flex flex-col items-center gap-1 rounded-xl border border-slate-700 bg-slate-900 py-4 text-sm hover:bg-slate-800 disabled:opacity-50">
                <span className="text-2xl">📷</span> InBody scan (camera)
              </button>
            </div>
            <input ref={scanGalleryRef} type="file" accept="image/*" hidden
              onChange={(e) => { logBodyScan(e.target.files?.[0]); e.target.value = ""; }} />
            <input ref={scanCameraRef} type="file" accept="image/*" capture="environment" hidden
              onChange={(e) => { logBodyScan(e.target.files?.[0]); e.target.value = ""; }} />
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {(["weight_kg", "lbm_kg", "smm_kg", "bf_pct"] as const).map((k) => (
                <Input key={k} type="number" inputMode="decimal" placeholder={k.replace("_kg", "").replace("_pct", " %")}
                  value={bodyForm[k]} disabled={busy}
                  onChange={(e) => setBodyForm((f) => ({ ...f, [k]: e.target.value }))} />
              ))}
            </div>
            <Button onClick={logBodyManual} disabled={busy || Object.values(bodyForm).every((v) => !v.trim())}>Save measurement</Button>
            {note && <div className="flex items-center gap-2 text-sm text-slate-400"><Spinner className="h-4 w-4" /> {note}</div>}
          </Card>

          <div>
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">History</div>
            {bodyData.history.length === 0 ? (
              <Card className="text-center text-sm text-slate-500">No measurements yet.</Card>
            ) : (
              <div className="space-y-2">
                {bodyData.history.slice().reverse().map((m, i) => (
                  <Card key={m.id ?? i} className="flex items-center justify-between gap-3 py-3">
                    <div className="min-w-0 text-sm tabular-nums">
                      <span className="mr-2 text-xs text-slate-500">{m.log_date}</span>
                      {m.weight_kg != null && `${m.weight_kg}kg `}
                      {m.bf_pct != null && `· ${m.bf_pct}% BF`}
                    </div>
                    <button aria-label="delete" onClick={() => deleteMetric(m.id)} disabled={busy || m.id == null}
                      className="shrink-0 rounded-md px-2 py-1 text-slate-500 hover:bg-slate-800 hover:text-red-400 disabled:opacity-50">✕</button>
                  </Card>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
