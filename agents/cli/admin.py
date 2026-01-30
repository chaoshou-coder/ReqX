from __future__ import annotations

import importlib
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _ask(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        return (input() or "").strip()
    except EOFError:
        return ""


def _ask_yes_no(prompt: str, *, default: bool = False) -> bool:
    suffix = " (Y/n) " if default else " (y/N) "
    raw = _ask(prompt.rstrip() + suffix).strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "是", "1", "true"}


def _ask_choice(prompt: str, choices: list[str], *, default_index: int = 0) -> str:
    if not choices:
        return ""
    for i, c in enumerate(choices, 1):
        tag = " (默认)" if i - 1 == default_index else ""
        sys.stdout.write(f"{i}) {c}{tag}\n")
    raw = _ask(prompt).strip()
    if not raw:
        return choices[default_index]
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(choices):
            return choices[idx - 1]
    return raw


def _ask_secret(prompt: str) -> str:
    try:
        import getpass

        return (getpass.getpass(prompt) or "").strip()
    except Exception:
        return _ask(prompt).strip()


def _write_env_kv(env_path: Path, key: str, value: str) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    out: list[str] = []
    replaced = False
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            out.append(line)
            continue
        k, _v = s.split("=", 1)
        if k.strip() == key:
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    env_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _dump_yaml(path: Path, data: dict[str, Any]) -> None:
    try:
        import yaml
    except Exception as e:
        raise RuntimeError(f"缺少依赖 PyYAML，无法写入配置：{e}") from e
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def init_config_main(*, config_out: str | None) -> int:
    repo_root = _repo_root()
    src = repo_root / "llm.yaml.example"
    if config_out:
        dst = Path(config_out)
    else:
        default_dst = repo_root / "llm.yaml"
        raw = _ask(f"请输入要生成的配置文件路径（回车使用 {default_dst}）：")
        dst = Path(raw) if raw else default_dst
    if not src.exists():
        sys.stdout.write("缺少 llm.yaml.example，无法初始化。\n")
        return 1
    if dst.exists():
        overwrite = _ask_yes_no(f"{dst.name} 已存在，是否覆盖？", default=False)
        if not overwrite:
            sys.stdout.write("未覆盖。\n")
            return 0
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    sys.stdout.write(f"已生成：{dst}\n")
    sys.stdout.write(f"下一步：编辑 {dst.name}，并在 .env 中配置对应的 API Key（不要把 Key 写进 yaml）。\n")
    return 0


def check_deps_main() -> int:
    targets = [
        ("PyYAML", "yaml"),
        ("crewai", "crewai"),
        ("langchain-openai", "langchain_openai"),
    ]
    ok = True
    for label, mod in targets:
        try:
            importlib.import_module(mod)
            sys.stdout.write(f"已安装：{label}\n")
        except Exception:
            ok = False
            sys.stdout.write(f"缺少：{label}\n")
    if ok:
        sys.stdout.write("依赖检查通过。\n")
        return 0
    sys.stdout.write("依赖不完整：请先 pip install -e . 或按需安装 extra（见 README）。\n")
    return 1


def clean_main() -> int:
    from clean_repo import clean

    repo_root = _repo_root()
    result = clean(repo_root)
    sys.stdout.write(f"清理完成：删除 {result['removed_count']} 项\n")
    return 0


def install_main(*, no_deps: bool = True) -> int:
    repo_root = _repo_root()
    python = sys.executable
    cmd = [python, "-m", "pip", "install", "-e", str(repo_root)]
    if no_deps:
        cmd.append("--no-deps")
    subprocess.check_call(cmd)
    sys.stdout.write("完成：已以可编辑模式安装本仓库。\n")
    return 0


def check_api_main(*, config_path: str | None) -> int:
    try:
        import yaml
        from ..core.llm_factory import get_llm, load_llm_config, redact_secrets
    except Exception as e:
        sys.stdout.write(f"缺少依赖，无法进行健康检查：{e}\n")
        return 1

    repo_root = _repo_root()
    if config_path:
        resolved = Path(config_path)
    else:
        default_cfg = repo_root / "llm.yaml"
        raw = _ask(f"请输入配置文件路径（回车使用 {default_cfg}）：")
        resolved = Path(raw) if raw else default_cfg
    if not resolved.exists():
        sys.stdout.write(f"配置文件不存在：{resolved}\n")
        return 1
    try:
        cfg = load_llm_config(str(resolved), strict=True)
    except Exception as e:
        sys.stdout.write(f"配置解析失败：{redact_secrets(str(e))}\n")
        return 1
    try:
        llm = get_llm(config_path=str(resolved), strict=True, max_tokens=16, temperature=0)
    except Exception as e:
        sys.stdout.write(f"模型初始化失败：{redact_secrets(str(e))}\n")
        return 1
    started = time.time()
    try:
        out = (llm.invoke("Return exactly: OK").content or "").strip()
    except Exception as e:
        payload = {"ok": False, "error": {"code": "invoke_failed", "message": redact_secrets(str(e))}}
        sys.stdout.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
        return 1
    elapsed_ms = int((time.time() - started) * 1000)
    payload = {
        "ok": bool(out),
        "provider": cfg.provider,
        "model": cfg.model,
        "latency_ms": elapsed_ms,
        "response_preview": (out[:80] if isinstance(out, str) else ""),
    }
    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
    return 0 if payload["ok"] else 1


