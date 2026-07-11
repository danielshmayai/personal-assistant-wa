import { NavLink, Outlet } from "react-router-dom";
import { useSession } from "../lib/session";
import { cn } from "../components/ui";

const MODULE_TABS: Record<string, { path: string; label: string; icon: string }> = {
  nutrition: { path: "/nutrition", label: "Nutrition", icon: "🥗" },
  fitness: { path: "/fitness", label: "Fitness", icon: "💪" },
  "smart-home": { path: "/smart-home", label: "Smart Home", icon: "🏠" },
};

export default function Dashboard() {
  const { me } = useSession();
  if (!me) return null;

  const enabledIds = new Set(me.modules.filter((m) => m.enabled).map((m) => m.id));
  const tabs = [
    { path: "/", label: "Chat", icon: "💬" },
    ...Object.entries(MODULE_TABS)
      .filter(([id]) => enabledIds.has(id))
      .map(([, tab]) => tab),
    ...(me.tenant.is_owner ? [{ path: "/admin", label: "Admin", icon: "🛡️" }] : []),
    { path: "/settings", label: "Settings", icon: "⚙️" },
  ];

  return (
    <div className="flex h-full">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-slate-800 bg-slate-950 p-3 sm:flex">
        <div className="mb-6 flex items-center gap-2 px-2 pt-2">
          <span className="text-2xl">⚡</span>
          <span className="font-semibold tracking-tight">Assistant</span>
        </div>
        <nav className="flex flex-col gap-1">
          {tabs.map((tab) => (
            <NavLink
              key={tab.path}
              to={tab.path}
              end={tab.path === "/"}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                  isActive ? "bg-indigo-600/15 text-indigo-300" : "text-slate-400 hover:bg-slate-900 hover:text-slate-200",
                )
              }
            >
              <span>{tab.icon}</span>
              {tab.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto px-2 pb-2 text-xs text-slate-600">
          {me.tenant.email}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile top nav */}
        <nav className="flex gap-1 overflow-x-auto border-b border-slate-800 p-2 sm:hidden">
          {tabs.map((tab) => (
            <NavLink
              key={tab.path}
              to={tab.path}
              end={tab.path === "/"}
              className={({ isActive }) =>
                cn(
                  "whitespace-nowrap rounded-lg px-3 py-1.5 text-sm",
                  isActive ? "bg-indigo-600/15 text-indigo-300" : "text-slate-400",
                )
              }
            >
              {tab.icon} {tab.label}
            </NavLink>
          ))}
        </nav>
        <main className="min-h-0 flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
