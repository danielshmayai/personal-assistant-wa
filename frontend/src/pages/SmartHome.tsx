import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { TuyaDevice } from "../lib/types";
import { api, ApiError } from "../lib/api";
import { Card, Spinner } from "../components/ui";

export default function SmartHome() {
  const [devices, setDevices] = useState<TuyaDevice[] | null>(null);
  const [status, setStatus] = useState<Record<string, Record<string, unknown>>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<TuyaDevice[]>("/api/devices")
      .then(setDevices)
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Failed to load devices"));
  }, []);

  const loadStatus = async (id: string) => {
    try {
      const s = await api.get<Record<string, unknown>>(`/api/devices/${id}/status`);
      setStatus((prev) => ({ ...prev, [id]: s }));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to load device status");
    }
  };

  const toggleExpand = (id: string) => {
    const next = expanded === id ? null : id;
    setExpanded(next);
    if (next && !status[next]) void loadStatus(next);
  };

  const toggleSwitch = async (id: string, on: boolean) => {
    setBusy(id);
    setError("");
    try {
      await api.post(`/api/devices/${id}/control`, { commands: { switch_1: on } });
      await loadStatus(id);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Command failed");
    } finally {
      setBusy(null);
    }
  };

  if (!devices) {
    if (error) {
      return (
        <div className="mx-auto max-w-2xl space-y-4 p-4">
          <h2 className="text-lg font-semibold">🏠 Smart Home</h2>
          <Card className="space-y-2 text-center text-sm text-slate-400">
            <div>{error}</div>
            <div className="text-xs text-slate-500">
              Add your Tuya Access ID/Key under <Link to="/settings" className="text-indigo-400 hover:underline">Settings → Secrets</Link>.
            </div>
          </Card>
        </div>
      );
    }
    return <div className="flex h-full items-center justify-center"><Spinner className="h-8 w-8" /></div>;
  }

  return (
    <div className="mx-auto max-w-2xl space-y-4 p-4">
      <h2 className="text-lg font-semibold">🏠 Smart Home</h2>

      {error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</div>
      )}

      {devices.length === 0 ? (
        <Card className="text-center text-sm text-slate-500">No devices found on this Tuya account.</Card>
      ) : (
        <div className="space-y-2">
          {devices.map((d) => {
            const dpsStatus = status[d.id];
            const switchOn = dpsStatus && typeof dpsStatus.switch_1 === "boolean" ? dpsStatus.switch_1 as boolean : null;
            return (
              <Card key={d.id} className="space-y-2">
                <button className="flex w-full items-center justify-between text-left" onClick={() => toggleExpand(d.id)}>
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{d.name}</div>
                    <div className="text-xs text-slate-500">{d.category}</div>
                  </div>
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${d.online ? "bg-emerald-500/15 text-emerald-400" : "bg-slate-800 text-slate-500"}`}>
                    {d.online ? "online" : "offline"}
                  </span>
                </button>
                {expanded === d.id && (
                  <div className="space-y-2 border-t border-slate-800 pt-2">
                    {!dpsStatus ? (
                      <div className="flex justify-center py-3"><Spinner className="h-4 w-4" /></div>
                    ) : (
                      <>
                        {switchOn !== null && (
                          <div className="flex items-center justify-between text-sm">
                            <span className="text-slate-400">Power</span>
                            <div className="flex gap-2">
                              <button
                                disabled={busy === d.id}
                                onClick={() => toggleSwitch(d.id, true)}
                                className={`rounded-lg px-3 py-1 text-xs ${switchOn ? "bg-indigo-600 text-white" : "bg-slate-800 text-slate-300"}`}
                              >On</button>
                              <button
                                disabled={busy === d.id}
                                onClick={() => toggleSwitch(d.id, false)}
                                className={`rounded-lg px-3 py-1 text-xs ${!switchOn ? "bg-indigo-600 text-white" : "bg-slate-800 text-slate-300"}`}
                              >Off</button>
                            </div>
                          </div>
                        )}
                        <details className="text-xs text-slate-500">
                          <summary className="cursor-pointer select-none">Raw status</summary>
                          <pre className="mt-1 overflow-x-auto rounded-lg bg-slate-950 p-2">{JSON.stringify(dpsStatus, null, 2)}</pre>
                        </details>
                      </>
                    )}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
