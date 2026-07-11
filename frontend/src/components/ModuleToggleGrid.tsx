import type { ModuleInfo } from "../lib/types";
import { Badge, Card, Switch, cn } from "./ui";

export function ModuleToggleGrid({
  modules,
  onToggle,
  disabled,
}: {
  modules: ModuleInfo[];
  onToggle: (id: string, enabled: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {modules.map((m) => {
        const locked = m.core || !m.available;
        return (
          <Card
            key={m.id}
            className={cn("flex items-start justify-between gap-3", !m.available && "opacity-60")}
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{m.label}</span>
                {m.core && <Badge tone="info">core</Badge>}
                {!m.available && <Badge>coming soon</Badge>}
                {m.enabled && m.missing_secrets.length > 0 && (
                  <Badge tone="warn">needs setup</Badge>
                )}
                {m.needs_oauth && <Badge tone="info">Google</Badge>}
              </div>
              <p className="mt-1 text-sm text-slate-400">{m.description}</p>
            </div>
            <Switch
              checked={m.enabled}
              disabled={disabled || locked}
              onChange={(next) => onToggle(m.id, next)}
            />
          </Card>
        );
      })}
    </div>
  );
}
