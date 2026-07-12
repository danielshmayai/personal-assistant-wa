import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ChatEvent } from "../lib/types";
import { api, ApiError } from "../lib/api";
import { Button, Spinner, cn } from "../components/ui";

interface Message {
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
  toolNote?: string;
}

interface Attachment {
  mediaId: string;
  filename: string;
  mimeType: string;
  previewUrl: string;
}

const ERROR_TEXT: Record<string, JSX.Element | string> = {
  invalid_key: (
    <>
      Your Gemini API key was rejected. Add a valid key under{" "}
      <Link to="/settings" className="underline">Settings → Secrets</Link>
      {" "}— get one free at{" "}
      <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer" className="underline">
        Google AI Studio
      </a>.
    </>
  ),
  quota_exceeded: (
    <>
      Your Gemini key hit its usage limit. Free keys allow only ~20 requests/day —
      wait for it to reset, or enable billing on your key's Google project for higher
      limits. Manage it under{" "}
      <Link to="/settings" className="underline">Settings → Secrets</Link>.
    </>
  ),
  rate_limited: "You're sending messages a bit too fast — wait a few seconds and try again.",
  session_expired: (
    <>
      Your session expired. <a href="/login" className="underline">Sign in again</a> to continue.
    </>
  ),
  internal: "Something went wrong on our side. Please try again in a moment.",
};

