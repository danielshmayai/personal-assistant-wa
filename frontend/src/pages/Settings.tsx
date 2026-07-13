import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useSession } from "../lib/session";
import { api } from "../lib/api";
import { ModuleToggleGrid } from "../components/ModuleToggleGrid";
import { SecretField } from "../components/SecretField";
import { Badge, Button, Card } from "../components/ui";

export default function Settings() {
  const { me, refresh, logout } = useSession();
  const [params] = useSearchParams();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const googleFlash = params.get("google");

  if (!me) return null;

  const toggleModule = async (id: string, enabled: boolean) => {
    await api.put("/api/modules", { module_id: id, enabled });
    await refresh();
  };

  const connectGoogle = async () => {
    const { auth_url } = await api.get<{ auth_url: string }>("/api/google/connect");
    window.location.href = auth_url;
  };

  const disconnectGoogle = async () => {
    await api.delete("/api/google/connect");
    await refresh();
  };

  const deleteAccount = async () => {
    await api.delete("/api/me");
    window.location.href = "/login";
  };

  return (
    <div className="mx-auto max-w-3xl space-y-8 p-4 pb-16">
      <section>
        <h2 className="mb-1 text-lg font-semibold">Account</h2>
        <Card className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {me.tenant.avatar_url && (
              <img src={me.tenant.avatar_url} alt="" className="h-10 w-10 rounded-full" referrerPolicy="no-referrer" />
            )}
            <div>
              <div className="flex items-center gap-2 font-medium">
                {me.tenant.display_name || me.tenant.email}
                {me.tenant.is_owner && <Badge tone="info">owner</Badge>}
              </div>
              <div className="text-sm text-slate-400">{me.tenant.email}</div>
            </div>
          </div>
          <Button variant="secondary" onClick={logout}>Sign out</Button>
        </Card>
      </section>

      <section>
        <h2 className="mb-1 text-lg font-semibold">Google services</h2>
        <p className="mb-3 text-sm text-slate-400">
          Connect Gmail, Calendar and Drive so the assistant can act on them — permissions apply
          only to your account.
        </p>
        {googleFlash === "connected" && (
          <p className="mb-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-300">
            Google services connected successfully.
          </p>
        )}
        {(googleFlash === "failed" || googleFlash === "denied") && (
          <p className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            Google connection {googleFlash}. Try again.
          </p>
        )}
        <Card className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="font-medium">Gmail · Calendar · Drive</span>
            {me.google_connected ? <Badge tone="ok">connected</Badge> : <Badge>not connected</Badge>}
          </div>
          {me.google_connected ? (
            <Button variant="secondary" onClick={disconnectGoogle}>Disconnect</Button>
          ) : (
            <Button onClick={connectGoogle}>Connect Google</Button>
          )}
        </Card>
      </section>

      <section>
        <h2 className="mb-1 text-lg font-semibold">API keys</h2>
        <p className="mb-3 text-sm text-slate-400">
          Stored encrypted per account. Saved values are never shown again.
        </p>
        {me.gemini && (
          <p className="mb-3 flex flex-wrap items-center gap-2 text-sm text-slate-400">
            Assistant model: <Badge tone={me.gemini.limited ? "warn" : "ok"}>{me.gemini.model}</Badge>
            {me.gemini.limited && (
              <span className="text-xs text-amber-400">
                free-tier key — daily limits apply; enable billing and re-save your key for the stronger model
              </span>
            )}
          </p>
        )}
        <Card className="space-y-5">
          {me.secrets.map((s) => (
            <SecretField key={s.key} secret={s} onSaved={refresh} />
          ))}
        </Card>
      </section>

      <section>
        <h2 className="mb-3 text-lg font-semibold">Modules</h2>
        <ModuleToggleGrid modules={me.modules} onToggle={toggleModule} />
      </section>

      {!me.tenant.is_owner && (
        <section>
          <h2 className="mb-1 text-lg font-semibold text-red-400">Danger zone</h2>
          <Card className="border-red-500/30">
            <p className="text-sm text-slate-400">
              Permanently delete your account and every piece of your data: memory, conversations,
              API keys and files. This cannot be undone.
            </p>
            <div className="mt-3 flex gap-2">
              {confirmDelete ? (
                <>
                  <Button variant="danger" onClick={deleteAccount}>Yes, delete everything</Button>
                  <Button variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Button>
                </>
              ) : (
                <Button variant="danger" onClick={() => setConfirmDelete(true)}>Delete my account</Button>
              )}
            </div>
          </Card>
        </section>
      )}
    </div>
  );
}
