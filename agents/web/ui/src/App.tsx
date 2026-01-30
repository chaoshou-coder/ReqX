import { useEffect, useMemo, useRef, useState } from "react";
import { apiJson } from "./api";
import { normalizeLang, t } from "./i18n";
import type { ChatMessage, ChatWireMessage, Lang } from "./types";
import { cn } from "./utils/cn";

type View = "chat" | "knowledge" | "config" | "prompt";

type ApiOk<T> = { ok: true; result: T };
type ApiFail = { ok: false; error?: { code?: string; message?: string } };

type KnowledgeSnap = Record<string, unknown>;

type ConfigReadResp = ApiOk<{ path: string; content: string; warning?: string | null }> | ApiFail;
type ConfigWriteResp = ApiOk<{ path: string; dry_run: boolean }> | ApiFail;
type KnowledgeReadResp = ApiOk<KnowledgeSnap> | ApiFail;
type DoctorResp = ApiOk<Record<string, unknown>> | ApiFail;
type PromptReadResp = ApiOk<{ content: string; prompt_version?: string; warning?: string | null }> | ApiFail;
type PromptWriteResp = ApiOk<{ dry_run: boolean }> | ApiFail;
type ChatSendResp = ApiOk<{ reply: string; knowledge_appended: number; dry_run: boolean }> | ApiFail;

type Toast = { id: string; text: string; kind: "info" | "error" };

type ChatSession = {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
};

const SESSIONS_KEY = "reqx_sessions_v1";
const ACTIVE_SESSION_KEY = "reqx_activeSessionId";

function makeId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `m_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
}

function nowMs(): number {
  return Date.now();
}

function loadSessions(): { sessions: ChatSession[]; activeId: string } {
  const raw = localStorage.getItem(SESSIONS_KEY) || "";
  const active = localStorage.getItem(ACTIVE_SESSION_KEY) || "";
  try {
    const parsed = JSON.parse(raw || "[]") as ChatSession[];
    const sessions = Array.isArray(parsed) ? parsed : [];
    const activeId = sessions.some((s) => s.id === active) ? active : sessions[0]?.id || "";
    if (sessions.length > 0 && activeId) return { sessions, activeId };
  } catch {
    // ignore
  }
  const id = makeId();
  const s: ChatSession = { id, title: t("zh-CN", "new_chat"), createdAt: nowMs(), updatedAt: nowMs(), messages: [] };
  return { sessions: [s], activeId: id };
}

function saveSessions(sessions: ChatSession[], activeId: string) {
  localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions));
  localStorage.setItem(ACTIVE_SESSION_KEY, activeId);
}

async function copyText(text: string) {
  const v = text ?? "";
  if (!v) return;
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(v);
    return;
  }
  const ta = document.createElement("textarea");
  ta.value = v;
  ta.style.position = "fixed";
  ta.style.left = "-9999px";
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  document.body.removeChild(ta);
}

function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ?? false;
}

function renderAssistant(lang: Lang, content: string, onCopyCode: (code: string) => void) {
  const raw = content ?? "";
  const chunks = raw.split("```");
  const nodes: React.ReactNode[] = [];

  for (let i = 0; i < chunks.length; i++) {
    const text = chunks[i] ?? "";
    if (i % 2 === 1) {
      const code = text.replace(/^\n+|\n+$/g, "");
      nodes.push(
        <div key={`code-${i}`} className="mt-3 overflow-hidden rounded-2xl border border-[#2a2b2e] bg-[#1e1f20]">
          <div className="flex items-center justify-between gap-2 border-b border-[#2a2b2e] px-4 py-2">
            <div className="text-[12px] text-[#c4c7c5]">{t(lang, "code_block")}</div>
            <button
              type="button"
              onClick={() => onCopyCode(code)}
              className="rounded-full px-3 py-1 text-[12px] text-[#c4c7c5] hover:bg-[#2d2e31] hover:text-[#e3e3e3]"
            >
              {t(lang, "copy")}
            </button>
          </div>
          <pre className="overflow-x-auto p-4 text-sm text-[#e3e3e3]">
            <code className="whitespace-pre">{code}</code>
          </pre>
        </div>
      );
      continue;
    }
    const paras = text
      .replace(/\r\n/g, "\n")
      .split(/\n{2,}/g)
      .map((p) => p.trim())
      .filter(Boolean);
    for (let j = 0; j < paras.length; j++) {
      nodes.push(
        <p key={`p-${i}-${j}`} className="whitespace-pre-wrap text-[15px] leading-7 text-[#e3e3e3]">
          {paras[j]}
        </p>
      );
    }
  }

  return <div className="space-y-3">{nodes}</div>;
}

