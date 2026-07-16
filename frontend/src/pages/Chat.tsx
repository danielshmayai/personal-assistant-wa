import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { ChatEvent } from "../lib/types";
import { api, ApiError } from "../lib/api";
import { useSession } from "../lib/session";
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
  const { me } = useSession();
  const [tierBannerDismissed, setTierBannerDismissed] = useState(
    () => sessionStorage.getItem("pa_tier_banner_dismissed") === "1",
  );
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<JSX.Element | string>("");
  const [attachment, setAttachment] = useState<Attachment | null>(null);
  const [uploading, setUploading] = useState(false);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [voiceReplies, setVoiceReplies] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const chatIdRef = useRef<string>("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const galleryRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const audioPlayerRef = useRef<HTMLAudioElement | null>(null);

  const ttsEnabled = !!me?.modules.find((m) => m.id === "tts")?.enabled;
  const voiceRepliesRef = useRef(false);
  useEffect(() => { voiceRepliesRef.current = voiceReplies; }, [voiceReplies]);

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
          if (voiceRepliesRef.current && event.full_reply) void playReply(event.full_reply);
          break;
        case "error":
          setBusy(false);
          setError(ERROR_TEXT[event.code] ?? ERROR_TEXT.internal);
          setMessages((prev) => prev.filter((m) => !(m.streaming && !m.text)));
          break;
        case "notification":
          // A scheduled reminder/automation fired while this tab was open.
          setMessages((prev) => [...prev, { role: "assistant", text: event.message }]);
          if (voiceRepliesRef.current) void playReply(event.message);
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
      audioPlayerRef.current?.pause();
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
    if (!file) {
      // Some mobile browsers fire onChange with an empty FileList in edge
      // cases (e.g. an iCloud photo still downloading) — surface it instead
      // of silently doing nothing, which looked like a dead button.
      setError("No file was received from the picker — please try again.");
      return;
    }
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

  const playReply = async (text: string) => {
    try {
      audioPlayerRef.current?.pause();
      const resp = await fetch(`/api/tts?text=${encodeURIComponent(text)}`, { credentials: "same-origin" });
      if (!resp.ok) return;
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioPlayerRef.current = audio;
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
    } catch {
      // Voice reply is a nice-to-have — never surface a TTS failure as a chat error.
    }
  };

  const startRecording = async () => {
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      audioChunksRef.current = [];
      recorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunksRef.current.push(e.data); };
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        void transcribe();
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      setError("Microphone access denied or unavailable.");
    }
  };

  const stopRecording = () => {
    mediaRecorderRef.current?.stop();
    setRecording(false);
  };

  const transcribe = async () => {
    const blob = new Blob(audioChunksRef.current, { type: mediaRecorderRef.current?.mimeType || "audio/webm" });
    if (blob.size === 0) return;
    setTranscribing(true);
    try {
      // iOS Safari records audio/mp4 (no webm support) — name the file to
      // match so the STT backend sniffs the container correctly.
      const ext = blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
      const file = new File([blob], `recording.${ext}`, { type: blob.type });
      const res = await api.upload<{ text: string }>("/api/stt", file);
      if (res.text) setInput((prev) => (prev ? `${prev} ${res.text}` : res.text));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Transcription failed");
    } finally {
      setTranscribing(false);
    }
  };

  const gemini = me?.gemini;
  const isFallback = gemini?.engine === "ollama";
  const showTierWarning = (gemini?.limited || isFallback) && !tierBannerDismissed;

  return (
    <div className="flex h-full flex-col">
      {/* Always visible — which model is actually answering right now, per
          product policy: the tenant should never have to guess. */}
      {gemini && (
        <div className="flex items-center justify-between gap-2 border-b border-slate-800 px-3 py-1 text-[11px] text-slate-500">
          <span className="flex items-center gap-1.5">
            <span className={cn("h-1.5 w-1.5 rounded-full", isFallback ? "bg-amber-400" : "bg-emerald-400")} />
            Model: <b className="text-slate-400">{gemini.model}</b>
            {isFallback && " (local fallback — no tools)"}
          </span>
          {ttsEnabled && (
            <button
              onClick={() => setVoiceReplies((v) => !v)}
              className={cn(
                "flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs",
                voiceReplies ? "bg-indigo-600/15 text-indigo-300" : "text-slate-500 hover:bg-slate-900",
              )}
              aria-label="Toggle voice replies"
            >
              {voiceReplies ? "🔊" : "🔇"} Voice replies
            </button>
          )}
        </div>
      )}
      {showTierWarning && (
        <div className="flex items-start gap-2 border-b border-amber-500/20 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          <span className="flex-1">
            {isFallback ? (
              <>
                Your Gemini key doesn't work on any supported model, so you're running on a
                local fallback model with <b>no tools</b> (no web search, email, memory,
                etc.) — just plain conversation. Add a valid key under{" "}
                <Link to="/settings" className="underline">Settings → Secrets</Link>{" "}
                to restore full functionality.
              </>
            ) : (
              <>
                Running on <b>{gemini!.model}</b> — your Gemini key is free-tier, so daily
                limits apply and answers may be slower. Enable billing on your Google project
                to unlock the stronger model, then re-save your key in{" "}
                <Link to="/settings" className="underline">Settings</Link>.
              </>
            )}
          </span>
          <button
            onClick={() => {
              setTierBannerDismissed(true);
              sessionStorage.setItem("pa_tier_banner_dismissed", "1");
            }}
            className="rounded px-1.5 text-amber-400 hover:bg-amber-500/20"
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      )}
      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto overscroll-contain p-4">
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

      <div className="border-t border-slate-800 p-3 pb-safe">
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
          {/* sr-only (not `hidden`/display:none) — some mobile browser engines
              don't reliably dispatch the picker or the change event to a
              display:none input triggered via ref.click(). */}
          <input
            ref={galleryRef} type="file" accept="image/*,application/pdf" className="sr-only"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              void attachFile(file);
            }}
          />
          <input
            ref={cameraRef} type="file" accept="image/*" capture="environment" className="sr-only"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              void attachFile(file);
            }}
          />
          <button
            disabled={!connected || transcribing}
            onClick={() => (recording ? stopRecording() : startRecording())}
            className={cn(
              "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border text-lg disabled:opacity-50",
              recording
                ? "border-red-500 bg-red-500/15 text-red-400 animate-pulse"
                : "border-slate-700 bg-slate-900 hover:bg-slate-800",
            )}
            aria-label={recording ? "Stop recording" : "Record voice message"}
          >
            {transcribing ? <Spinner className="h-4 w-4" /> : recording ? "⏹️" : "🎤"}
          </button>
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
