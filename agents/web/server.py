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
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
    <title>ReqX Studio</title>
    <link href="https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;700&family=Roboto:wght@400;500&display=swap" rel="stylesheet">
    <style>
      :root {
        /* Gemini Light Theme */
        --bg-body: #ffffff;
        --bg-sidebar: #f0f4f9;
        --bg-input: #f0f4f9;
        --text-primary: #1f1f1f;
        --text-secondary: #444746;
        --accent-color: #0b57d0; /* Google Blue */
        --accent-hover: #0842a0;
        --surface-hover: #e3e3e3;
        --border-color: #e0e3e7;
        --user-msg-bg: #f0f4f9;
        --user-msg-text: #1f1f1f;
        --bot-msg-bg: transparent;
        --code-bg: #f0f4f9;
        --shadow: 0 1px 3px rgba(0,0,0,0.12), 0 1px 2px rgba(0,0,0,0.24);
        --font-sans: 'Google Sans', 'Roboto', -apple-system, sans-serif;
        --font-mono: 'Roboto Mono', monospace;
        --radius-pill: 999px;
        --radius-card: 16px;
      }
      @media (prefers-color-scheme: dark) {
        :root {
          /* Gemini Dark Theme */
          --bg-body: #131314;
          --bg-sidebar: #1e1f20;
          --bg-input: #1e1f20;
          --text-primary: #e3e3e3;
          --text-secondary: #c4c7c5;
          --accent-color: #a8c7fa; /* Light Blue */
          --accent-hover: #d3e3fd;
          --surface-hover: #2d2e31;
          --border-color: #444746;
          --user-msg-bg: #2d2e31; /* Dark Grey Pill */
          --user-msg-text: #e3e3e3;
          --bot-msg-bg: transparent;
          --code-bg: #1e1f20;
          --shadow: 0 4px 8px rgba(0,0,0,0.3);
        }
      }

      body {
        font-family: var(--font-sans);
        margin: 0;
        background: var(--bg-body);
        color: var(--text-primary);
        line-height: 1.6;
        height: 100vh;
        overflow: hidden;
        display: flex;
      }

      /* Sidebar */
      .sidebar {
        width: 280px;
        background: var(--bg-sidebar);
        display: flex;
        flex-direction: column;
        padding: 20px 16px;
        gap: 8px;
        transition: transform 0.3s ease;
        z-index: 100;
      }
      .sidebar-header {
        padding: 0 12px 16px;
        font-size: 22px;
        font-weight: 500;
        color: var(--text-primary);
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .nav-item {
        padding: 12px 16px;
        border-radius: var(--radius-pill);
        cursor: pointer;
        color: var(--text-primary);
        font-weight: 500;
        font-size: 14px;
        display: flex;
        align-items: center;
        gap: 12px;
        transition: background 0.2s;
        border: none;
        background: transparent;
        text-align: left;
      }
      .nav-item:hover { background: var(--surface-hover); }
      .nav-item.active { background: #004a77; color: #d3e3fd; }
      @media (prefers-color-scheme: light) {
        .nav-item.active { background: #d3e3fd; color: #041e49; }
      }

      /* Main Area */
      .main {
        flex: 1;
        display: flex;
        flex-direction: column;
        position: relative;
        background: var(--bg-body);
        border-top-left-radius: 20px; /* Gemini curve */
        border-bottom-left-radius: 20px;
        margin-left: 0;
        overflow: hidden;
      }
      /* In mobile/responsive, sidebar might behave differently, but keep simple for now */

      /* Config/Panels */
      .panel {
        flex: 1;
        overflow-y: auto;
        padding: 40px;
        display: none;
        max-width: 800px;
        margin: 0 auto;
        width: 100%;
        box-sizing: border-box;
      }
      .panel.active { display: flex; flex-direction: column; gap: 24px; animation: fadeIn 0.3s; }

      /* Form Elements */
      label {
        font-size: 12px;
        font-weight: 500;
        color: var(--text-secondary);
        margin-bottom: 8px;
        display: block;
      }
      input, textarea, select {
        width: 100%;
        box-sizing: border-box;
        padding: 14px 16px;
        border-radius: 8px;
        border: 1px solid var(--border-color);
        background: transparent;
        color: var(--text-primary);
        font-family: inherit;
        font-size: 14px;
        outline: none;
      }
      input:focus, textarea:focus {
        border-color: var(--accent-color);
        box-shadow: 0 0 0 1px var(--accent-color);
      }
      button.btn {
        padding: 10px 24px;
        border-radius: var(--radius-pill);
        border: none;
        background: transparent;
        color: var(--accent-color);
        font-weight: 500;
        cursor: pointer;
        transition: background 0.2s;
      }
      button.btn:hover { background: rgba(11, 87, 208, 0.1); }
      button.btn-primary {
        background: var(--accent-color);
        color: var(--bg-body);
      }
      button.btn-primary:hover {
        opacity: 0.9;
        background: var(--accent-color); /* Override transparent hover */
        box-shadow: 0 1px 3px rgba(0,0,0,0.3);
      }
      
      /* Chat Area */
      .chat-view {
        flex: 1;
        display: flex;
        flex-direction: column;
        height: 100%;
      }
      .chat-history {
        flex: 1;
        overflow-y: auto;
        padding: 40px 20px;
        display: flex;
        flex-direction: column;
        gap: 32px;
        scroll-behavior: smooth;
      }
      .chat-content-width {
        width: 100%;
        max-width: 800px;
        margin: 0 auto;
      }
      
      .msg {
        display: flex;
        gap: 16px;
        line-height: 1.6;
        opacity: 0;
        animation: slideUp 0.3s forwards;
      }
      @keyframes slideUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }

      .msg-avatar {
        width: 32px;
        height: 32px;
        border-radius: 50%;
        background: var(--accent-color);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 14px;
        color: var(--bg-body);
        flex-shrink: 0;
      }
      .msg[data-role="user"] .msg-avatar { background: var(--text-secondary); }
      
      .msg-content {
        flex: 1;
        min-width: 0;
        font-size: 16px;
      }
      /* User Message Style */
      .msg[data-role="user"] {
        flex-direction: row-reverse;
      }
      .msg[data-role="user"] .msg-content {
        background: var(--user-msg-bg);
        color: var(--user-msg-text);
        padding: 12px 20px;
        border-radius: 20px;
        border-bottom-right-radius: 4px;
        max-width: 80%;
      }
      /* Bot Message Style */
      .msg[data-role="assistant"] .msg-content {
        background: transparent;
        padding: 0; /* No bubble for bot */
        color: var(--text-primary);
      }

      /* Markdown overrides */
      .msg-content h1, .msg-content h2, .msg-content h3 { font-weight: 500; margin-top: 24px; margin-bottom: 8px; }
      .msg-content p { margin-bottom: 12px; }
      .msg-content pre {
        background: var(--code-bg);
        padding: 16px;
        border-radius: 12px;
        overflow-x: auto;
        border: 1px solid var(--border-color);
      }
      .msg-content code { font-family: var(--font-mono); font-size: 0.9em; }

      /* Input Area */
      .input-area {
        padding: 20px;
        background: var(--bg-body);
      }
      .input-box {
        max-width: 800px;
        margin: 0 auto;
        background: var(--bg-input);
        border-radius: 28px; /* High radius for pill shape */
        padding: 8px 16px;
        display: flex;
        align-items: flex-end;
        gap: 12px;
        transition: background 0.2s;
      }
      .input-box:focus-within {
        background: var(--bg-input); /* Keep same, maybe darker shadow? */
      }
      .input-box textarea {
        background: transparent;
        border: none;
        padding: 12px 0;
        max-height: 200px;
        resize: none;
        font-size: 16px;
        line-height: 1.5;
        box-shadow: none !important; /* Remove focus ring */
      }
      .send-btn {
        width: 40px;
        height: 40px;
        border-radius: 50%;
        border: none;
        background: transparent;
        color: var(--text-secondary);
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 4px;
        transition: all 0.2s;
      }
      .send-btn:hover { background: var(--surface-hover); color: var(--text-primary); }
      .send-btn.active { color: var(--accent-color); }
      
      /* Status */
      .status-bar {
        font-size: 12px;
        color: var(--text-secondary);
        text-align: center;
        padding-top: 8px;
        min-height: 20px;
      }

      /* Scrollbar */
      ::-webkit-scrollbar { width: 8px; height: 8px; }
      ::-webkit-scrollbar-track { background: transparent; }
      ::-webkit-scrollbar-thumb { background: var(--border-color); border-radius: 4px; }
      ::-webkit-scrollbar-thumb:hover { background: var(--text-secondary); }
    </style>
  </head>
  <body>
    <aside class="sidebar">
      <div class="sidebar-header">
        <span style="font-size:24px;">✨</span> ReqX
      </div>
      <button class="nav-item active" onclick="app.tab('chat')">
        <span>💬</span> Chat
      </button>
      <button class="nav-item" onclick="app.tab('knowledge')">
        <span>📚</span> Knowledge
      </button>
      <button class="nav-item" onclick="app.tab('config')">
        <span>⚙️</span> Config
      </button>
      <button class="nav-item" onclick="app.tab('prompt')">
        <span>📝</span> Prompt
      </button>
    </aside>

    <main class="main">
      <!-- Chat View -->
      <div id="view-chat" class="chat-view">
        <div class="chat-history" id="chat-container">
          <!-- Welcome Message -->
          <div class="chat-content-width">
            <div class="msg" data-role="assistant" style="opacity:1; animation:none;">
              <div class="msg-avatar">✨</div>
              <div class="msg-content">
                <h2 style="margin-top:0;">你好, 我是 ReqX</h2>
                <p>我可以帮助你分析需求、生成代码或管理项目配置。</p>
              </div>
            </div>
          </div>
        </div>
        
        <div class="input-area">
          <div class="input-box">
             <textarea id="userInput" placeholder="输入指令或问题..." rows="1"></textarea>
             <button id="btnSend" class="send-btn">
               <svg height="24" viewBox="0 0 24 24" width="24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" fill="currentColor"/></svg>
             </button>
          </div>
          <div class="status-bar" id="status">Ready</div>
        </div>
      </div>

      <!-- Config Panel -->
      <div id="view-config" class="panel">
        <h2>Configuration</h2>
        <div class="row">
           <label>Authentication Token</label>
           <input id="authToken" type="password" placeholder="Optional" />
        </div>
        <div class="row" style="margin-top:16px;">
           <label>Config Path (YAML)</label>
           <input id="cfgPath" placeholder="llm.yaml" />
        </div>
        <div class="row" style="margin-top:16px;">
           <label>Content</label>
           <textarea id="cfgContent" style="height:300px; font-family:var(--font-mono);"></textarea>
        </div>
        <div class="row" style="margin-top:16px; display:flex; gap:12px;">
           <button class="btn btn-primary" onclick="app.loadCfg()">Load</button>
           <button class="btn btn-primary" onclick="app.saveCfg()">Save</button>
           <button class="btn" onclick="app.doctor()">Run Doctor</button>
        </div>
        <div class="row" style="margin-top:16px;">
           <label>Doctor Output</label>
           <textarea id="doctorOut" readonly style="height:100px; font-family:var(--font-mono); font-size:12px; background:var(--bg-input);"></textarea>
        </div>
      </div>

      <!-- Knowledge Panel -->
      <div id="view-knowledge" class="panel">
        <h2>Project Knowledge</h2>
        <div class="row">
           <label>Database Path</label>
           <input id="knowledgePath" placeholder="project_knowledge.db" />
        </div>
        <div class="row" style="margin-top:16px;">
           <label>Snapshot (Read Only)</label>
           <textarea id="knowledgeSnapshot" readonly style="height:400px; font-family:var(--font-mono); font-size:12px; background:var(--bg-input);"></textarea>
        </div>
        <div class="row" style="margin-top:16px;">
           <button class="btn btn-primary" onclick="app.refreshKnowledge()">Refresh</button>
        </div>
      </div>

      <!-- Prompt Panel -->
      <div id="view-prompt" class="panel">
        <h2>Global Prompt</h2>
        <div class="row">
           <textarea id="promptContent" style="height:500px; font-family:var(--font-mono);"></textarea>
        </div>
        <div class="row" style="margin-top:16px; display:flex; gap:12px;">
           <button class="btn btn-primary" onclick="app.loadPrompt()">Load</button>
           <button class="btn btn-primary" onclick="app.savePrompt()">Save</button>
        </div>
      </div>
      
      <!-- Hidden controls for logic compatibility -->
      <div style="display:none;">
        <input type="checkbox" id="dryRun" />
        <textarea id="importedContext"></textarea>
      </div>
    </main>

    <script src="/app.js"></script>
  </body>
</html>
"""


_APP_JS = r"""(() => {
  const $ = (id) => document.getElementById(id);
  
  // State
  const state = {
    messages: [],
    knowledgeSnapshot: null,
  };

  // Markdown Renderer (Simplified)
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

  // App Logic
  const app = {
    setStatus(text, ok = true) {
      const el = $("status");
      el.textContent = text;
      el.style.color = ok ? "var(--text-secondary)" : "#ff4444";
    },

    tab(name) {
      document.querySelectorAll(".nav-item").forEach(b => b.classList.remove("active"));
      // Simple heuristic for nav item highlighting
      const navs = document.querySelectorAll(".nav-item");
      if(name==='chat') navs[0].classList.add("active");
      if(name==='knowledge') navs[1].classList.add("active");
      if(name==='config') navs[2].classList.add("active");
      if(name==='prompt') navs[3].classList.add("active");

      document.querySelectorAll(".panel, .chat-view").forEach(p => p.style.display = "none");
      if (name === "chat") {
        $("view-chat").style.display = "flex";
      } else {
        $("view-" + name).style.display = "flex";
        $("view-" + name).classList.add("active");
      }
    },

    renderChat() {
      // Clear user generated messages (keep welcome msg if possible, but simpler to rebuild)
      const container = $("chat-container");
      // Find the inner content width container
      let contentDiv = container.querySelector(".chat-content-width");
      if (!contentDiv) {
         contentDiv = document.createElement("div");
         contentDiv.className = "chat-content-width";
         container.appendChild(contentDiv);
      }
      
      // We will rebuild the chat list for simplicity or append.
      // To avoid flashing, let's clear and rebuild.
      contentDiv.innerHTML = "";
      
      // Add welcome
      contentDiv.innerHTML += `
        <div class="msg" data-role="assistant" style="opacity:1; animation:none;">
          <div class="msg-avatar">✨</div>
          <div class="msg-content">
            <h2 style="margin-top:0;">你好, 我是 ReqX</h2>
            <p>我可以帮助你分析需求、生成代码或管理项目配置。</p>
          </div>
        </div>`;

      for (const msg of state.messages) {
        const div = document.createElement("div");
        div.className = "msg";
        div.setAttribute("data-role", msg.role);
        
        const avatar = document.createElement("div");
        avatar.className = "msg-avatar";
        avatar.textContent = msg.role === "user" ? "U" : "✨";
        
        const content = document.createElement("div");
        content.className = "msg-content";
        const esc = (s) => (s ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");
        
        if (msg.role === "assistant") {
          content.innerHTML = renderMarkdown(msg.content);
        } else {
          content.innerText = msg.content; // text only for user
        }
        
        div.appendChild(avatar);
        div.appendChild(content);
        contentDiv.appendChild(div);
      }
      // Scroll
      container.scrollTop = container.scrollHeight;
    },

    saveLocal() {
      localStorage.setItem("reqx_authToken", $("authToken").value);
      localStorage.setItem("reqx_cfgPath", $("cfgPath").value);
      localStorage.setItem("reqx_knowledgePath", $("knowledgePath").value);
    },

    loadLocal() {
      $("authToken").value = localStorage.getItem("reqx_authToken") || "";
      $("cfgPath").value = localStorage.getItem("reqx_cfgPath") || "llm.yaml";
      $("knowledgePath").value = localStorage.getItem("reqx_knowledgePath") || "project_knowledge.db";
    },

    async apiJson(method, path, body) {
      const token = $("authToken").value.trim();
      const headers = {"Content-Type": "application/json; charset=utf-8"};
      if (token) headers["Authorization"] = `Bearer ${token}`;
      try {
        const res = await fetch(path, {
          method, headers, body: body ? JSON.stringify(body) : undefined
        });
        return await res.json();
      } catch (e) {
        return {ok: false, error: {code: "network_error", message: String(e)}};
      }
    },

    async send() {
      app.saveLocal();
      const text = $("userInput").value;
      if (!text.trim()) return;
      
      // Add user message
      state.messages.push({role: "user", content: text});
      $("userInput").value = "";
      $("userInput").style.height = "auto"; // reset height
      app.renderChat();
      app.setStatus("Thinking...");

      const payload = {
        config_path: $("cfgPath").value.trim() || null,
        knowledge_path: $("knowledgePath").value.trim() || null,
        dry_run: $("dryRun").checked,
        imported_context: $("importedContext").value || "",
        messages: state.messages,
      };

      const r = await app.apiJson("POST", "/v1/chat/send", payload);
      if (!r.ok) {
        app.setStatus(`Error: ${r.error?.code || "unknown"}`, false);
        return;
      }
      
      state.messages.push({role: "assistant", content: r.result.reply});
      app.renderChat();
      
      if (r.result.knowledge_appended > 0) {
        await app.refreshKnowledge();
        app.setStatus(`Done (Learned ${r.result.knowledge_appended} items)`);
      } else {
        app.setStatus("Ready");
      }
    },

    async refreshKnowledge() {
      app.saveLocal();
      const kp = $("knowledgePath").value.trim();
      const r = await app.apiJson("POST", "/v1/knowledge/read", {knowledge_path: kp || null});
      if (r.ok) {
        $("knowledgeSnapshot").value = JSON.stringify(r.result, null, 2);
        app.setStatus("Knowledge refreshed");
      }
    },

    async loadCfg() {
      app.saveLocal();
      const p = $("cfgPath").value.trim();
      const r = await app.apiJson("POST", "/v1/config/read", {path: p || null});
      if (r.ok) {
        $("cfgContent").value = r.result.content || "";
        app.setStatus("Config loaded");
      } else {
        app.setStatus("Load failed", false);
      }
    },

    async saveCfg() {
      app.saveLocal();
      const r = await app.apiJson("POST", "/v1/config/write", {
        path: $("cfgPath").value.trim() || null, 
        content: $("cfgContent").value || "",
        dry_run: $("dryRun").checked
      });
      if (r.ok) app.setStatus("Config saved");
      else app.setStatus("Save failed", false);
    },

    async doctor() {
      app.saveLocal();
      const r = await app.apiJson("POST", "/v1/config/doctor", {path: $("cfgPath").value.trim() || null});
      if (r.ok) {
        $("doctorOut").value = JSON.stringify(r.result, null, 2);
        app.setStatus("Doctor passed");
      } else {
        $("doctorOut").value = "Failed";
        app.setStatus("Doctor failed", false);
      }
    },
    
    async loadPrompt() {
      const r = await app.apiJson("POST", "/v1/prompt/read", {});
      if (r.ok) {
         $("promptContent").value = r.result.content || "";
         app.setStatus("Prompt loaded");
      }
    },
    
    async savePrompt() {
      const r = await app.apiJson("POST", "/v1/prompt/write", {content: $("promptContent").value || "", dry_run: false});
      if (r.ok) app.setStatus("Prompt saved");
      else app.setStatus("Save failed", false);
    }
  };

  // Expose to window for onclick handlers
  window.app = app;

  // Init
  $("btnSend").addEventListener("click", app.send);
  
  // Auto-resize textarea
  const ta = $("userInput");
  ta.addEventListener("input", function() {
    this.style.height = "auto";
    this.style.height = (this.scrollHeight) + "px";
    if (this.value === "") this.style.height = "auto";
  });
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      app.send();
    }
  });

  app.loadLocal();
  app.refreshKnowledge().catch(()=>{});
  app.loadCfg().catch(()=>{});
  app.loadPrompt().catch(()=>{});

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
