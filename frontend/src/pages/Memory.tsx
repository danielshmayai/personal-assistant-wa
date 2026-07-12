import { useEffect, useState } from "react";
import type {
  MemoryCategory, MemoryEntityDetail, MemoryEntitySummary, MemoryRule,
} from "../lib/types";
import { api, ApiError } from "../lib/api";
import { Button, Card, Input, Spinner } from "../components/ui";

const CATEGORY_ICON: Record<string, string> = {
  People: "👤", Entities: "🏢", Investments: "💰", Projects: "📁",
  Preferences: "⭐", Photos: "📷", Misc: "🗂️",
};

export default function Memory() {
  const [tab, setTab] = useState<"browse" | "rules" | "search">("browse");
  const [categories, setCategories] = useState<MemoryCategory[] | null>(null);
  const [openCategory, setOpenCategory] = useState<string | null>(null);
  const [entities, setEntities] = useState<MemoryEntitySummary[] | null>(null);
  const [detail, setDetail] = useState<MemoryEntityDetail | null>(null);
  const [rules, setRules] = useState<MemoryRule[] | null>(null);
  const [query, setQuery] = useState("");
  const [searchResult, setSearchResult] = useState<string | null>(null);
  const [newFact, setNewFact] = useState({ category: "Misc", entity: "", content: "" });
  const [newRule, setNewRule] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const loadCategories = () => {
    api.get<{ categories: MemoryCategory[] }>("/api/memory/categories")
      .then((r) => setCategories(r.categories))
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Failed to load categories"));
  };

  useEffect(() => { loadCategories(); }, []);
  useEffect(() => {
    if (tab === "rules") {
      api.get<{ rules: MemoryRule[] }>("/api/memory/rules")
        .then((r) => setRules(r.rules)).catch(() => setError("Failed to load rules"));
    }
  }, [tab]);

  const openCat = (name: string) => {
    if (openCategory === name) { setOpenCategory(null); setEntities(null); return; }
    setOpenCategory(name);
    setDetail(null);
    api.get<{ entities: MemoryEntitySummary[] }>(`/api/memory/category/${encodeURIComponent(name)}`)
      .then((r) => setEntities(r.entities))
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Failed to load entities"));
  };

  const openEntity = (category: string, entity: string) => {
    api.get<MemoryEntityDetail>(`/api/memory/entity?category=${encodeURIComponent(category)}&entity=${encodeURIComponent(entity)}`)
      .then(setDetail)
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Failed to load entity"));
  };

  const hideFact = async (category: string, entity: string) => {
    setBusy(true); setError("");
    try {
      await api.post("/api/memory/fact/hide", { category, entity });
      setDetail(null);
      const r = await api.get<{ entities: MemoryEntitySummary[] }>(
        `/api/memory/category/${encodeURIComponent(category)}`,
      );
      setEntities(r.entities);
      loadCategories();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to hide fact");
    } finally { setBusy(false); }
  };

  const hideRule = async (instruction: string) => {
    setBusy(true); setError("");
    try {
      await api.post("/api/memory/rule/hide", { instruction });
      setRules((prev) => prev?.filter((r) => r.text !== instruction) ?? null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to hide rule");
    } finally { setBusy(false); }
  };

  const runSearch = () => {
    const q = query.trim();
    if (!q) return;
    setBusy(true); setError(""); setSearchResult(null);
    api.get<{ result: string }>(`/api/memory/search?q=${encodeURIComponent(q)}`)
      .then((r) => setSearchResult(r.result))
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Search failed"))
      .finally(() => setBusy(false));
  };

  const addFact = () => {
    if (!newFact.entity.trim() || !newFact.content.trim()) return;
    setBusy(true); setError("");
    api.post("/api/memory/fact", newFact)
      .then(() => { setNewFact({ category: newFact.category, entity: "", content: "" }); loadCategories(); })
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Failed to save fact"))
      .finally(() => setBusy(false));
  };

  const addRule = () => {
    const instruction = newRule.trim();
    if (!instruction) return;
    setBusy(true); setError("");
    api.post("/api/memory/rule", { instruction })
      .then(() => { setNewRule(""); })
      .then(() => api.get<{ rules: MemoryRule[] }>("/api/memory/rules"))
      .then((r) => setRules(r.rules))
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Failed to save rule"))
      .finally(() => setBusy(false));
  };

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">🧠 Memory</h2>
        <div className="flex rounded-lg border border-slate-800 p-0.5 text-sm">
          {(["browse", "rules", "search"] as const).map((t) => (
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

      {tab === "browse" && (
        <>
          <Card className="space-y-3">
            <div className="text-xs font-medium uppercase tracking-wide text-slate-400">Save a fact</div>
            <div className="grid grid-cols-3 gap-2">
              <select value={newFact.category} disabled={busy}
                onChange={(e) => setNewFact((f) => ({ ...f, category: e.target.value }))}
                className="rounded-lg border border-slate-700 bg-slate-900 px-2 py-2 text-sm">
                {["People", "Entities", "Investments", "Projects", "Preferences", "Misc"].map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
              <Input className="col-span-2" placeholder="Entity name" value={newFact.entity} disabled={busy}
                onChange={(e) => setNewFact((f) => ({ ...f, entity: e.target.value }))} />
            </div>
            <Input dir="auto" placeholder="What do you want to remember?" value={newFact.content} disabled={busy}
              onChange={(e) => setNewFact((f) => ({ ...f, content: e.target.value }))}
              onKeyDown={(e) => { if (e.key === "Enter") addFact(); }} />
            <Button onClick={addFact} disabled={busy || !newFact.entity.trim() || !newFact.content.trim()}>+ Save fact</Button>
          </Card>

          {!categories ? (
            <div className="flex justify-center py-8"><Spinner className="h-6 w-6" /></div>
          ) : (
            <div className="space-y-2">
              {categories.map((c) => (
                <Card key={c.name} className="space-y-2">
                  <button className="flex w-full items-center justify-between text-left" onClick={() => openCat(c.name)}>
                    <span className="text-sm font-medium">{CATEGORY_ICON[c.name] ?? "📄"} {c.name}</span>
                    <span className="text-xs text-slate-500">{c.count}</span>
                  </button>
                  {openCategory === c.name && (
                    <div className="space-y-1 border-t border-slate-800 pt-2">
                      {!entities ? (
                        <div className="flex justify-center py-3"><Spinner className="h-4 w-4" /></div>
                      ) : entities.length === 0 ? (
                        <div className="text-center text-xs text-slate-500">Nothing here yet.</div>
                      ) : (
                        entities.map((e) => (
                          <button key={e.file} onClick={() => openEntity(c.name, e.entity)}
                            className="block w-full rounded-lg px-2 py-1.5 text-left text-sm hover:bg-slate-800">
                            <div className="font-medium">{e.entity}</div>
                            {e.preview && <div dir="auto" className="truncate text-xs text-slate-500">{e.preview}</div>}
                          </button>
                        ))
                      )}
                    </div>
                  )}
                </Card>
              ))}
            </div>
          )}

          {detail && (
            <Card className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="text-sm font-medium">{detail.entity}</div>
                <button onClick={() => hideFact(detail.category, detail.entity)} disabled={busy}
                  className="text-xs text-slate-500 hover:text-red-400">Hide</button>
              </div>
              <pre dir="auto" className="whitespace-pre-wrap text-xs text-slate-300">{detail.content}</pre>
            </Card>
          )}
        </>
      )}

      {tab === "rules" && (
        <>
          <Card className="space-y-2">
            <div className="text-xs font-medium uppercase tracking-wide text-slate-400">Add a rule</div>
            <div className="flex gap-2">
              <Input dir="auto" placeholder="e.g. always reply in Hebrew" value={newRule} disabled={busy}
                onChange={(e) => setNewRule(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") addRule(); }} />
              <Button onClick={addRule} disabled={busy || !newRule.trim()}>+ Add</Button>
            </div>
          </Card>
          {!rules ? (
            <div className="flex justify-center py-8"><Spinner className="h-6 w-6" /></div>
          ) : rules.length === 0 ? (
            <Card className="text-center text-sm text-slate-500">No rules yet.</Card>
          ) : (
            <div className="space-y-2">
              {rules.map((r) => (
                <Card key={r.raw} className="flex items-start justify-between gap-3 py-3">
                  <div dir="auto" className="text-sm">{r.text}{r.added && <span className="ml-2 text-xs text-slate-500">({r.added})</span>}</div>
                  <button aria-label="hide" onClick={() => hideRule(r.text)} disabled={busy}
                    className="shrink-0 rounded-md px-2 py-1 text-slate-500 hover:bg-slate-800 hover:text-red-400">✕</button>
                </Card>
              ))}
            </div>
          )}
        </>
      )}

      {tab === "search" && (
        <>
          <div className="flex gap-2">
            <Input placeholder="Search your memory…" value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") runSearch(); }} />
            <Button onClick={runSearch} disabled={busy || !query.trim()}>Search</Button>
          </div>
          {busy && <div className="flex justify-center py-6"><Spinner className="h-5 w-5" /></div>}
          {searchResult !== null && (
            <Card>
              <pre dir="auto" className="whitespace-pre-wrap text-sm text-slate-300">{searchResult || "No matches found."}</pre>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
