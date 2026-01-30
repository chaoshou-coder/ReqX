from __future__ import annotations

from dataclasses import asdict
import hmac
import json
import os
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..cli.common import (
    PROMPT_VERSION,
    load_global_prompt,
    parse_knowledge_update,
    truncate_text,
)
from ..service.knowledge_service import KnowledgeService
from ..storage.knowledge_store import open_knowledge_store
from ..core.llm_factory import get_llm, load_llm_config, redact_secrets


_DEFAULT_BIND = "127.0.0.1"
_DEFAULT_PORT = 8788
_MAX_BODY_BYTES_DEFAULT = 2 * 1024 * 1024


_INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>ReqX WebUI</title>
    <style>
      :root { color-scheme: light dark; }
      body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, "Helvetica Neue", Arial; margin: 0; }
      .wrap { display: grid; grid-template-columns: 360px 1fr; height: 100vh; }
      .side { border-right: 1px solid rgba(127,127,127,.25); padding: 12px; overflow: auto; }
      .main { padding: 12px; overflow: auto; }
      .row { display: grid; gap: 8px; margin-bottom: 10px; }
      label { font-size: 12px; opacity: 0.85; }
      input, textarea, button { width: 100%; box-sizing: border-box; padding: 8px; border-radius: 6px; border: 1px solid rgba(127,127,127,.35); background: transparent; color: inherit; }
      textarea { min-height: 120px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
      .btnrow { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
      .tabs { display: flex; gap: 8px; margin-bottom: 10px; }
      .tab { padding: 8px 10px; border-radius: 999px; border: 1px solid rgba(127,127,127,.35); background: transparent; cursor: pointer; }
      .tab[aria-selected="true"] { background: rgba(127,127,127,.15); }
      .card { border: 1px solid rgba(127,127,127,.25); border-radius: 10px; padding: 10px; margin-bottom: 12px; }
      .chat { display: grid; gap: 10px; }
      .msg { border: 1px solid rgba(127,127,127,.25); border-radius: 10px; padding: 10px; }
      .meta { font-size: 12px; opacity: .7; margin-bottom: 6px; }
      pre { overflow: auto; padding: 10px; border-radius: 8px; border: 1px solid rgba(127,127,127,.25); }
      code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
      .danger { color: #b00020; }
      .ok { color: #0a7a0a; }
    </style>
  </head>
  <body>
    <div class="wrap">
      <aside class="side">
        <div class="tabs">
          <button class="tab" id="tab-chat" aria-selected="true">对话</button>
          <button class="tab" id="tab-knowledge" aria-selected="false">知识库</button>
          <button class="tab" id="tab-config" aria-selected="false">配置</button>
          <button class="tab" id="tab-prompt" aria-selected="false">提示词</button>
        </div>

        <section id="panel-chat">
          <div class="row">
            <label>鉴权 Token（可选；与环境变量 REQX_WEB_TOKEN 对应）</label>
            <input id="authToken" placeholder="留空表示不发送 Authorization" />
          </div>
          <div class="row">
            <label>LLM 配置文件路径</label>
            <input id="cfgPath" placeholder="例如：llm.yaml" />
          </div>
          <div class="row">
            <label>项目知识文件路径</label>
            <input id="knowledgePath" placeholder="例如：project_knowledge.db" />
          </div>
          <div class="row">
            <label><input type="checkbox" id="dryRun" /> dry-run（只演练不落盘）</label>
          </div>
          <div class="row">
            <label>导入上下文（可选）</label>
            <textarea id="importedContext" placeholder="粘贴历史对话/背景材料（可选）"></textarea>
          </div>
          <div class="btnrow">
            <button id="btnReset">清空本页对话</button>
            <button id="btnRefreshKnowledge">刷新知识库快照</button>
          </div>
        </section>

        <section id="panel-knowledge" style="display:none">
          <div class="row">
            <label>知识库快照（只读）</label>
            <textarea id="knowledgeSnapshot" readonly></textarea>
          </div>
          <button id="btnLoadKnowledge">读取</button>
        </section>

        <section id="panel-config" style="display:none">
          <div class="row">
            <label>配置文件内容（YAML）</label>
            <textarea id="cfgContent"></textarea>
          </div>
          <div class="btnrow">
            <button id="btnLoadCfg">读取</button>
            <button id="btnSaveCfg">保存</button>
          </div>
          <button id="btnDoctor">Doctor（解析并输出摘要）</button>
          <div class="row">
            <label>Doctor 输出</label>
            <textarea id="doctorOut" readonly></textarea>
          </div>
        </section>

        <section id="panel-prompt" style="display:none">
          <div class="row">
            <label>全局提示词（global_prompt.txt）</label>
            <textarea id="promptContent"></textarea>
          </div>
          <div class="btnrow">
            <button id="btnLoadPrompt">读取</button>
            <button id="btnSavePrompt">保存</button>
          </div>
        </section>
      </aside>

      <main class="main">
        <div class="card">
          <div class="row">
            <label>你要对 agent 说什么？</label>
            <textarea id="userInput" placeholder="输入后按 Ctrl+Enter 发送"></textarea>
          </div>
          <button id="btnSend">发送</button>
          <div id="status" style="margin-top:8px; font-size:12px; opacity:.85"></div>
        </div>
        <div class="chat" id="chat"></div>
      </main>
    </div>
    <script src="/app.js"></script>
  </body>
</html>
"""


_APP_JS = r"""(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    messages: [],
    knowledgeSnapshot: null,
  };

  function renderMarkdown(md) {
    const src = (md ?? "").replace(/\r\n/g, "\n");
    const blocks = [];
    const placeholder = (i) => `@@CODE_BLOCK_${i}@@`;
    let text = src.replace(/```([\s\S]*?)```/g, (_m, code) => {
      const i = blocks.length;
      blocks.push(code);
      return placeholder(i);
    });
    const esc = (s) => (s ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
    text = esc(text);
    text = text.replace(/@@CODE_BLOCK_(\d+)@@/g, (_m, idx) => {
      const i = Number(idx);
      const code = blocks[i] ?? "";
      return `<pre><code>${esc(code.trim())}</code></pre>`;
    });
    text = text.replace(/`([^`]+)`/g, (_m, code) => `<code>${esc(code)}</code>`);
    text = text.replace(/^### (.*)$/gm, "<h3>$1</h3>");
    text = text.replace(/^## (.*)$/gm, "<h2>$1</h2>");
    text = text.replace(/^# (.*)$/gm, "<h1>$1</h1>");
    text = text.replace(/^\s*[-*] (.*)$/gm, "<li>$1</li>");
    text = text.replace(/(<li>[\s\S]*?<\/li>)/g, "<ul>$1</ul>");
    text = text.replace(/\n{2,}/g, "</p><p>");
    text = `<p>${text}</p>`;
    return text;
  }

  function setStatus(text, ok = true) {
    const el = $("status");
    el.textContent = text;
    el.className = ok ? "ok" : "danger";
  }

  function saveLocal() {
    localStorage.setItem("reqx_authToken", $("authToken").value);
    localStorage.setItem("reqx_cfgPath", $("cfgPath").value);
    localStorage.setItem("reqx_knowledgePath", $("knowledgePath").value);
    localStorage.setItem("reqx_dryRun", $("dryRun").checked ? "1" : "");
  }

  function loadLocal() {
    $("authToken").value = localStorage.getItem("reqx_authToken") || "";
    $("cfgPath").value = localStorage.getItem("reqx_cfgPath") || "llm.yaml";
    $("knowledgePath").value = localStorage.getItem("reqx_knowledgePath") || "project_knowledge.db";
    $("dryRun").checked = (localStorage.getItem("reqx_dryRun") || "") === "1";
  }

  function renderChat() {
    const root = $("chat");
    root.innerHTML = "";
    for (const msg of state.messages) {
      const div = document.createElement("div");
      div.className = "msg";
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = msg.role === "user" ? "用户" : "助手";
      div.appendChild(meta);
      const body = document.createElement("div");
      const esc = (s) => (s ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
      body.innerHTML = msg.role === "assistant" ? renderMarkdown(msg.content) : `<pre>${esc(msg.content)}</pre>`;
      div.appendChild(body);
      root.appendChild(div);
    }
  }

  async function apiJson(method, path, body) {
    const token = $("authToken").value.trim();
    const headers = {"Content-Type": "application/json; charset=utf-8"};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    return await res.json();
  }

  async function refreshKnowledge() {
    saveLocal();
    const kp = $("knowledgePath").value.trim();
    const r = await apiJson("POST", "/v1/knowledge/read", {knowledge_path: kp || null});
    if (!r.ok) {
      setStatus(`读取知识库失败：${r.error?.code || "unknown"}`, false);
      return;
    }
    state.knowledgeSnapshot = r.result;
    $("knowledgeSnapshot").value = JSON.stringify(r.result, null, 2);
    setStatus("知识库已刷新");
  }

  async function send() {
    saveLocal();
    const text = $("userInput").value;
    if (!text.trim()) return;
    $("userInput").value = "";
    state.messages.push({role: "user", content: text});
    renderChat();
    setStatus("调用模型中…");

    const payload = {
      config_path: $("cfgPath").value.trim() || null,
      knowledge_path: $("knowledgePath").value.trim() || null,
      dry_run: $("dryRun").checked,
      imported_context: $("importedContext").value || "",
      messages: state.messages,
    };
    const r = await apiJson("POST", "/v1/chat/send", payload);
    if (!r.ok) {
      setStatus(`失败：${r.error?.code || "unknown"} ${r.error?.message || ""}`.trim(), false);
      return;
    }
    state.messages.push({role: "assistant", content: r.result.reply});
    renderChat();
    if (r.result.knowledge_appended > 0) {
      await refreshKnowledge();
      setStatus(`完成（已追加知识 ${r.result.knowledge_appended} 条）`);
      return;
    }
    setStatus("完成");
  }

  async function loadCfg() {
    saveLocal();
    const p = $("cfgPath").value.trim();
    const r = await apiJson("POST", "/v1/config/read", {path: p || null});
    if (!r.ok) {
      setStatus(`读取配置失败：${r.error?.code || "unknown"}`, false);
      return;
    }
    $("cfgContent").value = r.result.content || "";
    setStatus("配置已读取");
  }

  async function saveCfg() {
    saveLocal();
    const p = $("cfgPath").value.trim();
    const r = await apiJson("POST", "/v1/config/write", {path: p || null, content: $("cfgContent").value || "", dry_run: $("dryRun").checked});
    if (!r.ok) {
      setStatus(`保存配置失败：${r.error?.code || "unknown"}`, false);
      return;
    }
    setStatus(r.result?.dry_run ? "dry-run：未落盘" : "配置已保存");
  }

  async function doctor() {
    saveLocal();
    const p = $("cfgPath").value.trim();
    const r = await apiJson("POST", "/v1/config/doctor", {path: p || null});
    if (!r.ok) {
      setStatus(`Doctor失败：${r.error?.code || "unknown"}`, false);
      $("doctorOut").value = "";
      return;
    }
    $("doctorOut").value = JSON.stringify(r.result, null, 2);
    setStatus("Doctor完成");
  }

  async function loadPrompt() {
    const r = await apiJson("POST", "/v1/prompt/read", {});
    if (!r.ok) {
      setStatus(`读取提示词失败：${r.error?.code || "unknown"}`, false);
      return;
    }
    $("promptContent").value = r.result.content || "";
    setStatus("提示词已读取");
  }

  async function savePrompt() {
    const r = await apiJson("POST", "/v1/prompt/write", {content: $("promptContent").value || "", dry_run: $("dryRun").checked});
    if (!r.ok) {
      setStatus(`保存提示词失败：${r.error?.code || "unknown"}`, false);
      return;
    }
    setStatus(r.result?.dry_run ? "dry-run：未落盘" : "提示词已保存");
  }

  function tab(name) {
    const tabs = ["chat","knowledge","config","prompt"];
    for (const t of tabs) {
      $(`tab-${t}`).setAttribute("aria-selected", t === name ? "true" : "false");
      $(`panel-${t}`).style.display = t === name ? "" : "none";
    }
  }

  $("btnSend").addEventListener("click", send);
  $("btnReset").addEventListener("click", () => { state.messages = []; renderChat(); setStatus("已清空"); });
  $("btnRefreshKnowledge").addEventListener("click", refreshKnowledge);
  $("btnLoadKnowledge").addEventListener("click", refreshKnowledge);
  $("btnLoadCfg").addEventListener("click", loadCfg);
  $("btnSaveCfg").addEventListener("click", saveCfg);
  $("btnDoctor").addEventListener("click", doctor);
  $("btnLoadPrompt").addEventListener("click", loadPrompt);
  $("btnSavePrompt").addEventListener("click", savePrompt);

  $("tab-chat").addEventListener("click", () => tab("chat"));
  $("tab-knowledge").addEventListener("click", () => tab("knowledge"));
  $("tab-config").addEventListener("click", () => tab("config"));
  $("tab-prompt").addEventListener("click", () => tab("prompt"));

  $("userInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      send();
    }
  });

  loadLocal();
  refreshKnowledge().catch(() => {});
  loadCfg().catch(() => {});
  loadPrompt().catch(() => {});
})();"""


class _JsonError(Exception):
    def __init__(self, code: str, *, status: int = 400, message: str | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.message = message


def _read_body(handler: BaseHTTPRequestHandler, *, limit: int) -> bytes:
    raw_len = handler.headers.get("Content-Length", "")
    try:
        n = int(raw_len)
    except Exception:
        n = 0
    if n <= 0:
        return b""
    if n > limit:
        raise _JsonError("body_too_large", status=413)
    return handler.rfile.read(n)


def _read_json(handler: BaseHTTPRequestHandler, *, limit: int) -> dict[str, Any]:
    raw = _read_body(handler, limit=limit)
    if not raw:
        return {}
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise _JsonError("invalid_json") from e
    if not isinstance(obj, dict):
        raise _JsonError("json_must_be_object")
    return obj


def _apply_security_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header(
        "Content-Security-Policy",
        "default-src 'none'; "
        "connect-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "base-uri 'none'; "
        "frame-ancestors 'none'",
    )


def _write_json(
    handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any], *, headers: dict[str, str] | None = None
) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    _apply_security_headers(handler)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    for k, v in (headers or {}).items():
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(raw)


def _write_text(
    handler: BaseHTTPRequestHandler, status: int, body: str, *, content_type: str, headers: dict[str, str] | None = None
) -> None:
    raw = body.encode("utf-8")
    handler.send_response(status)
    _apply_security_headers(handler)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(raw)))
    for k, v in (headers or {}).items():
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(raw)


def _resolve_under(base_dir: Path, path: str | None, *, default_name: str) -> Path:
    raw = (path or "").strip() or default_name
    p = Path(raw)
    if not p.is_absolute():
        p = base_dir / p
    resolved = p.expanduser().resolve()
    try:
        resolved.relative_to(base_dir)
    except Exception as e:
        raise _JsonError("path_outside_repo", status=400) from e
    return resolved


def _require_yaml_path(path: Path) -> None:
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise _JsonError("path_must_be_yaml", status=400)
    if path.exists() and path.is_dir():
        raise _JsonError("path_is_directory", status=400)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = text.rstrip() + "\n"
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            delete=False,
            dir=str(path.parent),
            prefix=path.name + ".",
            suffix=".tmp",
        ) as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
            tmp_path = Path(f.name)
        os.replace(tmp_path, path)
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


def _build_web_chat_prompt(
    *,
    messages: list[tuple[str, str]],
    global_prompt: str,
    project_knowledge: str,
    imported_context: str,
) -> str:
    history: list[str] = []
    for role, content in messages:
        c = (content or "").strip()
        if not c:
            continue
        r = "用户" if role == "user" else "助手"
        history.append(f"{r}: {c}")
    history_text = "\n".join(history)
    knowledge = truncate_text(project_knowledge, 4000, keep="tail")
    context = truncate_text(imported_context, 4000, keep="tail")
    return (
        f"{global_prompt}\n"
        "你现在处于 WebUI chat 模式：你的目标是通过多轮问答澄清需求。\n"
        "规则：\n"
        "- 允许输出 Markdown；若全局提示词与此冲突，以此处为准。\n"
        "- 不要输出 JSON/YAML 规格文档。\n"
        "- “项目知识”用于后续生成：是否写入、写入什么由你决定。\n"
        "- 当你认为某条信息已经稳定、对后续生成很关键时，在回复末尾额外输出一行：\n"
        '  <KNOWLEDGE>{"append":["...","..."]}</KNOWLEDGE>\n'
        "  该行仅供程序解析并写入项目知识文件，不会展示给用户；不要写入任何密钥或敏感信息。\n"
        "历史上下文（可选，来自本地导入的内容，供你参考但不要复述全文）：\n"
        f"{context}\n"
        "已有项目知识（可能来自历史会话，供你引用但不要复述全文）：\n"
        f"{knowledge}\n"
        "本轮对话记录：\n"
        f"{history_text}\n"
        "请输出你的下一句话（只输出对用户可见内容）："
    )


class WebUIServer:
    def __init__(
        self,
        *,
        repo_root: str | Path,
        bind: str = _DEFAULT_BIND,
        port: int = _DEFAULT_PORT,
        dry_run: bool = False,
        token_env: str | None = "REQX_WEB_TOKEN",
        token_value: str | None = None,
        max_body_bytes: int = _MAX_BODY_BYTES_DEFAULT,
    ):
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.bind = bind
        self.port = port
        self.dry_run = dry_run
        self.max_body_bytes = max_body_bytes
        self._token: str | None = token_value
        if self._token is None and token_env:
            self._token = os.getenv(token_env) or None
        self._knowledge_service = KnowledgeService(base_dir=self.repo_root, default_path=self.repo_root / "project_knowledge.db")
        self._prompt_path = self.repo_root / "agents" / "global_prompt.txt"

    def read_global_prompt(self) -> str:
        if self._prompt_path.exists():
            return self._prompt_path.read_text(encoding="utf-8").strip()
        return load_global_prompt()

    def create_server(self) -> ThreadingHTTPServer:
        handler = self._make_handler()
        return ThreadingHTTPServer((self.bind, self.port), handler)

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ReqXWebUI/1"

            def log_message(self, format: str, *args: Any) -> None:
                super().log_message(format, *args)

            def _bearer_token(self) -> str | None:
                auth = (self.headers.get("Authorization") or "").strip()
                if not auth:
                    return None
                parts = auth.split()
                if len(parts) != 2:
                    return None
                if parts[0].lower() != "bearer":
                    return None
                return parts[1].strip() or None

            def _require_auth(self, *, allow_if_no_token: bool) -> None:
                expected = server._token
                if not expected:
                    if allow_if_no_token:
                        return
                    raise _JsonError("token_required", status=401)
                got = self._bearer_token()
                if not got or not hmac.compare_digest(got, expected):
                    raise _JsonError("unauthorized", status=401)

            def do_GET(self) -> None:
                try:
                    parsed = urlparse(self.path)
                    if parsed.path == "/" or parsed.path == "/index.html":
                        _write_text(self, 200, _INDEX_HTML, content_type="text/html; charset=utf-8")
                        return
                    if parsed.path == "/app.js":
                        _write_text(self, 200, _APP_JS, content_type="application/javascript; charset=utf-8")
                        return
                    if parsed.path == "/health":
                        _write_json(self, 200, {"ok": True})
                        return
                    raise _JsonError("not_found", status=404)
                except _JsonError as e:
                    headers: dict[str, str] = {}
                    if e.status == 401:
                        headers["WWW-Authenticate"] = "Bearer"
                    _write_json(self, e.status, {"ok": False, "error": {"code": e.code}}, headers=headers or None)
                except Exception:
                    _write_json(self, 500, {"ok": False, "error": {"code": "internal_error"}})

            def do_POST(self) -> None:
                try:
                    parsed = urlparse(self.path)
                    write_paths = {"/v1/prompt/write", "/v1/config/write", "/v1/chat/send"}
                    self._require_auth(allow_if_no_token=parsed.path not in write_paths)
                    body = _read_json(self, limit=server.max_body_bytes)

                    if parsed.path == "/v1/knowledge/read":
                        kp = body.get("knowledge_path")
                        snap = server._knowledge_service.read(kp if isinstance(kp, str) and kp.strip() else None)
                        _write_json(self, 200, {"ok": True, "result": asdict(snap)})
                        return

                    if parsed.path == "/v1/prompt/read":
                        content = server.read_global_prompt()
                        content_redacted = redact_secrets(content)
                        _write_json(
                            self,
                            200,
                            {
                                "ok": True,
                                "result": {
                                    "content": content_redacted,
                                    "prompt_version": PROMPT_VERSION,
                                    "warning": "content_redacted" if content_redacted != content else None,
                                },
                            },
                        )
                        return

                    if parsed.path == "/v1/prompt/write":
                        text = body.get("content")
                        if not isinstance(text, str):
                            raise _JsonError("missing_or_invalid_content")
                        dry_run = bool(body.get("dry_run")) or server.dry_run
                        if not dry_run:
                            _atomic_write_text(server._prompt_path, text)
                        _write_json(self, 200, {"ok": True, "result": {"dry_run": bool(dry_run)}})
                        return

                    if parsed.path == "/v1/config/read":
                        p = body.get("path")
                        path = _resolve_under(server.repo_root, p if isinstance(p, str) else None, default_name="llm.yaml")
                        _require_yaml_path(path)
                        content = path.read_text(encoding="utf-8") if path.exists() else ""
                        content_redacted = redact_secrets(content)
                        _write_json(
                            self,
                            200,
                            {
                                "ok": True,
                                "result": {
                                    "path": str(path),
                                    "content": content_redacted,
                                    "warning": "content_redacted" if content_redacted != content else None,
                                },
                            },
                        )
                        return

                    if parsed.path == "/v1/config/write":
                        p = body.get("path")
                        content = body.get("content")
                        if not isinstance(content, str):
                            raise _JsonError("missing_or_invalid_content")
                        path = _resolve_under(server.repo_root, p if isinstance(p, str) else None, default_name="llm.yaml")
                        _require_yaml_path(path)
                        dry_run = bool(body.get("dry_run")) or server.dry_run
                        if not dry_run:
                            _atomic_write_text(path, content)
                        _write_json(self, 200, {"ok": True, "result": {"path": str(path), "dry_run": bool(dry_run)}})
                        return

                    if parsed.path == "/v1/config/doctor":
                        p = body.get("path")
                        path = _resolve_under(server.repo_root, p if isinstance(p, str) else None, default_name="llm.yaml")
                        _require_yaml_path(path)
                        if not path.exists():
                            raise _JsonError("config_not_found", status=404)
                        cfg = load_llm_config(str(path), strict=True)
                        payload: dict[str, Any] = {
                            "provider": cfg.provider,
                            "model": cfg.model,
                            "temperature": cfg.temperature,
                            "max_tokens": cfg.max_tokens,
                            "input_char_limit": cfg.input_char_limit,
                            "output_char_limit": cfg.output_char_limit,
                            "base_url": cfg.base_url,
                            "env_file": cfg.env_file,
                            "prompt_version": PROMPT_VERSION,
                        }
                        for k, v in list(payload.items()):
                            if isinstance(v, str):
                                payload[k] = redact_secrets(v)
                        _write_json(self, 200, {"ok": True, "result": payload})
                        return

                    if parsed.path == "/v1/chat/send":
                        cfg_path_raw = body.get("config_path")
                        kp_raw = body.get("knowledge_path")
                        imported = body.get("imported_context") or ""
                        dry_run = bool(body.get("dry_run"))
                        msgs = body.get("messages") or []
                        if not isinstance(imported, str):
                            imported = ""
                        if not isinstance(msgs, list):
                            raise _JsonError("missing_or_invalid_messages")
                        messages: list[tuple[str, str]] = []
                        for m in msgs:
                            if not isinstance(m, dict):
                                continue
                            role = m.get("role")
                            content = m.get("content")
                            if role not in {"user", "assistant"}:
                                continue
                            if not isinstance(content, str) or not content.strip():
                                continue
                            messages.append((role, content))
                        if not messages or messages[-1][0] != "user":
                            raise _JsonError("missing_or_invalid_messages")

                        cfg_path = _resolve_under(
                            server.repo_root, cfg_path_raw if isinstance(cfg_path_raw, str) else None, default_name="llm.yaml"
                        )
                        _require_yaml_path(cfg_path)
                        if not cfg_path.exists():
                            raise _JsonError("config_not_found", status=404)
                        knowledge_path = (
                            server._knowledge_service.resolve_path(kp_raw if isinstance(kp_raw, str) and kp_raw.strip() else None)
                        )

                        ks = open_knowledge_store(knowledge_path)
                        ks.load()
                        project_knowledge = ks.transcript()
                        global_prompt = server.read_global_prompt()
                        prompt = _build_web_chat_prompt(
                            messages=messages,
                            global_prompt=global_prompt,
                            project_knowledge=project_knowledge,
                            imported_context=imported,
                        )

                        llm = get_llm(config_path=str(cfg_path), strict=True)
                        raw_reply = getattr(llm.invoke(prompt), "content", "") or ""
                        visible, items = parse_knowledge_update(str(raw_reply))
                        appended = 0
                        for item in items:
                            ks.append("system", item, autosave=False)
                            appended += 1
                        if appended and (not dry_run) and (not server.dry_run):
                            ks.save()
                        _write_json(
                            self,
                            200,
                            {"ok": True, "result": {"reply": visible, "knowledge_appended": appended, "dry_run": bool(dry_run or server.dry_run)}},
                        )
                        return

                    raise _JsonError("not_found", status=404)
                except _JsonError as e:
                    payload: dict[str, Any] = {"ok": False, "error": {"code": e.code}}
                    if e.message:
                        payload["error"]["message"] = e.message
                    headers = {"WWW-Authenticate": "Bearer"} if e.status == 401 else None
                    _write_json(self, e.status, payload, headers=headers)
                except Exception as e:
                    _write_json(
                        self,
                        500,
                        {"ok": False, "error": {"code": "internal_error", "message": redact_secrets(str(e))}},
                    )

        return Handler

    def serve_forever(self) -> None:
        with self.create_server() as httpd:
            httpd.serve_forever()


def serve_webui(*, repo_root: str | Path, bind: str = _DEFAULT_BIND, port: int = _DEFAULT_PORT, dry_run: bool = False) -> None:
    WebUIServer(repo_root=repo_root, bind=bind, port=port, dry_run=dry_run).serve_forever()
