import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { CalendarToday, Job, ProactiveCard, TodayInfo } from "../lib/types";
import { api, ApiError } from "../lib/api";
import { useSession } from "../lib/session";
import { Badge, Button, Card, Spinner } from "../components/ui";

const CARD_ICON: Record<string, string> = { trigger: "⚡", notice: "🔔", memory: "🧠" };

export default function Home() {
  const { me } = useSession();
  const [today, setToday] = useState<TodayInfo | null>(null);
  const [calendar, setCalendar] = useState<CalendarToday | null>(null);
  const [cards, setCards] = useState<ProactiveCard[] | null>(null);
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState("");
  const [busyCard, setBusyCard] = useState<number | null>(null);

  const schedulingEnabled = !!me?.modules.find((m) => m.id === "scheduling")?.enabled;

  const loadCards = useCallback(async () => {
    setCards((await api.get<{ cards: ProactiveCard[] }>("/api/proactive-cards")).cards);
  }, []);

  useEffect(() => {
    api.get<TodayInfo>("/api/today").then(setToday).catch(() => setError("Failed to load today's summary"));
    api.get<CalendarToday>("/api/calendar-today").then(setCalendar).catch(() => {});
    void loadCards().catch(() => {});
  }, [loadCards]);

  useEffect(() => {
    if (!schedulingEnabled) return;
    api.get<{ jobs: Job[] }>("/api/jobs").then((r) => setJobs(r.jobs)).catch(() => {});
  }, [schedulingEnabled]);

  const dismissCard = async (id: number) => {
    setBusyCard(id);
    try {
      await api.delete(`/api/proactive-cards/${id}`);
      await loadCards();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to dismiss card");
    } finally {
      setBusyCard(null);
    }
  };

  const cancelJob = async (id: number) => {
    try {
      await api.delete(`/api/jobs/${id}`);
      setJobs((prev) => prev?.filter((j) => j.id !== id) ?? null);
    } catch { /* leave the list as-is on failure */ }
  };

  if (!today) return <div className="flex h-full items-center justify-center"><Spinner className="h-8 w-8" /></div>;

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">🏠 Home</h2>
        <span className="text-sm text-slate-400">
          {new Date().toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" })}
        </span>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>
      )}

      <Card className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-2xl">{today.weather ? "🌤️" : "—"}</span>
          <span className="text-sm text-slate-300">{today.weather || "Weather unavailable"}</span>
        </div>
        {schedulingEnabled && (
          <Badge tone={today.jobs_count > 0 ? "info" : "default"}>{today.jobs_count} pending job{today.jobs_count === 1 ? "" : "s"}</Badge>
        )}
      </Card>

      {cards && cards.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs font-medium uppercase tracking-wide text-slate-400">For you</div>
          {cards.map((c) => (
            <Card key={c.id} className="flex items-start justify-between gap-3 py-3">
              <div className="min-w-0">
                <div dir="auto" className="text-sm font-medium">
                  <span className="mr-1">{CARD_ICON[c.card_type] ?? "📌"}</span>{c.title}
                </div>
                {c.detail && <div dir="auto" className="mt-0.5 text-xs text-slate-400">{c.detail}</div>}
                {c.action_label && (
                  <Link to="/chat" className="mt-1 inline-block text-xs text-indigo-400 hover:underline">
                    {c.action_label} →
                  </Link>
                )}
              </div>
              <button aria-label="dismiss" onClick={() => dismissCard(c.id)} disabled={busyCard === c.id}
                className="shrink-0 rounded-md px-2 py-1 text-slate-500 hover:bg-slate-800 hover:text-red-400 disabled:opacity-50">✕</button>
            </Card>
          ))}
        </div>
      )}

      <div>
        <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Today's calendar</div>
        {!calendar ? (
          <div className="flex justify-center py-6"><Spinner className="h-5 w-5" /></div>
        ) : !calendar.connected ? (
          <Card className="text-center text-sm text-slate-500">
            Google Calendar isn't connected. <Link to="/settings" className="text-indigo-400 hover:underline">Connect it in Settings</Link>.
          </Card>
        ) : calendar.events.length === 0 ? (
          <Card className="text-center text-sm text-slate-500">Nothing on the calendar today.</Card>
        ) : (
          <div className="space-y-2">
            {calendar.events.map((e, i) => (
              <Card key={i} className="flex items-center justify-between py-2.5 text-sm">
                <span dir="auto" className="truncate">{e.title}</span>
                <span className="shrink-0 text-slate-400 tabular-nums">{e.full_day ? "All day" : e.time}</span>
              </Card>
            ))}
          </div>
        )}
      </div>

      {schedulingEnabled && (
        <div>
          <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">Scheduled jobs</div>
          {!jobs ? (
            <div className="flex justify-center py-6"><Spinner className="h-5 w-5" /></div>
          ) : jobs.length === 0 ? (
            <Card className="text-center text-sm text-slate-500">No pending jobs.</Card>
          ) : (
            <div className="space-y-2">
              {jobs.map((j) => (
                <Card key={j.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                  <div className="min-w-0">
                    <div className="truncate font-medium">{j.action_type}</div>
                    <div className="text-xs text-slate-400 tabular-nums">
                      {new Date(j.run_at).toLocaleString()} · {j.status}
                    </div>
                  </div>
                  {j.status === "pending" && (
                    <button aria-label="cancel" onClick={() => cancelJob(j.id)}
                      className="shrink-0 rounded-md px-2 py-1 text-slate-500 hover:bg-slate-800 hover:text-red-400">✕</button>
                  )}
                </Card>
              ))}
            </div>
          )}
        </div>
      )}

      <Card className="flex items-center justify-between">
        <span className="text-sm text-slate-400">Ask your assistant anything</span>
        <Link to="/chat"><Button>💬 Open Chat</Button></Link>
      </Card>
    </div>
  );
}