def wizard_main() -> int:
    repo_root = _repo_root()
    sys.stdout.write("\n进入一键配置向导。你可以一路按回车使用默认值，也可以随时输入自定义值。\n\n")

    default_cfg = repo_root / "llm.yaml"
    cfg_raw = _ask(f"请输入要生成的配置文件路径（回车使用 {default_cfg}）：").strip()
    cfg_path = Path(cfg_raw) if cfg_raw else default_cfg
    if cfg_path.exists() and not _ask_yes_no(f"{cfg_path.name} 已存在，是否覆盖？", default=False):
        sys.stdout.write("未覆盖配置文件。\n")
        return 0

    providers = ["openai", "openai_compatible", "azure", "anthropic", "google"]
    provider = _ask_choice("请选择 provider（输入序号或直接输入值）：", providers, default_index=0).strip()
    provider = (provider or "openai").strip()

    model_default = "gpt-4o-mini" if provider == "openai" else "deepseek-chat"
    model = _ask(f"请输入 model（回车使用 {model_default}）：").strip() or model_default

    cfg: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "temperature": 0,
        "max_tokens": 1024,
        "input_char_limit": 8000,
        "output_char_limit": 20000,
        "env_file": ".env",
    }

    if provider == "openai_compatible":
        while True:
            base_url = _ask("请输入 base_url（例如 https://api.deepseek.com/v1）：").strip()
            if base_url:
                cfg["base_url"] = base_url
                break
            sys.stdout.write("base_url 不能为空（provider=openai_compatible 必填）。\n")
        api_key_env = _ask("请输入 api_key_env（回车使用 DEEPSEEK_API_KEY）：").strip() or "DEEPSEEK_API_KEY"
        cfg["api_key_env"] = api_key_env
    elif provider in {"azure", "azure_openai"}:
        cfg["provider"] = "azure"
        cfg["azure_endpoint"] = _ask("请输入 azure_endpoint（例如 https://xxx.openai.azure.com/）：").strip()
        cfg["azure_deployment"] = _ask("请输入 azure_deployment（部署名）：").strip()
        cfg["azure_api_version"] = _ask("请输入 azure_api_version（回车使用 2024-02-15-preview）：").strip() or "2024-02-15-preview"
        cfg["api_key_env"] = _ask("请输入 api_key_env（回车使用 OPENAI_API_KEY）：").strip() or "OPENAI_API_KEY"
        cfg["azure_api_key_env"] = _ask("请输入 azure_api_key_env（回车使用 AZURE_OPENAI_API_KEY）：").strip() or "AZURE_OPENAI_API_KEY"
    elif provider in {"anthropic", "claude"}:
        cfg["provider"] = "anthropic"
        cfg["api_key_env"] = _ask("请输入 api_key_env（回车使用 ANTHROPIC_API_KEY）：").strip() or "ANTHROPIC_API_KEY"
    elif provider in {"google", "gemini", "google_genai"}:
        cfg["provider"] = "google"
        cfg["api_key_env"] = _ask("请输入 api_key_env（回车使用 GOOGLE_API_KEY）：").strip() or "GOOGLE_API_KEY"
    else:
        cfg["provider"] = "openai"
        cfg["api_key_env"] = _ask("请输入 api_key_env（回车使用 OPENAI_API_KEY）：").strip() or "OPENAI_API_KEY"

    env_default = repo_root / (cfg.get("env_file") or ".env")
    env_raw = _ask(f"请输入 env 文件路径（回车使用 {env_default}）：").strip()
    env_path = Path(env_raw) if env_raw else env_default
    cfg["env_file"] = env_path.name if env_path.parent == repo_root else str(env_path)

    _dump_yaml(cfg_path, cfg)
    sys.stdout.write(f"\n已生成配置文件：{cfg_path}\n\n")

    key_env = ""
    if cfg.get("provider") in {"azure"}:
        key_env = str(cfg.get("azure_api_key_env") or "")
    else:
        key_env = str(cfg.get("api_key_env") or "")

    if key_env and _ask_yes_no(f"是否现在把 {key_env} 写入 env 文件？（推荐）", default=True):
        key_value = _ask_secret(f"请输入 {key_env}（输入时不回显；回车跳过）：")
        if key_value:
            _write_env_kv(env_path, key_env, key_value)
            sys.stdout.write(f"已写入：{env_path}\n\n")
        else:
            sys.stdout.write("未写入 env。\n\n")

    if _ask_yes_no("现在运行健康检查（check-api）？", default=True):
        sys.stdout.write("\n")
        code = check_api_main(config_path=str(cfg_path))
        if code == 0:
            sys.stdout.write("\n配置完成：健康检查通过。\n")
            return 0
        sys.stdout.write("\n健康检查未通过。\n")
    return 0

