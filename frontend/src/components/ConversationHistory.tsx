import { useState } from "react";
import { api } from "../lib/api";
import { cn } from "./ui";

interface Conversation {
  id: string;
  title: string | null;
  updated_at: string | null;
}

export interface HistoryMessage {
  role: "user" | "assistant";
  content: string;
}

// "New chat" + a dropdown of past conversations; opening one reloads its
// messages and continues that thread.
export function ConversationHistory({
  activeId,
  onOpen,
  onNew,
}: {
  activeId: string;
  onOpen: (chatId: string, messages: HistoryMessage[]) => void;
  onNew: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Conversation[] | null>(null);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next) {
      setItems((await api.get<{ conversations: Conversation[] }>("/api/conversations")).conversations);
    }
  };

  const openOne = async (id: string) => {
    const { messages } = await api.get<{ messages: HistoryMessage[] }>(
      `/api/conversations/${encodeURIComponent(id)}/messages`,
    );
    setOpen(false);
    onOpen(id, messages);
  };

  const remove = async (id: string) => {
    await api.delete(`/api/conversations/${encodeURIComponent(id)}`);
    setItems((prev) => prev?.filter((c) => c.id !== id) ?? null);
    if (id === activeId) onNew();
  };

  return (
    <div className="relative flex items-center gap-1">
      <button onClick={onNew} className="rounded-full px-2.5 py-0.5 text-xs text-slate-400 hover:bg-slate-900">
        ＋ New chat
      </button>
      <button onClick={toggle} className="rounded-full px-2.5 py-0.5 text-xs text-slate-400 hover:bg-slate-900">
        🕘 History
      </button>
      {open && (
        <div className="absolute left-0 top-6 z-20 max-h-80 w-72 overflow-y-auto rounded-lg border border-slate-800 bg-slate-950 p-1 shadow-xl">
          {items === null && <div className="p-2 text-xs text-slate-500">Loading…</div>}
          {items?.length === 0 && <div className="p-2 text-xs text-slate-500">No conversations yet.</div>}
          {items?.map((c) => (
            <div
              key={c.id}
              className={cn(
                "group flex items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-slate-900",
                c.id === activeId && "bg-slate-900",
              )}
            >
              <button dir="auto" onClick={() => openOne(c.id)} className="flex-1 truncate text-left text-slate-300">
                {c.title || "Conversation"}
              </button>
              <span className="shrink-0 text-slate-600">
                {c.updated_at ? new Date(c.updated_at).toLocaleDateString() : ""}
              </span>
              <button
                onClick={() => remove(c.id)}
                className="shrink-0 text-slate-600 opacity-0 hover:text-red-400 group-hover:opacity-100"
                aria-label="Delete conversation"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