const TOOL_LABELS: Record<string, string> = {
  web_search: "Searching the web",
  get_weather: "Checking the weather",
  gmail_read: "Reading Gmail",
  gmail_send: "Sending email",
  calendar_list: "Checking calendar",
  calendar_create: "Creating event",
  save_fact: "Saving to memory",
  retrieve_context: "Searching memory",
  search_vault: "Searching memory",
  log_meal: "Logging meal",
  log_water: "Logging water",
  log_workout: "Logging workout",
};

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<JSX.Element | string>("");
  const [attachment, setAttachment] = useState<Attachment | null>(null);
  const [uploading, setUploading] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const chatIdRef = useRef<string>("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const galleryRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);

  const connect = useCallback(() => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/chat`);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setError("");
    };
    ws.onclose = (ev) => {
      setConnected(false);
      setBusy(false);
      // Auth failures won't fix themselves by reconnecting — tell the user what
      // to do instead of looping silently (4401 = dead session, 4403 = onboarding).
      if (ev.code === 4401) {
        setError(ERROR_TEXT.session_expired);
        return;
      }
      if (ev.code === 4403) {
        window.location.href = "/onboarding";
        return;
      }
      // Transient drop → gentle auto-reconnect.
      setTimeout(() => {
        if (wsRef.current === ws) connect();
      }, 2500);
    };

    ws.onmessage = (raw) => {
      const event: ChatEvent = JSON.parse(raw.data);
      switch (event.type) {
        case "ack":
          chatIdRef.current = event.chat_id;
          break;
        case "token":
          setMessages((prev) => {
            const next = [...prev];
            const last = next[next.length - 1];
            if (last?.role === "assistant" && last.streaming) {
              next[next.length - 1] = { ...last, text: last.text + event.content, toolNote: undefined };
            } else {
              next.push({ role: "assistant", text: event.content, streaming: true });
            }
            return next;
          });
          break;
        case "tool_start":
          setMessages((prev) => {
            const next = [...prev];
            const note = TOOL_LABELS[event.name] ?? `Running ${event.name}`;
            const last = next[next.length - 1];
            if (last?.role === "assistant" && last.streaming) {
              next[next.length - 1] = { ...last, toolNote: note };
            } else {
              next.push({ role: "assistant", text: "", streaming: true, toolNote: note });
            }
            return next;
          });
          break;
        case "done":
          setBusy(false);
          setMessages((prev) =>
            prev.map((m, i) =>
              i === prev.length - 1 && m.role === "assistant"
                ? { ...m, streaming: false, toolNote: undefined, text: m.text || event.full_reply }
                : m,
            ),
          );
          break;
        case "error":
          setBusy(false);
          setError(ERROR_TEXT[event.code] ?? ERROR_TEXT.internal);
          setMessages((prev) => prev.filter((m) => !(m.streaming && !m.text)));
          break;
      }
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      const ws = wsRef.current;
      wsRef.current = null;
      ws?.close();
    };
  }, [connect]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // Revoke the object URL when the attachment changes or the page unmounts —
  // otherwise each picked photo leaks its blob URL for the tab's lifetime.
  useEffect(() => {
    return () => {
      if (attachment) URL.revokeObjectURL(attachment.previewUrl);
    };
  }, [attachment]);

  const clearAttachment = () => {
    setAttachment((prev) => {
      if (prev) URL.revokeObjectURL(prev.previewUrl);
      return null;
    });
  };

  const attachFile = async (file?: File) => {
    if (!file) return;
    setError("");
    setUploading(true);
    try {
      const res = await api.upload<{ media_id: string; filename: string; mime_type: string }>(
        "/api/upload",
        file,
      );
      clearAttachment();
      setAttachment({
        mediaId: res.media_id,
        filename: res.filename,
        mimeType: res.mime_type,
        previewUrl: URL.createObjectURL(file),
      });
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const send = () => {
    const text = input.trim();
    if ((!text && !attachment) || !connected || busy) return;
    setError("");
    setBusy(true);
    setMessages((prev) => [
      ...prev,
      { role: "user", text: text || (attachment ? `📎 ${attachment.filename}` : "") },
    ]);
    wsRef.current?.send(
      JSON.stringify({
        type: "message",
        text,
        media_id: attachment?.mediaId,
        chat_id: chatIdRef.current || undefined,
      }),
    );
    setInput("");
    clearAttachment();
  };

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center text-center text-slate-500">
            <div className="text-4xl">⚡</div>
            <p className="mt-3 max-w-sm text-sm">
              Your assistant is ready. Ask anything — it remembers what matters to you.
            </p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
            <div
              dir="auto"
              className={cn(
                "max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
                m.role === "user"
                  ? "bg-indigo-600 text-white"
                  : "border border-slate-800 bg-slate-900",
              )}
            >
              {m.text}
              {m.streaming && (
                <span className="mt-1 flex items-center gap-2 text-xs text-slate-400">
                  <Spinner className="h-3 w-3" />
                  {m.toolNote ?? (m.text ? "" : "Thinking…")}
                </span>
              )}
            </div>
          </div>
        ))}
        {error && (
          <div className="mx-auto max-w-md rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-300">
            {error}
          </div>
        )}
      </div>

      <div className="border-t border-slate-800 p-3">
        {attachment && (
          <div className="mb-2 flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm">
            {attachment.mimeType.startsWith("image/") ? (
              <img src={attachment.previewUrl} alt="" className="h-10 w-10 rounded-md object-cover" />
            ) : (
              <span className="flex h-10 w-10 items-center justify-center rounded-md bg-slate-800 text-lg">📄</span>
            )}
            <span className="flex-1 truncate text-slate-300">{attachment.filename}</span>
            <button
              onClick={clearAttachment}
              className="rounded-full px-2 py-0.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              aria-label="Remove attachment"
            >
              ✕
            </button>
          </div>
        )}
        <div className="flex items-end gap-2">
          <button
            disabled={!connected || uploading}
            onClick={() => galleryRef.current?.click()}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-slate-700 bg-slate-900 text-lg hover:bg-slate-800 disabled:opacity-50"
            aria-label="Attach image"
          >
            {uploading ? <Spinner className="h-4 w-4" /> : "🖼️"}
          </button>
          <button
            disabled={!connected || uploading}
            onClick={() => cameraRef.current?.click()}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-slate-700 bg-slate-900 text-lg hover:bg-slate-800 disabled:opacity-50"
            aria-label="Take photo"
          >
            📷
          </button>
          <input
            ref={galleryRef} type="file" accept="image/*,application/pdf" hidden
            onChange={(e) => { void attachFile(e.target.files?.[0]); e.target.value = ""; }}
          />
          <input
            ref={cameraRef} type="file" accept="image/*" capture="environment" hidden
            onChange={(e) => { void attachFile(e.target.files?.[0]); e.target.value = ""; }}
          />
          <textarea
            dir="auto"
            rows={1}
            value={input}
            placeholder={connected ? "Message your assistant…" : "Connecting…"}
            disabled={!connected}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            className="max-h-40 flex-1 resize-none rounded-xl border border-slate-700 bg-slate-900 px-4 py-2.5 text-sm placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
          />
          <Button onClick={send} disabled={!connected || busy || (!input.trim() && !attachment)}>
            {busy ? <Spinner className="h-4 w-4 border-white/40 border-t-white" /> : "Send"}
          </Button>
        </div>
      </div>
    </div>
  );
}
