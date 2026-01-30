from __future__ import annotations

from dataclasses import asdict
import hmac
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..service.knowledge_service import KnowledgeService
from ..storage.knowledge_store import Role


class _JsonError(Exception):
    def __init__(self, code: str, *, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


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


def _require_str(obj: dict[str, Any], key: str) -> str:
    v = obj.get(key)
    if not isinstance(v, str):
        raise _JsonError(f"missing_or_invalid_{key}")
    s = v.strip()
    if not s:
        raise _JsonError(f"missing_or_invalid_{key}")
    return s


def _opt_str(obj: dict[str, Any], key: str) -> str | None:
    v = obj.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise _JsonError(f"invalid_{key}")
    s = v.strip()
    return s or None


def _opt_role(obj: dict[str, Any], key: str) -> Role:
    v = obj.get(key)
    if v is None:
        return "system"
    if v not in {"user", "assistant", "system"}:
        raise _JsonError(f"invalid_{key}")
    return v


def _opt_items(obj: dict[str, Any], key: str) -> list[str]:
    v = obj.get(key)
    if v is None:
        return []
    if not isinstance(v, list):
        raise _JsonError(f"invalid_{key}")
    out: list[str] = []
    for item in v:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if s:
            out.append(s)
    return out


class KnowledgeHttpApi:
    def __init__(
        self,
        *,
        bind: str,
        port: int,
        base_dir: str | Path | None = None,
        default_knowledge_path: str | Path | None = None,
        token_env: str | None = "REQX_KNOWLEDGE_API_TOKEN",
        token_value: str | None = None,
        max_body_bytes: int = 2 * 1024 * 1024,
    ):
        self.bind = bind
        self.port = port
        self.service = KnowledgeService(base_dir=base_dir, default_path=default_knowledge_path)
        self.max_body_bytes = max_body_bytes
        self._token: str | None = token_value
        if self._token is None and token_env:
            self._token = os.getenv(token_env) or None

    def create_server(self) -> ThreadingHTTPServer:
        handler = self._make_handler()
        return ThreadingHTTPServer((self.bind, self.port), handler)

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        api = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ReqXKnowledgeApi/1"

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
                expected = api._token
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
                    if parsed.path == "/health":
                        _write_json(self, 200, {"ok": True})
                        return
                    if parsed.path == "/v1/knowledge/read":
                        self._require_auth(allow_if_no_token=True)
                        qs = parse_qs(parsed.query or "")
                        kp = (qs.get("knowledge_path") or [None])[0]
                        snap = api.service.read(kp)
                        _write_json(self, 200, {"ok": True, "result": asdict(snap)})
                        return
                    raise _JsonError("not_found", status=404)
                except _JsonError as e:
                    headers = {"WWW-Authenticate": "Bearer"} if e.status == 401 else None
                    _write_json(self, e.status, {"ok": False, "error": {"code": e.code}}, headers=headers)
                except ValueError as e:
                    code = str(e) or "invalid_request"
                    _write_json(self, 400, {"ok": False, "error": {"code": code}})
                except Exception:
                    _write_json(self, 500, {"ok": False, "error": {"code": "internal_error"}})

            def do_POST(self) -> None:
                try:
                    parsed = urlparse(self.path)
                    self._require_auth(allow_if_no_token=True)
                    body = _read_json(self, limit=api.max_body_bytes)

                    if parsed.path == "/v1/knowledge/append":
                        kp = _opt_str(body, "knowledge_path")
                        role = _opt_role(body, "role")
                        items = _opt_items(body, "items")
                        dry_run = bool(body.get("dry_run"))
                        if not items:
                            raise _JsonError("missing_or_invalid_items")
                        n = api.service.append_items(items, knowledge_path=kp, role=role, dry_run=dry_run)
                        _write_json(self, 200, {"ok": True, "result": {"appended": n, "dry_run": dry_run}})
                        return

                    if parsed.path == "/v1/knowledge/set_project_name":
                        kp = _opt_str(body, "knowledge_path")
                        name = _require_str(body, "project_name")
                        dry_run = bool(body.get("dry_run"))
                        api.service.set_project_name(name, knowledge_path=kp, dry_run=dry_run)
                        _write_json(self, 200, {"ok": True, "result": {"dry_run": dry_run}})
                        return

                    if parsed.path == "/v1/knowledge/set_latest_spec":
                        kp = _opt_str(body, "knowledge_path")
                        spec = _require_str(body, "latest_spec_yaml")
                        dry_run = bool(body.get("dry_run"))
                        api.service.set_latest_spec_yaml(spec, knowledge_path=kp, dry_run=dry_run)
                        _write_json(self, 200, {"ok": True, "result": {"dry_run": dry_run}})
                        return

                    raise _JsonError("not_found", status=404)
                except _JsonError as e:
                    headers = {"WWW-Authenticate": "Bearer"} if e.status == 401 else None
                    _write_json(self, e.status, {"ok": False, "error": {"code": e.code}}, headers=headers)
                except ValueError as e:
                    code = str(e) or "invalid_request"
                    _write_json(self, 400, {"ok": False, "error": {"code": code}})
                except Exception:
                    _write_json(self, 500, {"ok": False, "error": {"code": "internal_error"}})

        return Handler

    def serve_forever(self) -> None:
        with self.create_server() as httpd:
            httpd.serve_forever()
