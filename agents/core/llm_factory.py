from __future__ import annotations

from dataclasses import dataclass, field
import atexit
import functools
import inspect
from pathlib import Path
import os
import re
import threading
from typing import Any

import httpx
import yaml

from .types import LLMClient

@dataclass(frozen=True)
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    temperature: float = 0
    max_tokens: int | None = 1024

    input_char_limit: int = 8000
    output_char_limit: int = 20000

    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"

    env_file: str = ".env"

    azure_endpoint: str | None = None
    azure_deployment: str | None = None
    azure_api_version: str = "2024-02-15-preview"
    azure_api_key_env: str = "AZURE_OPENAI_API_KEY"
    warnings: tuple[str, ...] = field(default_factory=tuple)


_ALLOWED_PROVIDERS: frozenset[str] = frozenset(
    {"openai", "openai_compatible", "anthropic", "claude", "google", "gemini", "google_genai", "azure", "azure_openai"}
)

_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHARED_HTTPX_CLIENT: httpx.Client | None = None
_HTTPX_CLIENT_LOCK = threading.Lock()
_DEFAULT_HTTP_TIMEOUT_S = 180.0
_SECRET_ASSIGN_RE = re.compile(r"(?i)\b([A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD))\b\s*=\s*([^\s#]+)")
_AUTH_BEARER_RE = re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+([A-Za-z0-9._\\-]+)")
_INLINE_KV_RE = re.compile(r"(?i)\b(api_?key|token|secret|password)\b\s*[:=]\s*([^\s'\"\\)\\]]+)")
_TOKEN_PREFIX_RE = re.compile(
    r"\b(?:sk|xai|nvapi|ghp|glpat|hf)_[A-Za-z0-9_-]{10,}\b|\b(?:sk|xai|nvapi)-[A-Za-z0-9_-]{10,}\b|\bAIza[0-9A-Za-z_-]{20,}\b"
)


def _http_timeout_seconds() -> float:
    raw = (os.getenv("LLM_HTTP_TIMEOUT_S") or "").strip()
    if not raw:
        return _DEFAULT_HTTP_TIMEOUT_S
    try:
        v = float(raw)
        if v <= 0:
            return _DEFAULT_HTTP_TIMEOUT_S
        return v
    except Exception:
        return _DEFAULT_HTTP_TIMEOUT_S


def redact_secrets(text: str) -> str:
    if not text:
        return text
    out = str(text)
    out = _AUTH_BEARER_RE.sub("authorization: Bearer <redacted>", out)
    out = _SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}=<redacted>", out)
    out = _INLINE_KV_RE.sub(lambda m: f"{m.group(1)}=<redacted>", out)
    out = _TOKEN_PREFIX_RE.sub("<redacted>", out)
    return out


