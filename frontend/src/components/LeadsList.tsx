import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Button, Card, Input } from "./ui";

interface Lead {
  id: number;
  phone: string;
  label: string;
  obsidian_note: string;
  last_activity: string | null;
}

// WhatsApp contacts the engine watches silently and summarizes into memory.
// The API answers only for the local owner (WhatsApp lives there); for
// everyone else it 404s and this renders nothing.
export function LeadsList() {
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [phone, setPhone] = useState("");
  const [label, setLabel] = useState("");

  const load = () =>
    api.get<{ leads: Lead[] }>("/api/leads").then((r) => setLeads(r.leads)).catch(() => setLeads(null));

  useEffect(() => { void load(); }, []);

  if (!leads) return null;

  const add = async () => {
    if (!phone.trim()) return;
    await api.post("/api/leads", { phone: phone.trim(), label: label.trim() });
    setPhone("");
    setLabel("");
    await load();
  };

  const remove = async (id: number) => {
    await api.delete(`/api/leads/${id}`);
    await load();
  };

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Watched WhatsApp leads</h2>
      <p className="text-sm text-slate-400">
        Messages with these contacts are logged silently and summarized into memory — no replies are sent.
      </p>
      <Card className="flex flex-wrap gap-2">
        <Input placeholder="Phone, e.g. 972501234567" value={phone} onChange={(e) => setPhone(e.target.value)} className="flex-1" />
        <Input placeholder="Label" value={label} onChange={(e) => setLabel(e.target.value)} className="flex-1" />
        <Button onClick={add} disabled={!phone.trim()}>Add</Button>
      </Card>
      {leads.map((l) => (
        <Card key={l.id} className="flex items-center justify-between gap-3 py-3 text-sm">
          <div>
            <div className="font-medium">{l.label || l.phone}</div>
            <div className="text-xs text-slate-500">
              {l.phone}{l.last_activity ? ` · last message ${new Date(l.last_activity).toLocaleDateString()}` : ""}
            </div>
          </div>
          <Button variant="secondary" onClick={() => remove(l.id)}>Remove</Button>
        </Card>
      ))}
    </section>
  );
}