export function App() {
  const [view, setView] = useState<View>("chat");
  const [lang, setLang] = useState<Lang>(() => normalizeLang(localStorage.getItem("reqx_lang") || "zh-CN"));

  const [authToken, setAuthToken] = useState<string>(() => localStorage.getItem("reqx_authToken") || "");
  const [cfgPath, setCfgPath] = useState<string>(() => localStorage.getItem("reqx_cfgPath") || "llm.yaml");
  const [knowledgePath, setKnowledgePath] = useState<string>(() => localStorage.getItem("reqx_knowledgePath") || "project_knowledge.db");
  const [dryRun, setDryRun] = useState<boolean>(() => (localStorage.getItem("reqx_dryRun") || "") === "1");

  const [cfgContent, setCfgContent] = useState<string>("");
  const [doctorOut, setDoctorOut] = useState<string>("");
  const [knowledgeSnapshot, setKnowledgeSnapshot] = useState<string>("");
  const [promptContent, setPromptContent] = useState<string>("");

  const [{ sessions, activeId }, setSessionState] = useState(() => loadSessions());
  const [input, setInput] = useState<string>("");
  const [statusText, setStatusText] = useState<string>(t(lang, "status_ready"));
  const [statusOk, setStatusOk] = useState<boolean>(true);
  const [busy, setBusy] = useState<boolean>(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [showJump, setShowJump] = useState<boolean>(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<string>("");

  const listRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const animateAbortRef = useRef<{ aborted: boolean } | null>(null);

  useEffect(() => {
    localStorage.setItem("reqx_lang", lang);
  }, [lang]);
  useEffect(() => {
    localStorage.setItem("reqx_authToken", authToken);
  }, [authToken]);
  useEffect(() => {
    localStorage.setItem("reqx_cfgPath", cfgPath);
  }, [cfgPath]);
  useEffect(() => {
    localStorage.setItem("reqx_knowledgePath", knowledgePath);
  }, [knowledgePath]);
  useEffect(() => {
    localStorage.setItem("reqx_dryRun", dryRun ? "1" : "");
  }, [dryRun]);

  useEffect(() => {
    setStatusText(t(lang, "status_ready"));
    setStatusOk(true);
  }, [lang]);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      setShowJump(distance > 280);
    };
    onScroll();
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    saveSessions(sessions, activeId);
  }, [sessions, activeId]);

  const navItems = useMemo(
    () =>
      [
        { key: "chat" as const, label: t(lang, "nav_chat") },
        { key: "knowledge" as const, label: t(lang, "nav_knowledge") },
        { key: "config" as const, label: t(lang, "nav_config") },
        { key: "prompt" as const, label: t(lang, "nav_prompt") },
      ] as const,
    [lang]
  );

  const activeSession = useMemo(() => sessions.find((s) => s.id === activeId) || sessions[0], [sessions, activeId]);
  const messages = activeSession?.messages || [];

  function pushToast(text: string, kind: Toast["kind"] = "info") {
    const id = makeId();
    setToasts((prev) => [...prev, { id, text, kind }]);
    window.setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 2600);
  }

  function updateActiveMessages(updater: (prev: ChatMessage[]) => ChatMessage[]) {
    setSessionState((prev) => {
      const nextSessions = prev.sessions.map((s) => {
        if (s.id !== prev.activeId) return s;
        const nextMsgs = updater(s.messages || []);
        return { ...s, messages: nextMsgs, updatedAt: nowMs(), title: s.title || t(lang, "new_chat") };
      });
      return { ...prev, sessions: nextSessions };
    });
  }

  function setActiveMessages(nextMsgs: ChatMessage[]) {
    updateActiveMessages(() => nextMsgs);
  }

  function setActiveSession(nextActiveId: string) {
    setSessionState((prev) => {
      if (prev.activeId === nextActiveId) return prev;
      if (!prev.sessions.some((s) => s.id === nextActiveId)) return prev;
      return { ...prev, activeId: nextActiveId };
    });
    setEditingId(null);
    setEditDraft("");
    setTimeout(() => inputRef.current?.focus(), 0);
  }

  function newChat() {
    const id = makeId();
    const s: ChatSession = { id, title: t(lang, "new_chat"), createdAt: nowMs(), updatedAt: nowMs(), messages: [] };
    setSessionState((prev) => ({ sessions: [s, ...prev.sessions], activeId: id }));
    setEditingId(null);
    setEditDraft("");
    setTimeout(() => inputRef.current?.focus(), 0);
  }

  function deleteChat(id: string) {
    setSessionState((prev) => {
      const next = prev.sessions.filter((s) => s.id !== id);
      if (next.length === 0) {
        const nid = makeId();
        const s: ChatSession = { id: nid, title: t(lang, "new_chat"), createdAt: nowMs(), updatedAt: nowMs(), messages: [] };
        return { sessions: [s], activeId: nid };
      }
      const nextActive = prev.activeId === id ? next[0].id : prev.activeId;
      return { sessions: next, activeId: nextActive };
    });
  }

  function renameChat(id: string, title: string) {
    const v = title.trim();
    if (!v) return;
    setSessionState((prev) => ({
      ...prev,
      sessions: prev.sessions.map((s) => (s.id === id ? { ...s, title: v, updatedAt: nowMs() } : s)),
    }));
  }

  async function animateAssistantText(messageId: string, full: string) {
    const reduce = prefersReducedMotion();
    if (reduce) {
      updateActiveMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, content: full, status: "done" } : m)));
      return;
    }
    const abort = { aborted: false };
    animateAbortRef.current = abort;
    updateActiveMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, content: "", status: "pending" } : m)));
    const src = full ?? "";
    let i = 0;
    const step = () => {
      if (abort.aborted) return;
      i = Math.min(src.length, i + Math.max(2, Math.floor(src.length / 80)));
      const chunk = src.slice(0, i);
      updateActiveMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, content: chunk, status: i >= src.length ? "done" : "pending" } : m)));
      if (i >= src.length) return;
      window.setTimeout(step, 12);
    };
    step();
  }

  function toWireMessages(ms: ChatMessage[]): ChatWireMessage[] {
    return (ms || []).map((m) => ({ role: m.role, content: m.content || "" }));
  }

  async function sendWithMessages(nextMsgs: ChatMessage[], opts?: { autoTitle?: boolean }) {
    if (busy) return;
    setBusy(true);
    setStatusText(t(lang, "status_thinking"));
    setStatusOk(true);

    setActiveMessages(nextMsgs);

    const pendingId = makeId();
    updateActiveMessages((prev) => [...prev, { id: pendingId, role: "assistant", content: "", status: "pending" }]);

    const r = await apiJson<{ reply: string; knowledge_appended: number; dry_run: boolean }>(
      "POST",
      "/v1/chat/send",
      {
        config_path: cfgPath || null,
        knowledge_path: knowledgePath || null,
        dry_run: dryRun,
        imported_context: "",
        messages: toWireMessages(nextMsgs),
      },
      authToken
    );
    const payload = r as ChatSendResp;
    if (!payload.ok) {
      updateActiveMessages((prev) => prev.filter((m) => m.id !== pendingId));
      setStatusOk(false);
      setStatusText(`${t(lang, "status_error")}: ${payload.error?.code || "unknown"}`);
      pushToast(`${t(lang, "status_error")}: ${payload.error?.code || "unknown"}`, "error");
      setBusy(false);
      return;
    }

    const reply = payload.result.reply || "";
    await animateAssistantText(pendingId, reply);
    if (payload.result.knowledge_appended > 0) {
      await refreshKnowledge();
    }

    if (opts?.autoTitle && nextMsgs.length > 0) {
      const firstUser = nextMsgs.find((m) => m.role === "user")?.content?.trim() || "";
      const title = firstUser ? firstUser.slice(0, 24) : t(lang, "new_chat");
      renameChat(activeId, title);
    }

    setStatusText(t(lang, "status_ready"));
    setBusy(false);
  }

  async function sendSlashCommand(cmd: string) {
    const text = cmd.trim();
    if (!text || busy) return;
    if (text === "/reset") {
      setActiveMessages([]);
      animateAbortRef.current && (animateAbortRef.current.aborted = true);
      setStatusOk(true);
      setStatusText(t(lang, "status_done"));
      return;
    }
    setInput("");
    await sendWithMessages([...messages, { id: makeId(), role: "user", content: text, status: "done" }], { autoTitle: true });
  }

  async function refreshKnowledge() {
    setBusy(true);
    setStatusText(t(lang, "status_thinking"));
    setStatusOk(true);
    const r = await apiJson<KnowledgeSnap>("POST", "/v1/knowledge/read", { knowledge_path: knowledgePath || null }, authToken);
    const payload = r as KnowledgeReadResp;
    if (!payload.ok) {
      setStatusOk(false);
      setStatusText(`${t(lang, "status_error")}: ${payload.error?.code || "unknown"}`);
      pushToast(`${t(lang, "status_error")}: ${payload.error?.code || "unknown"}`, "error");
      setBusy(false);
      return;
    }
    setKnowledgeSnapshot(JSON.stringify(payload.result, null, 2));
    setStatusText(t(lang, "status_done"));
    setBusy(false);
  }

  async function loadCfg() {
    setBusy(true);
    setStatusText(t(lang, "status_thinking"));
    setStatusOk(true);
    const r = await apiJson<{ path: string; content: string; warning?: string | null }>(
      "POST",
      "/v1/config/read",
      { path: cfgPath || null },
      authToken
    );
    const payload = r as ConfigReadResp;
    if (!payload.ok) {
      setStatusOk(false);
      setStatusText(`${t(lang, "status_error")}: ${payload.error?.code || "unknown"}`);
      pushToast(`${t(lang, "status_error")}: ${payload.error?.code || "unknown"}`, "error");
      setBusy(false);
      return;
    }
    setCfgContent(payload.result.content || "");
    setStatusText(t(lang, "status_done"));
    setBusy(false);
  }

  async function saveCfg() {
    setBusy(true);
    setStatusText(t(lang, "status_thinking"));
    setStatusOk(true);
    const r = await apiJson<{ path: string; dry_run: boolean }>(
      "POST",
      "/v1/config/write",
      { path: cfgPath || null, content: cfgContent || "", dry_run: dryRun },
      authToken
    );
    const payload = r as ConfigWriteResp;
    if (!payload.ok) {
      setStatusOk(false);
      setStatusText(`${t(lang, "status_error")}: ${payload.error?.code || "unknown"}`);
      pushToast(`${t(lang, "status_error")}: ${payload.error?.code || "unknown"}`, "error");
      setBusy(false);
      return;
    }
    setStatusText(payload.result.dry_run ? t(lang, "dry_run") : t(lang, "status_done"));
    setBusy(false);
  }

  async function doctor() {
    setBusy(true);
    setStatusText(t(lang, "status_thinking"));
    setStatusOk(true);
    const r = await apiJson<Record<string, unknown>>("POST", "/v1/config/doctor", { path: cfgPath || null }, authToken);
    const payload = r as DoctorResp;
    if (!payload.ok) {
      setDoctorOut("");
      setStatusOk(false);
      setStatusText(`${t(lang, "status_error")}: ${payload.error?.code || "unknown"}`);
      pushToast(`${t(lang, "status_error")}: ${payload.error?.code || "unknown"}`, "error");
      setBusy(false);
      return;
    }
    setDoctorOut(JSON.stringify(payload.result, null, 2));
    setStatusText(t(lang, "status_done"));
    setBusy(false);
  }

  async function loadPrompt() {
    setBusy(true);
    setStatusText(t(lang, "status_thinking"));
    setStatusOk(true);
    const r = await apiJson<{ content: string; prompt_version?: string; warning?: string | null }>(
      "POST",
      "/v1/prompt/read",
      {},
      authToken
    );
    const payload = r as PromptReadResp;
    if (!payload.ok) {
      setStatusOk(false);
      setStatusText(`${t(lang, "status_error")}: ${payload.error?.code || "unknown"}`);
      pushToast(`${t(lang, "status_error")}: ${payload.error?.code || "unknown"}`, "error");
      setBusy(false);
      return;
    }
    setPromptContent(payload.result.content || "");
    setStatusText(t(lang, "status_done"));
    setBusy(false);
  }

  async function savePrompt() {
    setBusy(true);
    setStatusText(t(lang, "status_thinking"));
    setStatusOk(true);
    const r = await apiJson<{ dry_run: boolean }>(
      "POST",
      "/v1/prompt/write",
      { content: promptContent || "", dry_run: dryRun },
      authToken
    );
    const payload = r as PromptWriteResp;
    if (!payload.ok) {
      setStatusOk(false);
      setStatusText(`${t(lang, "status_error")}: ${payload.error?.code || "unknown"}`);
      pushToast(`${t(lang, "status_error")}: ${payload.error?.code || "unknown"}`, "error");
      setBusy(false);
      return;
    }
    setStatusText(payload.result.dry_run ? t(lang, "dry_run") : t(lang, "status_done"));
    setBusy(false);
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    animateAbortRef.current && (animateAbortRef.current.aborted = true);
    await sendWithMessages([...messages, { id: makeId(), role: "user", content: text, status: "done" }], { autoTitle: true });
  }

  useEffect(() => {
    refreshKnowledge().catch(() => {});
    loadCfg().catch(() => {});
    loadPrompt().catch(() => {});
  }, []);

  return (
    <div className="flex h-full">
      <aside className="w-[320px] flex-none bg-[#1e1f20] text-[#e3e3e3]">
        <div className="flex h-full flex-col p-4">
          <div className="flex items-center gap-3 px-2 py-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#0b57d0] text-sm font-semibold text-white">
              R
            </div>
            <div className="text-[15px] font-medium">{t(lang, "app_name")}</div>
            <div className="ml-auto flex items-center gap-2">
              <button
                type="button"
                onClick={() => newChat()}
                className="rounded-full bg-[#2d2e31] px-3 py-1.5 text-[12px] text-[#e3e3e3] hover:bg-[#3a3b3e]"
              >
                {t(lang, "new_chat")}
              </button>
            </div>
          </div>

          <div className="mt-2 min-h-0 flex-1">
            <div className="reqx-fade-in text-[12px] text-[#c4c7c5]">{t(lang, "chat_history")}</div>
            <div className="mt-2 max-h-[240px] overflow-y-auto pr-1">
              <div className="space-y-1">
                {sessions.map((s) => (
                  <div key={s.id} className="group flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setActiveSession(s.id)}
                      className={cn(
                        "min-w-0 flex-1 rounded-2xl px-3 py-2 text-left text-[13px] transition-colors",
                        s.id === activeId ? "bg-[#2d2e31] text-[#e3e3e3]" : "text-[#c4c7c5] hover:bg-[#2d2e31]/60 hover:text-[#e3e3e3]"
                      )}
                      title={s.title}
                    >
                      <div className="truncate">{s.title || t(lang, "new_chat")}</div>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        const v = prompt(t(lang, "rename_chat_prompt"), s.title || "");
                        if (v) renameChat(s.id, v);
                      }}
                      className="invisible rounded-xl px-2 py-2 text-[#c4c7c5] hover:bg-[#2d2e31] group-hover:visible"
                      aria-label={t(lang, "rename")}
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                        <path
                          d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0 0-3L16.5 4.5a2.1 2.1 0 0 0-3 0L3 15v5z"
                          stroke="currentColor"
                          strokeWidth="1.6"
                        />
                      </svg>
                    </button>
                    <button
                      type="button"
                      onClick={() => deleteChat(s.id)}
                      className="invisible rounded-xl px-2 py-2 text-[#c4c7c5] hover:bg-[#2d2e31] group-hover:visible"
                      aria-label={t(lang, "delete")}
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                        <path d="M4 7h16" stroke="currentColor" strokeWidth="1.6" />
                        <path d="M10 11v6" stroke="currentColor" strokeWidth="1.6" />
                        <path d="M14 11v6" stroke="currentColor" strokeWidth="1.6" />
                        <path d="M6 7l1 14h10l1-14" stroke="currentColor" strokeWidth="1.6" />
                        <path d="M9 7V4h6v3" stroke="currentColor" strokeWidth="1.6" />
                      </svg>
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="mt-2 space-y-1">
            {navItems.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setView(item.key)}
                className={cn(
                  "w-full rounded-full px-4 py-3 text-left text-[14px] font-medium transition-colors",
                  view === item.key ? "bg-[#2d2e31] text-[#e3e3e3]" : "text-[#c4c7c5] hover:bg-[#2d2e31]/60 hover:text-[#e3e3e3]"
                )}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="mt-6 space-y-4 px-2">
            <div className="space-y-2">
              <div className="text-[12px] text-[#c4c7c5]">{t(lang, "token")}</div>
              <input
                value={authToken}
                onChange={(e) => setAuthToken(e.target.value)}
                type="password"
                className="w-full rounded-xl border border-[#2a2b2e] bg-transparent px-3 py-2 text-[13px] text-[#e3e3e3] outline-none focus:border-[#0b57d0]"
                placeholder={t(lang, "token_ph")}
              />
              <div className="text-[11px] leading-5 text-[#7b7d80]">{t(lang, "token_hint")}</div>
            </div>
            <div className="space-y-2">
              <div className="text-[12px] text-[#c4c7c5]">{t(lang, "cfg_path")}</div>
              <input
                value={cfgPath}
                onChange={(e) => setCfgPath(e.target.value)}
                className="w-full rounded-xl border border-[#2a2b2e] bg-transparent px-3 py-2 text-[13px] text-[#e3e3e3] outline-none focus:border-[#0b57d0]"
                placeholder="llm.yaml"
              />
            </div>
            <div className="space-y-2">
              <div className="text-[12px] text-[#c4c7c5]">{t(lang, "knowledge_path")}</div>
              <input
                value={knowledgePath}
                onChange={(e) => setKnowledgePath(e.target.value)}
                className="w-full rounded-xl border border-[#2a2b2e] bg-transparent px-3 py-2 text-[13px] text-[#e3e3e3] outline-none focus:border-[#0b57d0]"
                placeholder="project_knowledge.db"
              />
            </div>

            <label className="flex cursor-pointer items-center gap-2 text-[13px] text-[#c4c7c5]">
              <input checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} type="checkbox" />
              <span>{t(lang, "dry_run")}</span>
            </label>
          </div>

          <div className="mt-auto border-t border-[#2a2b2e] px-2 py-4">
            <div className="flex items-center justify-between gap-3">
              <div className="text-[12px] text-[#c4c7c5]">{t(lang, "lang_label")}</div>
              <select
                value={lang}
                onChange={(e) => setLang(normalizeLang(e.target.value))}
                className="rounded-xl border border-[#2a2b2e] bg-[#1e1f20] px-3 py-2 text-[13px] text-[#e3e3e3] outline-none focus:border-[#0b57d0]"
              >
                <option value="zh-CN">简体中文</option>
                <option value="en">English</option>
              </select>
            </div>
          </div>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col bg-[#131314]">
        {view === "chat" && (
          <div className="flex min-h-0 flex-1 flex-col">
            <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto px-6 py-10">
              <div className="mx-auto w-full max-w-3xl space-y-8">
                <div className="reqx-animate-slide-up flex gap-4">
                  <div className="mt-1 flex h-8 w-8 items-center justify-center rounded-full bg-[#0b57d0] text-sm font-semibold text-white">
                    R
                  </div>
                  <div className="space-y-2">
                    <div className="text-[18px] font-medium text-white">{t(lang, "welcome_title")}</div>
                    <div className="text-[14px] text-[#c4c7c5]">{t(lang, "welcome_desc")}</div>
                  </div>
                </div>

                {messages.length === 0 && (
                  <div className="reqx-animate-fade-in space-y-3">
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => sendSlashCommand("/spec")}
                        className="rounded-full bg-[#1e1f20] px-4 py-2 text-[13px] text-[#e3e3e3] hover:bg-[#2d2e31] disabled:opacity-60"
                      >
                        {t(lang, "try_spec")}
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          setInput(t(lang, "suggest_1"));
                          inputRef.current?.focus();
                        }}
                        className="rounded-full bg-[#1e1f20] px-4 py-2 text-[13px] text-[#e3e3e3] hover:bg-[#2d2e31] disabled:opacity-60"
                      >
                        {t(lang, "suggest_1")}
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          setInput(t(lang, "suggest_2"));
                          inputRef.current?.focus();
                        }}
                        className="rounded-full bg-[#1e1f20] px-4 py-2 text-[13px] text-[#e3e3e3] hover:bg-[#2d2e31] disabled:opacity-60"
                      >
                        {t(lang, "suggest_2")}
                      </button>
                    </div>
                  </div>
                )}

                {messages.map((m, idx) => (
                  <div
                    key={m.id}
                    className={cn("reqx-animate-slide-up group flex gap-4", m.role === "user" ? "flex-row-reverse" : "")}
                  >
                    <div
                      className={cn(
                        "mt-1 flex h-8 w-8 items-center justify-center rounded-full text-sm font-semibold",
                        m.role === "user" ? "bg-[#2d2e31] text-[#e3e3e3]" : "bg-[#0b57d0] text-white"
                      )}
                    >
                      {m.role === "user" ? "U" : "R"}
                    </div>
                    <div className={cn("min-w-0 flex-1", m.role === "user" ? "flex justify-end" : "")}>
                      {m.role === "user" ? (
                        <div className="max-w-[80%] space-y-2">
                          {editingId === m.id ? (
                            <div className="rounded-2xl bg-[#2d2e31] p-3">
                              <textarea
                                value={editDraft}
                                onChange={(e) => setEditDraft(e.target.value)}
                                className="h-[120px] w-full resize-none bg-transparent text-[14px] leading-6 text-[#e3e3e3] outline-none"
                              />
                              <div className="mt-2 flex justify-end gap-2">
                                <button
                                  type="button"
                                  onClick={() => {
                                    setEditingId(null);
                                    setEditDraft("");
                                  }}
                                  className="rounded-full bg-[#1e1f20] px-4 py-2 text-[12px] text-[#c4c7c5] hover:bg-[#2d2e31] hover:text-[#e3e3e3]"
                                >
                                  {t(lang, "cancel")}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => {
                                    const v = editDraft.trim();
                                    if (!v) return;
                                    animateAbortRef.current && (animateAbortRef.current.aborted = true);
                                    const at = messages.findIndex((x) => x.id === m.id);
                                    const base = at >= 0 ? messages.slice(0, at + 1) : messages;
                                    const edited = base.map((x) => (x.id === m.id ? { ...x, content: v } : x));
                                    setEditingId(null);
                                    setEditDraft("");
                                    sendWithMessages(edited).catch(() => {});
                                  }}
                                  className="rounded-full bg-[#0b57d0] px-4 py-2 text-[12px] text-white hover:opacity-95"
                                >
                                  {t(lang, "save_and_run")}
                                </button>
                              </div>
                            </div>
                          ) : (
                            <>
                              <div className="rounded-2xl bg-[#2d2e31] px-5 py-3 text-[15px] leading-7 text-[#e3e3e3]">
                                <div className="whitespace-pre-wrap">{m.content}</div>
                              </div>
                              <div className="flex justify-end">
                                <button
                                  type="button"
                                  onClick={() => {
                                    setEditingId(m.id);
                                    setEditDraft(m.content || "");
                                  }}
                                  className="invisible rounded-full px-3 py-1.5 text-[12px] text-[#c4c7c5] hover:bg-[#1e1f20] hover:text-[#e3e3e3] group-hover:visible"
                                >
                                  {t(lang, "edit")}
                                </button>
                              </div>
                            </>
                          )}
                        </div>
                      ) : (
                        <div className="min-w-0">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                              {renderAssistant(lang, m.content, async (code) => {
                                try {
                                  await copyText(code);
                                  pushToast(t(lang, "copied"));
                                } catch {
                                  pushToast(t(lang, "copy_failed"), "error");
                                }
                              })}
                            </div>
                            <div className="mt-1 flex flex-none items-center gap-1">
                              <button
                                type="button"
                                onClick={async () => {
                                  try {
                                    await copyText(m.content || "");
                                    pushToast(t(lang, "copied"));
                                  } catch {
                                    pushToast(t(lang, "copy_failed"), "error");
                                  }
                                }}
                                className="invisible rounded-full px-2 py-2 text-[#c4c7c5] hover:bg-[#1e1f20] hover:text-[#e3e3e3] group-hover:visible"
                                aria-label={t(lang, "copy")}
                              >
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                                  <path
                                    d="M8 8h11v13H8z"
                                    stroke="currentColor"
                                    strokeWidth="1.6"
                                    strokeLinejoin="round"
                                  />
                                  <path
                                    d="M5 16H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h11a1 1 0 0 1 1 1v1"
                                    stroke="currentColor"
                                    strokeWidth="1.6"
                                    strokeLinejoin="round"
                                  />
                                </svg>
                              </button>
                              <button
                                type="button"
                                onClick={() => {
                                  updateActiveMessages((prev) => prev.map((x) => (x.id === m.id ? { ...x, rating: x.rating === "up" ? null : "up" } : x)));
                                }}
                                className={cn(
                                  "invisible rounded-full px-2 py-2 hover:bg-[#1e1f20] group-hover:visible",
                                  m.rating === "up" ? "text-[#a8c7fa]" : "text-[#c4c7c5] hover:text-[#e3e3e3]"
                                )}
                                aria-label={t(lang, "thumb_up")}
                              >
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                                  <path
                                    d="M10 11V6a3 3 0 0 1 3-3l1 7h6a2 2 0 0 1 2 2l-2 7a2 2 0 0 1-2 1H8a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h2z"
                                    stroke="currentColor"
                                    strokeWidth="1.6"
                                    strokeLinejoin="round"
                                  />
                                  <path d="M6 11v9" stroke="currentColor" strokeWidth="1.6" />
                                </svg>
                              </button>
                              <button
                                type="button"
                                onClick={() => {
                                  updateActiveMessages((prev) =>
                                    prev.map((x) => (x.id === m.id ? { ...x, rating: x.rating === "down" ? null : "down" } : x))
                                  );
                                }}
                                className={cn(
                                  "invisible rounded-full px-2 py-2 hover:bg-[#1e1f20] group-hover:visible",
                                  m.rating === "down" ? "text-[#ff6b6b]" : "text-[#c4c7c5] hover:text-[#e3e3e3]"
                                )}
                                aria-label={t(lang, "thumb_down")}
                              >
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                                  <path
                                    d="M14 13v5a3 3 0 0 1-3 3l-1-7H4a2 2 0 0 1-2-2l2-7a2 2 0 0 1 2-1h10a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-2z"
                                    stroke="currentColor"
                                    strokeWidth="1.6"
                                    strokeLinejoin="round"
                                  />
                                  <path d="M18 13V4" stroke="currentColor" strokeWidth="1.6" />
                                </svg>
                              </button>
                              {idx === messages.length - 1 && messages[messages.length - 1]?.role === "assistant" && (
                                <button
                                  type="button"
                                  onClick={() => {
                                    const lastUserIdx = [...messages].reverse().findIndex((x) => x.role === "user");
                                    if (lastUserIdx < 0) return;
                                    const uidx = messages.length - 1 - lastUserIdx;
                                    const base = messages.slice(0, uidx + 1);
                                    animateAbortRef.current && (animateAbortRef.current.aborted = true);
                                    sendWithMessages(base).catch(() => {});
                                  }}
                                  className="invisible rounded-full px-2 py-2 text-[#c4c7c5] hover:bg-[#1e1f20] hover:text-[#e3e3e3] group-hover:visible"
                                >
                                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                                    <path
                                      d="M20 12a8 8 0 1 1-2.34-5.66"
                                      stroke="currentColor"
                                      strokeWidth="1.6"
                                      strokeLinecap="round"
                                    />
                                    <path d="M20 4v6h-6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
                                  </svg>
                                </button>
                              )}
                            </div>
                          </div>
                          {m.status === "pending" && (
                            <div className="mt-2 flex items-center gap-2 text-[12px] text-[#c4c7c5]">
                              <span className="reqx-dot-pulse inline-flex h-1.5 w-1.5 rounded-full bg-[#a8c7fa]" />
                              <span>{t(lang, "assistant_typing")}</span>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div className="px-6 pb-6">
              <div className="mx-auto w-full max-w-3xl">
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => sendSlashCommand("/help")}
                    className="rounded-full bg-[#1e1f20] px-3 py-1.5 text-[12px] text-[#c4c7c5] hover:bg-[#2d2e31] hover:text-[#e3e3e3] disabled:opacity-60"
                  >
                    {t(lang, "cmd_help")}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => sendSlashCommand("/show")}
                    className="rounded-full bg-[#1e1f20] px-3 py-1.5 text-[12px] text-[#c4c7c5] hover:bg-[#2d2e31] hover:text-[#e3e3e3] disabled:opacity-60"
                  >
                    {t(lang, "cmd_show")}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => sendSlashCommand("/spec")}
                    className="rounded-full bg-[#1e1f20] px-3 py-1.5 text-[12px] text-[#c4c7c5] hover:bg-[#2d2e31] hover:text-[#e3e3e3] disabled:opacity-60"
                  >
                    {t(lang, "cmd_spec")}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => sendSlashCommand("/done")}
                    className="rounded-full bg-[#1e1f20] px-3 py-1.5 text-[12px] text-[#c4c7c5] hover:bg-[#2d2e31] hover:text-[#e3e3e3] disabled:opacity-60"
                  >
                    {t(lang, "cmd_done")}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => sendSlashCommand("/reset")}
                    className="rounded-full bg-[#1e1f20] px-3 py-1.5 text-[12px] text-[#c4c7c5] hover:bg-[#2d2e31] hover:text-[#e3e3e3] disabled:opacity-60"
                  >
                    {t(lang, "cmd_reset")}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => sendSlashCommand("/exit")}
                    className="rounded-full bg-[#1e1f20] px-3 py-1.5 text-[12px] text-[#c4c7c5] hover:bg-[#2d2e31] hover:text-[#e3e3e3] disabled:opacity-60"
                  >
                    {t(lang, "cmd_exit")}
                  </button>
                </div>
                <div className="rounded-[28px] bg-[#1e1f20] px-4 py-3">
                  <div className="flex items-end gap-3">
                    <textarea
                      ref={inputRef}
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          send().catch(() => {});
                        }
                      }}
                      rows={1}
                      placeholder={t(lang, "input_placeholder")}
                      className="max-h-[200px] min-h-[28px] flex-1 resize-none bg-transparent text-[15px] leading-6 text-[#e3e3e3] outline-none placeholder:text-[#7b7d80]"
                    />
                    <button
                      type="button"
                      disabled={!input.trim() || busy}
                      onClick={() => send().catch(() => {})}
                      className={cn(
                        "flex h-10 w-10 items-center justify-center rounded-full transition-colors",
                        input.trim() && !busy ? "text-[#a8c7fa] hover:bg-[#2d2e31]" : "text-[#7b7d80]"
                      )}
                      aria-label={t(lang, "send")}
                    >
                      <svg height="22" viewBox="0 0 24 24" width="22">
                        <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" fill="currentColor" />
                      </svg>
                    </button>
                  </div>
                </div>
                <div className={cn("mt-2 text-center text-[12px]", statusOk ? "text-[#c4c7c5]" : "text-[#ff6b6b]")}>
                  {statusText}
                </div>
              </div>
            </div>
          </div>
        )}

        {view !== "chat" && (
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-10">
            <div className="mx-auto w-full max-w-3xl space-y-6">
              {view === "config" && (
                <>
                  <div className="flex items-center justify-between">
                    <div className="text-[18px] font-medium text-white">{t(lang, "nav_config")}</div>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => loadCfg().catch(() => {})}
                        disabled={busy}
                        className="rounded-full bg-[#2d2e31] px-4 py-2 text-[13px] text-[#e3e3e3] hover:bg-[#3a3b3e] disabled:opacity-60"
                      >
                        {t(lang, "load")}
                      </button>
                      <button
                        type="button"
                        onClick={() => saveCfg().catch(() => {})}
                        disabled={busy}
                        className="rounded-full bg-[#0b57d0] px-4 py-2 text-[13px] text-white hover:opacity-95 disabled:opacity-60"
                      >
                        {t(lang, "save")}
                      </button>
                      <button
                        type="button"
                        onClick={() => doctor().catch(() => {})}
                        disabled={busy}
                        className="rounded-full bg-[#2d2e31] px-4 py-2 text-[13px] text-[#e3e3e3] hover:bg-[#3a3b3e] disabled:opacity-60"
                      >
                        {t(lang, "doctor")}
                      </button>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <div className="text-[12px] text-[#c4c7c5]">{t(lang, "config_content")}</div>
                    <textarea
                      value={cfgContent}
                      onChange={(e) => setCfgContent(e.target.value)}
                      className="h-[320px] w-full resize-y rounded-2xl border border-[#2a2b2e] bg-[#1e1f20] p-4 font-mono text-[12px] leading-6 text-[#e3e3e3] outline-none focus:border-[#0b57d0]"
                    />
                  </div>

                  <div className="space-y-2">
                    <div className="text-[12px] text-[#c4c7c5]">{t(lang, "doctor_output")}</div>
                    <textarea
                      value={doctorOut}
                      readOnly
                      className="h-[140px] w-full resize-none rounded-2xl border border-[#2a2b2e] bg-[#1e1f20] p-4 font-mono text-[12px] leading-6 text-[#e3e3e3] outline-none"
                    />
                  </div>
                </>
              )}

              {view === "knowledge" && (
                <>
                  <div className="flex items-center justify-between">
                    <div className="text-[18px] font-medium text-white">{t(lang, "nav_knowledge")}</div>
                    <button
                      type="button"
                      onClick={() => refreshKnowledge().catch(() => {})}
                      disabled={busy}
                      className="rounded-full bg-[#2d2e31] px-4 py-2 text-[13px] text-[#e3e3e3] hover:bg-[#3a3b3e] disabled:opacity-60"
                    >
                      {t(lang, "refresh")}
                    </button>
                  </div>

                  <div className="space-y-2">
                    <div className="text-[12px] text-[#c4c7c5]">{t(lang, "knowledge_snapshot")}</div>
                    <textarea
                      value={knowledgeSnapshot}
                      readOnly
                      className="h-[480px] w-full resize-none rounded-2xl border border-[#2a2b2e] bg-[#1e1f20] p-4 font-mono text-[12px] leading-6 text-[#e3e3e3] outline-none"
                    />
                  </div>
                </>
              )}

              {view === "prompt" && (
                <>
                  <div className="flex items-center justify-between">
                    <div className="text-[18px] font-medium text-white">{t(lang, "nav_prompt")}</div>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => loadPrompt().catch(() => {})}
                        disabled={busy}
                        className="rounded-full bg-[#2d2e31] px-4 py-2 text-[13px] text-[#e3e3e3] hover:bg-[#3a3b3e] disabled:opacity-60"
                      >
                        {t(lang, "load")}
                      </button>
                      <button
                        type="button"
                        onClick={() => savePrompt().catch(() => {})}
                        disabled={busy}
                        className="rounded-full bg-[#0b57d0] px-4 py-2 text-[13px] text-white hover:opacity-95 disabled:opacity-60"
                      >
                        {t(lang, "save")}
                      </button>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <div className="text-[12px] text-[#c4c7c5]">{t(lang, "prompt_content")}</div>
                    <textarea
                      value={promptContent}
                      onChange={(e) => setPromptContent(e.target.value)}
                      className="h-[520px] w-full resize-y rounded-2xl border border-[#2a2b2e] bg-[#1e1f20] p-4 font-mono text-[12px] leading-6 text-[#e3e3e3] outline-none focus:border-[#0b57d0]"
                    />
                  </div>
                </>
              )}

              <div className={cn("text-center text-[12px]", statusOk ? "text-[#c4c7c5]" : "text-[#ff6b6b]")}>
                {statusText}
              </div>
            </div>
          </div>
        )}
      </main>

      {showJump && (
        <button
          type="button"
          onClick={() => {
            const el = listRef.current;
            if (!el) return;
            el.scrollTo({ top: el.scrollHeight, behavior: prefersReducedMotion() ? "auto" : "smooth" });
          }}
          className="reqx-animate-fade-in fixed bottom-24 right-8 rounded-full bg-[#2d2e31] px-4 py-2 text-[12px] text-[#e3e3e3] hover:bg-[#3a3b3e]"
        >
          {t(lang, "jump_to_bottom")}
        </button>
      )}

      <div className="pointer-events-none fixed bottom-6 left-1/2 z-50 w-[min(520px,calc(100vw-32px))] -translate-x-1/2 space-y-2">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={cn(
              "reqx-animate-fade-in pointer-events-auto rounded-2xl border px-4 py-3 text-[13px] shadow-lg backdrop-blur",
              toast.kind === "error"
                ? "border-[#3a2323] bg-[#251515]/80 text-[#ffb3b3]"
                : "border-[#2a2b2e] bg-[#1e1f20]/80 text-[#e3e3e3]"
            )}
          >
            {toast.text}
          </div>
        ))}
      </div>
    </div>
  );
}
