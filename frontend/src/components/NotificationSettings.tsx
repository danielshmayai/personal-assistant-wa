import { useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import { disablePush, enablePush, getPushState, type PushState } from "../lib/pwa";
import { Badge, Button, Card, Switch } from "./ui";

interface BriefSettings {
  enabled: boolean;
  time: string;
}

const PUSH_BADGE: Record<PushState, JSX.Element> = {
  on: <Badge tone="ok">on</Badge>,
  off: <Badge>off</Badge>,
  denied: <Badge tone="warn">blocked in browser</Badge>,
  unsupported: <Badge>not supported here</Badge>,
};

// Reminders and the morning brief reach the user only while a tab is open
// unless this browser has a Web Push subscription.
export function NotificationSettings() {
  const [push, setPush] = useState<PushState | null>(null);
  const [brief, setBrief] = useState<BriefSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => {
    getPushState().then(setPush).catch(() => setPush("unsupported"));
    api.get<BriefSettings>("/api/brief/settings").then(setBrief).catch(() => {});
  }, []);

  const togglePush = async (next: boolean) => {
    setBusy(true);
    setNote("");
    try {
      setPush(next ? await enablePush() : await disablePush());
    } catch (e) {
      setNote(e instanceof ApiError ? e.detail : "Could not change notifications");
    } finally {
      setBusy(false);
    }
  };

  const sendTest = async () => {
    const { sent } = await api.post<{ sent: number }>("/api/push/test");
    setNote(sent ? "Test notification sent." : "No subscribed devices yet.");
  };

  const saveBrief = async (next: BriefSettings) => {
    setBrief(next);
    try {
      setBrief(await api.put<BriefSettings>("/api/brief/settings", next));
    } catch (e) {
      setNote(e instanceof ApiError ? e.detail : "Could not save the morning brief");
    }
  };

  return (
    <Card className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 font-medium">
            Notifications on this device {push && PUSH_BADGE[push]}
          </div>
          <div className="text-sm text-slate-400">Reminders arrive even when the app is closed.</div>
        </div>
        <div className="flex items-center gap-2">
          {push === "on" && <Button variant="secondary" onClick={sendTest}>Test</Button>}
          <Switch
            checked={push === "on"}
            disabled={busy || push === null || push === "unsupported" || push === "denied"}
            onChange={togglePush}
          />
        </div>
      </div>

      {brief && (
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="font-medium">Morning brief</div>
            <div className="text-sm text-slate-400">
              Weather, today's calendar, email, fitness and what I learned — every day at
            </div>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="time"
              value={brief.time}
              onChange={(e) => e.target.value && saveBrief({ ...brief, time: e.target.value })}
              className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm"
            />
            <Switch checked={brief.enabled} onChange={(enabled) => saveBrief({ ...brief, enabled })} />
          </div>
        </div>
      )}

      {note && <p className="text-sm text-slate-400">{note}</p>}
    </Card>
  );
}