def redact_secrets_in_obj(obj: Any) -> Any:
    if obj is None:
        return obj
    if isinstance(obj, str):
        return redact_secrets(obj)
    if isinstance(obj, list):
        return [redact_secrets_in_obj(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(redact_secrets_in_obj(x) for x in obj)
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            out[k] = redact_secrets_in_obj(v)
        return out
    return obj


def _is_env_var_name(value: str) -> bool:
    return bool(_ENV_VAR_NAME_RE.fullmatch((value or "").strip()))


def _redact_if_suspicious(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return v
    if _is_env_var_name(v):
        return v
    return "<redacted>"


def _get_shared_http_client() -> httpx.Client:
    global _SHARED_HTTPX_CLIENT
    if _SHARED_HTTPX_CLIENT is not None:
        return _SHARED_HTTPX_CLIENT

    with _HTTPX_CLIENT_LOCK:
        if _SHARED_HTTPX_CLIENT is not None:
            return _SHARED_HTTPX_CLIENT

        timeout_s = _http_timeout_seconds()
        timeout = httpx.Timeout(timeout=timeout_s, connect=min(30.0, timeout_s))
        try:
            client = httpx.Client(http2=True, timeout=timeout)
        except ImportError:
            client = httpx.Client(timeout=timeout)
        _SHARED_HTTPX_CLIENT = client
        atexit.register(client.close)
        return client


def _default_config_path() -> Path:
    forced = os.getenv("LLM_CONFIG_PATH")
    if forced:
        return Path(forced)

    cwd = Path.cwd()
    default_path = cwd / "llm.yaml"
    if default_path.exists():
        return default_path

    candidates: list[Path] = []
    for pat in ("llm*.yaml", "llm*.yml"):
        candidates.extend(cwd.glob(pat))
    candidates = sorted({p for p in candidates if p.name.lower() not in {"llm.yaml", "llm.yml"}})
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise RuntimeError(f"发现多个候选配置文件：{', '.join(p.name for p in candidates)}；请使用 --config 或设置 LLM_CONFIG_PATH")

    return default_path


def _load_env_file(env_file: str, *, allowed_keys: set[str], config_dir: Path) -> None:
    forced = os.getenv("LLM_ENV_PATH")
    env_path = Path(forced) if forced else (config_dir / env_file if not Path(env_file).is_absolute() else Path(env_file))
    if not env_path.exists():
        return

    try:
        from dotenv import dotenv_values  # type: ignore

        values = dotenv_values(env_path)
        for key, value in values.items():
            if not key or value is None:
                continue
            if key in allowed_keys and key not in os.environ:
                os.environ[key] = str(value)
        return
    except Exception:
        pass

    def _strip_unquoted_comment(s: str) -> str:
        out = s
        for i, ch in enumerate(out):
            if ch == "#" and i > 0 and out[i - 1].isspace():
                return out[:i].rstrip()
        return out.rstrip()

    def _unescape_quoted(s: str, quote: str) -> str:
        if not s:
            return ""
        out: list[str] = []
        i = 0
        if quote == '"':
            mapping = {"n": "\n", "r": "\r", "t": "\t", "\\": "\\", '"': '"'}
        else:
            mapping = {"\\": "\\", "'": "'"}
        while i < len(s):
            ch = s[i]
            if ch == "\\" and i + 1 < len(s):
                nxt = s[i + 1]
                if nxt in mapping:
                    out.append(mapping[nxt])
                    i += 2
                    continue
            out.append(ch)
            i += 1
        return "".join(out)

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')):
            q = value[0]
            if value.endswith(q) and len(value) >= 2:
                value = _unescape_quoted(value[1:-1], q)
            else:
                value = value[1:]
        else:
            value = _strip_unquoted_comment(value)
        if key and key in allowed_keys and key not in os.environ:
            os.environ[key] = value


@functools.lru_cache(maxsize=32)
def _load_llm_config_cached(resolved_path: str, mtime_ns: int, strict: bool) -> LLMConfig:
    return _load_llm_config_uncached(Path(resolved_path), strict=strict)


def load_llm_config(path: str | os.PathLike[str] | None = None, *, strict: bool = False) -> LLMConfig:
    config_path = Path(path) if path else _default_config_path()
    resolved_path = str(config_path.resolve()) if config_path.exists() else str(config_path)
    mtime_ns = int(config_path.stat().st_mtime_ns) if config_path.exists() else -1
    return _load_llm_config_cached(resolved_path, mtime_ns, strict)


def _load_llm_config_uncached(config_path: Path, *, strict: bool) -> LLMConfig:
    warnings: list[str] = []

    def _fail(message: str) -> None:
        if strict:
            raise RuntimeError(message)
        warnings.append(message)

    if not config_path.exists():
        if strict:
            raise RuntimeError(f"缺少 LLM 配置文件：{config_path}（请先运行 reqx init-config 生成 llm.yaml）")
        return LLMConfig(warnings=(f"未找到配置文件：{config_path}，已回落默认配置",))

    try:
        raw = config_path.read_text(encoding="utf-8")
    except Exception as e:
        _fail(f"无法读取 LLM 配置文件：{config_path}（{e}）")
        return LLMConfig(warnings=tuple(warnings))

    try:
        data = yaml.safe_load(raw) or {}
    except Exception as e:
        _fail(f"LLM 配置文件解析失败：{config_path}（{e}）")
        return LLMConfig(warnings=tuple(warnings))

    if not isinstance(data, dict):
        _fail(f"LLM 配置文件格式错误：{config_path}（期望 YAML object）")
        return LLMConfig(warnings=tuple(warnings))

    def _strip_wrappers(s: str) -> str:
        out = (s or "").strip()
        if out.startswith("```") and out.rstrip().endswith("```"):
            inner = out.strip()
            inner = inner[3:-3]
            inner = inner.lstrip("\r\n")
            if "\n" in inner:
                first, rest = inner.split("\n", 1)
                tag = first.strip()
                if tag and all(ch.isalnum() or ch in {"-", "_", "+"} for ch in tag):
                    inner = rest
            out = inner.strip()
        if len(out) >= 2 and out[0] == out[-1] and out[0] in {"`", "'", '"'}:
            out = out[1:-1].strip()
        return out

    def _get_str(key: str, default: str) -> str:
        v = data.get(key, default)
        if v is None:
            return default
        if isinstance(v, str):
            s = _strip_wrappers(v)
            if s:
                return s
            _fail(f"配置字段 {key} 不能为空字符串（文件：{config_path}）")
            return default
        _fail(f"配置字段 {key} 类型错误：{type(v).__name__}（期望 string，文件：{config_path}）")
        return default

    def _get_optional_str(key: str) -> str | None:
        if key not in data:
            return None
        v = data.get(key)
        if v is None:
            return None
        if isinstance(v, str):
            s = _strip_wrappers(v)
            return s if s else None
        _fail(f"配置字段 {key} 类型错误：{type(v).__name__}（期望 string|null，文件：{config_path}）")
        return None

    def _get_int(key: str, default: int, *, allow_none: bool = False, min_value: int | None = None) -> int | None:
        if allow_none and key in data and data.get(key) is None:
            return None
        v = data.get(key, default)
        try:
            iv = int(v)
        except Exception:
            _fail(f"配置字段 {key} 值非法：{v!r}（期望 int，文件：{config_path}）")
            return default
        if min_value is not None and iv < min_value:
            _fail(f"配置字段 {key} 取值过小：{iv}（最小 {min_value}，文件：{config_path}）")
            return default
        return iv

    provider = _get_str("provider", "openai")
    model = _get_str("model", "gpt-4o-mini")
    temperature_raw = data.get("temperature", 0)
    try:
        temperature = float(temperature_raw)
        if temperature < 0:
            _fail(f"配置字段 temperature 取值过小：{temperature}（最小 0，文件：{config_path}）")
            temperature = 0
    except Exception:
        _fail(f"配置字段 temperature 值非法：{temperature_raw!r}（期望 float，文件：{config_path}）")
        temperature = 0

    max_tokens = _get_int("max_tokens", 1024, allow_none=True, min_value=1)
    input_char_limit = int(_get_int("input_char_limit", 8000, min_value=1) or 8000)
    output_char_limit = int(_get_int("output_char_limit", 20000, min_value=1) or 20000)
    base_url = _get_optional_str("base_url")
    api_key_env = _get_str("api_key_env", "OPENAI_API_KEY")
    env_file = _get_str("env_file", ".env")

    azure_endpoint = _get_optional_str("azure_endpoint")
    azure_deployment = _get_optional_str("azure_deployment")
    azure_api_version = _get_str("azure_api_version", "2024-02-15-preview")
    azure_api_key_env = _get_str("azure_api_key_env", "AZURE_OPENAI_API_KEY")

    normalized_provider = provider.lower().strip()
    if normalized_provider not in _ALLOWED_PROVIDERS:
        _fail(f"未知 provider：{provider!r}（允许值：openai/openai_compatible/anthropic/google/azure）")

    if not _is_env_var_name(api_key_env):
        _fail(f"配置字段 api_key_env 必须是环境变量名（例如 OPENAI_API_KEY），不要粘贴 API Key（文件：{config_path}）")
        api_key_env = "OPENAI_API_KEY"

    if not _is_env_var_name(azure_api_key_env):
        _fail(
            f"配置字段 azure_api_key_env 必须是环境变量名（例如 AZURE_OPENAI_API_KEY），不要粘贴 API Key（文件：{config_path}）"
        )
        azure_api_key_env = "AZURE_OPENAI_API_KEY"

    config_dir = config_path.parent if config_path.parent else Path.cwd()
    allowed_keys: set[str] = {api_key_env}
    if azure_api_key_env:
        allowed_keys.add(azure_api_key_env)
    _load_env_file(env_file, allowed_keys=allowed_keys, config_dir=config_dir)

    return LLMConfig(
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        input_char_limit=input_char_limit,
        output_char_limit=output_char_limit,
        base_url=base_url,
        api_key_env=api_key_env,
        env_file=env_file,
        azure_endpoint=azure_endpoint,
        azure_deployment=azure_deployment,
        azure_api_version=azure_api_version,
        azure_api_key_env=azure_api_key_env,
        warnings=tuple(warnings),
    )


def _filter_kwargs(callable_obj: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        sig = inspect.signature(callable_obj)
    except Exception:
        return kwargs

    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return kwargs

    return {k: v for k, v in kwargs.items() if k in sig.parameters}


def get_llm(*, config_path: str | os.PathLike[str] | None = None, strict: bool = True, **overrides: Any) -> LLMClient:
    cfg = load_llm_config(config_path, strict=strict)
    provider = cfg.provider.lower().strip()

    max_tokens = overrides.pop("max_tokens", cfg.max_tokens)
    temperature = overrides.pop("temperature", cfg.temperature)

    if provider not in _ALLOWED_PROVIDERS:
        raise RuntimeError(
            f"未知 provider：{cfg.provider!r}（允许值：openai/openai_compatible/anthropic/google/azure）"
        )

    if provider in {"azure", "azure_openai"}:
        from langchain_openai import AzureChatOpenAI

        if not cfg.azure_api_key_env:
            raise RuntimeError("缺少 azure_api_key_env（请在配置文件中设置）")
        if not cfg.azure_api_version:
            raise RuntimeError("缺少 azure_api_version（请在配置文件中设置）")
        api_key = os.getenv(cfg.azure_api_key_env)
        if not cfg.azure_endpoint:
            raise RuntimeError("缺少 azure_endpoint（请在配置文件中设置）")
        if not cfg.azure_deployment:
            raise RuntimeError("缺少 azure_deployment（请在配置文件中设置）")
        if not api_key:
            raise RuntimeError(f"缺少 Azure API Key（环境变量 {_redact_if_suspicious(cfg.azure_api_key_env)} 未设置）")

        kwargs: dict[str, Any] = {
            "azure_endpoint": cfg.azure_endpoint,
            "azure_deployment": cfg.azure_deployment,
            "api_version": cfg.azure_api_version,
            "api_key": api_key,
            "temperature": temperature,
            **overrides,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return AzureChatOpenAI(**_filter_kwargs(AzureChatOpenAI, kwargs))

    if provider in {"claude", "anthropic"}:
        try:
            from langchain_anthropic import ChatAnthropic
        except Exception as e:
            raise RuntimeError("缺少依赖：langchain-anthropic（Claude/Anthropic）") from e

        api_key = os.getenv(cfg.api_key_env)
        if not api_key:
            raise RuntimeError(f"缺少 Anthropic API Key（环境变量 {_redact_if_suspicious(cfg.api_key_env)} 未设置）")

        kwargs = {"model": cfg.model, "api_key": api_key, "temperature": temperature, **overrides}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return ChatAnthropic(**_filter_kwargs(ChatAnthropic, kwargs))

    if provider in {"gemini", "google", "google_genai"}:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except Exception as e:
            raise RuntimeError("缺少依赖：langchain-google-genai（Gemini/Google）") from e

        api_key = os.getenv(cfg.api_key_env)
        if not api_key:
            raise RuntimeError(f"缺少 Google API Key（环境变量 {_redact_if_suspicious(cfg.api_key_env)} 未设置）")

        kwargs = {"model": cfg.model, "google_api_key": api_key, "temperature": temperature, **overrides}
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens
        return ChatGoogleGenerativeAI(**_filter_kwargs(ChatGoogleGenerativeAI, kwargs))

    if provider == "openai_compatible":
        if not cfg.base_url:
            raise RuntimeError("provider=openai_compatible 需要 base_url（请在 llm.yaml 配置或使用 reqx init-config/wizard）")
        api_key = os.getenv(cfg.api_key_env)
        if not api_key:
            raise RuntimeError(f"缺少 OpenAI API Key（环境变量 {_redact_if_suspicious(cfg.api_key_env)} 未设置）")

        from langchain_openai import ChatOpenAI

        http_client = overrides.pop("http_client", None)
        if http_client is None:
            http_client = _get_shared_http_client()

        kwargs = {
            "model": cfg.model,
            "api_key": api_key,
            "base_url": cfg.base_url,
            "temperature": temperature,
            "http_client": http_client,
            **overrides,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return ChatOpenAI(**_filter_kwargs(ChatOpenAI, kwargs))

    if provider == "openai":
        if cfg.base_url:
            raise RuntimeError("provider=openai 不应配置 base_url（如需兼容接口请使用 provider=openai_compatible）")
        api_key = os.getenv(cfg.api_key_env)
        if not api_key:
            raise RuntimeError(f"缺少 OpenAI API Key（环境变量 {_redact_if_suspicious(cfg.api_key_env)} 未设置）")

        from langchain_openai import ChatOpenAI

        http_client = overrides.pop("http_client", None)
        if http_client is None:
            http_client = _get_shared_http_client()

        kwargs = {
            "model": cfg.model,
            "api_key": api_key,
            "temperature": temperature,
            "http_client": http_client,
            **overrides,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return ChatOpenAI(**_filter_kwargs(ChatOpenAI, kwargs))

    raise RuntimeError(f"未知 provider：{cfg.provider!r}（允许值：openai/openai_compatible/anthropic/google/azure）")
