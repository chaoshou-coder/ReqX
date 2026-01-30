from __future__ import annotations

from dataclasses import asdict
import base64
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
    generate_project_names,
    load_global_prompt,
    parse_knowledge_update,
    tool_run,
    truncate_text,
)
from ..service.knowledge_service import KnowledgeService
from ..storage.knowledge_store import open_knowledge_store
from ..core.llm_factory import get_llm, load_llm_config, redact_secrets
from ..core.requirement_excavation_skill import RequirementExcavationSkill


_DEFAULT_BIND = "127.0.0.1"
_DEFAULT_PORT = 8788
_MAX_BODY_BYTES_DEFAULT = 2 * 1024 * 1024


_WEBUI_HTML_PATH = Path(__file__).resolve().parent / "static" / "webui.html"


def _make_nonce() -> str:
    return base64.b64encode(os.urandom(16)).decode("ascii")


def _inject_nonce(html: str, nonce: str) -> str:
    def _inject_first_tag(tag: str, doc: str) -> str:
        lower = doc.lower()
        needle = f"<{tag}"
        start = lower.find(needle)
        if start < 0:
            return doc
        end = doc.find(">", start)
        if end < 0:
            return doc
        head = doc[start:end]
        if "nonce=" in head.lower():
            return doc
        insert_at = start + len(needle)
        return doc[:insert_at] + f' nonce="{nonce}"' + doc[insert_at:]

    out = html
    out = _inject_first_tag("script", out)
    out = _inject_first_tag("style", out)
    return out


def _load_webui_html() -> str:
    try:
        return _WEBUI_HTML_PATH.read_text(encoding="utf-8")
    except Exception:
        return """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>ReqX WebUI</title>
  </head>
  <body style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Noto Sans SC,Arial,sans-serif;padding:24px;">
    <h2>WebUI 静态资源缺失</h2>
    <p>未找到构建产物：agents/web/static/webui.html</p>
    <p>请在 agents/web/ui 目录执行：</p>
    <pre>npm ci
npm run build:webui</pre>
  </body>
</html>
"""


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


def _apply_security_headers(handler: BaseHTTPRequestHandler, *, nonce: str | None = None) -> None:
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Referrer-Policy", "no-referrer")
    script_src = "script-src 'self'; "
    style_src = "style-src 'self'; "
    if nonce:
        script_src = f"script-src 'self' 'nonce-{nonce}'; "
        style_src = f"style-src 'self' 'nonce-{nonce}'; "
    handler.send_header(
        "Content-Security-Policy",
        "default-src 'none'; "
        "connect-src 'self'; "
        f"{script_src}"
        f"{style_src}"
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
    handler: BaseHTTPRequestHandler,
    status: int,
    body: str,
    *,
    content_type: str,
    headers: dict[str, str] | None = None,
    nonce: str | None = None,
) -> None:
    raw = body.encode("utf-8")
    handler.send_response(status)
    _apply_security_headers(handler, nonce=nonce)
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
                        nonce = _make_nonce()
                        html = _inject_nonce(_load_webui_html(), nonce)
                        _write_text(self, 200, html, content_type="text/html; charset=utf-8", nonce=nonce)
                        return
                    if parsed.path == "/favicon.ico":
                        self.send_response(204)
                        _apply_security_headers(self)
                        self.end_headers()
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

                        cmd = (messages[-1][1] or "").strip().lower()

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

                        if cmd in {"/exit", "/quit"}:
                            _write_json(
                                self,
                                200,
                                {
                                    "ok": True,
                                    "result": {"reply": "已退出。", "knowledge_appended": 0, "dry_run": bool(dry_run or server.dry_run)},
                                },
                            )
                            return
                        if cmd in {"/help", "/h"}:
                            _write_json(
                                self,
                                200,
                                {
                                    "ok": True,
                                    "result": {
                                        "reply": (
                                            "命令说明：\n"
                                            "- /spec: 基于项目知识生成需求 YAML（不结束）\n"
                                            "- /done: 生成需求 YAML → 生成 10 个项目名（默认选第 1 个）\n"
                                            "- /show: 显示当前项目知识\n"
                                            "- /reset: 清空本次页面对话记录\n"
                                            "- /exit: 退出\n"
                                        ),
                                        "knowledge_appended": 0,
                                        "dry_run": bool(dry_run or server.dry_run),
                                    },
                                },
                            )
                            return
                        if cmd == "/reset":
                            _write_json(
                                self,
                                200,
                                {
                                    "ok": True,
                                    "result": {
                                        "reply": "本轮对话记录已清空（仅影响本页面显示）。\n",
                                        "knowledge_appended": 0,
                                        "dry_run": bool(dry_run or server.dry_run),
                                    },
                                },
                            )
                            return
                        if cmd == "/show":
                            _write_json(
                                self,
                                200,
                                {
                                    "ok": True,
                                    "result": {
                                        "reply": (ks.transcript() or "") + "\n",
                                        "knowledge_appended": 0,
                                        "dry_run": bool(dry_run or server.dry_run),
                                    },
                                },
                            )
                            return
                        if cmd in {"/spec", "/done"}:
                            llm = get_llm(config_path=str(cfg_path), strict=True)
                            tool = RequirementExcavationSkill(llm=llm, config_path=str(cfg_path))
                            surface = ("项目知识（按时间顺序）：\n" + ks.transcript()) if ks.transcript() else ""
                            spec_yaml = tool_run(tool, surface)
                            ks.latest_spec_yaml = spec_yaml
                            if not (dry_run or server.dry_run):
                                ks.save()
                            reply = spec_yaml
                            if cmd == "/done":
                                names = generate_project_names(llm, ks.latest_spec_yaml or spec_yaml)
                                project_name = names[0] if names else "未命名项目"
                                ks.project_name = project_name
                                if not (dry_run or server.dry_run):
                                    ks.save()
                                reply = (
                                    spec_yaml
                                    + "\n\n"
                                    + "候选项目名：\n"
                                    + "\n".join([f"{i+1}. {n}" for i, n in enumerate(names)])
                                    + "\n\n"
                                    + f"已选择项目名称：{project_name}\n"
                                    + "全流程结束。可输入 /exit 退出。\n"
                                )
                            _write_json(
                                self,
                                200,
                                {"ok": True, "result": {"reply": reply, "knowledge_appended": 0, "dry_run": bool(dry_run or server.dry_run)}},
                            )
                            return
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
