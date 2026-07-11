import { useEffect, useState } from "react";
import type { AdminTenant } from "../lib/types";
import { api } from "../lib/api";
import { Badge, Button, Card, FullPageSpinner } from "../components/ui";

export default function Admin() {
  const [tenants, setTenants] = useState<AdminTenant[] | null>(null);
  const [confirmId, setConfirmId] = useState("");

  const load = async () => {
    const data = await api.get<{ tenants: AdminTenant[] }>("/api/admin/tenants");
    setTenants(data.tenants);
  };

  useEffect(() => {
    void load();
  }, []);

  if (!tenants) return <FullPageSpinner />;

  const act = async (fn: () => Promise<unknown>) => {
    await fn();
    setConfirmId("");
    await load();
  };

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4">
      <h2 className="text-lg font-semibold">Members</h2>
      <p className="text-sm text-slate-400">
        Anyone signing in with Google gets an isolated account. Revoke to block access instantly;
        delete to erase all of their data.
      </p>
      {tenants.map((t) => (
        <Card key={t.id} className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 font-medium">
              {t.display_name || t.email}
              {t.is_owner && <Badge tone="info">owner</Badge>}
              {t.status === "active" && !t.is_owner && <Badge tone="ok">active</Badge>}
              {t.status === "revoked" && <Badge tone="warn">revoked</Badge>}
              {t.status === "deleted" && <Badge>deleted</Badge>}
              {!t.onboarded && t.status === "active" && <Badge>not onboarded</Badge>}
            </div>
            <div className="text-sm text-slate-400">{t.email}</div>
          </div>
          {!t.is_owner && t.status !== "deleted" && (
            <div className="flex gap-2">
              {t.status === "active" ? (
                <Button variant="secondary" onClick={() => act(() => api.post(`/api/admin/tenants/${t.id}/revoke`))}>
                  Revoke
                </Button>
              ) : (
                <Button variant="secondary" onClick={() => act(() => api.post(`/api/admin/tenants/${t.id}/restore`))}>
                  Restore
                </Button>
              )}
              {confirmId === t.id ? (
                <>
                  <Button variant="danger" onClick={() => act(() => api.delete(`/api/admin/tenants/${t.id}`))}>
                    Confirm delete
                  </Button>
                  <Button variant="ghost" onClick={() => setConfirmId("")}>Cancel</Button>
                </>
              ) : (
                <Button variant="danger" onClick={() => setConfirmId(t.id)}>Delete</Button>
              )}
            </div>
          )}
        </Card>
      ))}
    </div>
  );
}
