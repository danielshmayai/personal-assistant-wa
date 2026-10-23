import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Card, Spinner, cn } from "../components/ui";

interface Entry {
  id: number;
  event_type: string;
  summary: string;
  detail: string | null;
  created_at: string;
}

const TYPE_ICON: Record<string, string> = {
  calendar: "📅", email: "📧", drive: "📁", map: "🗺️", memory: "🧠", device: "💡",
  job: "⏰", nutrition: "🥗", fitness: "💪", image: "🖼️", search: "🔎", tool: "🛠️",
};

// What the assistant did on your behalf — every tool call, newest first.
export default function Activity() {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    setEntries(null);
    const q = filter ? `&event_type=${encodeURIComponent(filter)}` : "";
    api.get<{ entries: Entry[] }>(`/api/activity?limit=100${q}`).then((r) => setEntries(r.entries)).catch(() => setEntries([]));
  }, [filter]);

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4">
      <h2 className="text-lg font-semibold">📜 Activity</h2>
      <div className="flex flex-wrap gap-1">
        {["", ...Object.keys(TYPE_ICON)].map((t) => (
          <button key={t || "all"} onClick={() => setFilter(t)}
            className={cn("rounded-full px-2.5 py-0.5 text-xs",
              filter === t ? "bg-indigo-600/20 text-indigo-300" : "text-slate-400 hover:bg-slate-900")}>
            {t ? `${TYPE_ICON[t]} ${t}` : "All"}
          </button>
        ))}
      </div>
      {entries === null ? (
        <div className="flex justify-center py-8"><Spinner className="h-6 w-6" /></div>
      ) : entries.length === 0 ? (
        <Card className="text-center text-sm text-slate-500">Nothing yet — actions the assistant takes will show up here.</Card>
      ) : (
        <div className="space-y-2">
          {entries.map((e) => (
            <Card key={e.id} className="py-3">
              <button className="flex w-full items-start gap-3 text-left text-sm" onClick={() => setOpen(open === e.id ? null : e.id)}>
                <span>{TYPE_ICON[e.event_type] ?? "🛠️"}</span>
                <span dir="auto" className="flex-1">{e.summary}</span>
                <span className="shrink-0 text-xs text-slate-500">{new Date(e.created_at).toLocaleString()}</span>
              </button>
              {open === e.id && e.detail && (
                <pre dir="auto" className="mt-2 whitespace-pre-wrap break-words text-xs text-slate-400">{e.detail}</pre>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
