import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Badge, Card } from "./ui";

interface Health {
  memory_writes_7d: Record<string, number>;
  tool_calls_7d: Record<string, number>;
  embeddings: { state: string; model: string; last_error: string | null; errors: number };
  web: {
    replies: number;
    median_reply_ms: number | null;
    p90_reply_ms: number | null;
    retrieval: { turns: number; with_memory: number; with_notes: number };
    notes_hit_rate: number | null;
  };
}

const sum = (o: Record<string, number>) => Object.values(o).reduce((a, b) => a + b, 0);
const secs = (ms: number | null) => (ms == null ? "—" : `${(ms / 1000).toFixed(1)}s`);

function Counts({ data }: { data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return <span className="text-slate-500">none</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map(([k, v]) => (
        <span key={k} className="rounded-full bg-slate-800 px-2 py-0.5 text-xs tabular-nums text-slate-300">
          {k} {v}
        </span>
      ))}
    </div>
  );
}

// "Is it actually helping?" — what got remembered, what got done, how fast.
export function AssistantHealth() {
  const [h, setH] = useState<Health | null>(null);
  useEffect(() => { api.get<Health>("/api/admin/health").then(setH).catch(() => {}); }, []);
  if (!h) return null;

  const emb = h.embeddings;
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Assistant health</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <Card className="space-y-2">
          <div className="text-sm text-slate-400">Learned (7 days)</div>
          <div className="text-2xl font-semibold tabular-nums">{sum(h.memory_writes_7d)}</div>
          <Counts data={h.memory_writes_7d} />
        </Card>
        <Card className="space-y-2">
          <div className="text-sm text-slate-400">Actions taken (7 days)</div>
          <div className="text-2xl font-semibold tabular-nums">{sum(h.tool_calls_7d)}</div>
          <Counts data={h.tool_calls_7d} />
        </Card>
        <Card className="space-y-1">
          <div className="text-sm text-slate-400">Reply time (web, since restart)</div>
          <div className="text-2xl font-semibold tabular-nums">{secs(h.web.median_reply_ms)}</div>
          <div className="text-xs text-slate-500">
            median of {h.web.replies} · p90 {secs(h.web.p90_reply_ms)}
          </div>
        </Card>
        <Card className="space-y-1">
          <div className="flex items-center gap-2 text-sm text-slate-400">
            Memory recall {emb.state === "ok" ? <Badge tone="ok">ok</Badge>
              : emb.state === "error" ? <Badge tone="warn">error</Badge> : <Badge>idle</Badge>}
          </div>
          <div className="text-2xl font-semibold tabular-nums">
            {h.web.notes_hit_rate == null ? "—" : `${Math.round(h.web.notes_hit_rate * 100)}%`}
          </div>
          <div className="text-xs text-slate-500">
            of {h.web.retrieval.turns} web turns used relevant notes · {emb.model}
          </div>
          {emb.state === "error" && emb.last_error && (
            <div className="truncate text-xs text-amber-300" title={emb.last_error}>{emb.last_error}</div>
          )}
        </Card>
      </div>
    </section>
  );
}
