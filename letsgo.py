from __future__ import annotations

import argparse
import importlib
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


def _ask(prompt: str) -> str:
    print(prompt, end="")
    try:
        return (input() or "").strip()
    except EOFError:
        return ""


def _menu() -> str:
    print("请选择要执行的操作：")
    print("1) 初始化/更新 LLM 配置文件（llm.yaml）")
    print("2) 验证 API 配置是否可用（健康检查）")
    print("3) 清理项目缓存与构建产物（一键清洁）")
    print("4) 检查依赖是否已安装（小白自检）")
    print("5) 安装本仓库到当前环境（pip editable）")
    print("6) 一键配置向导（生成配置 → 写入 .env → 健康检查）")
    print("0) 退出")
    return _ask("输入序号：")


def _pause() -> None:
    print("\n按回车返回菜单...", end="")
    try:
        input()
    except EOFError:
        return


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
        print(f"{i}) {c}{tag}")
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
        k, _v = line.split("=", 1)
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


def _wizard(repo_root: Path) -> int:
    print("\n进入一键配置向导。你可以一路按回车使用默认值，也可以随时输入自定义值。\n")

    default_cfg = repo_root / "llm.yaml"
    cfg_raw = _ask(f"请输入要生成的配置文件路径（回车使用 {default_cfg}）：").strip()
    cfg_path = Path(cfg_raw) if cfg_raw else default_cfg
    if cfg_path.exists() and not _ask_yes_no(f"{cfg_path.name} 已存在，是否覆盖？", default=False):
        print("未覆盖配置文件。")
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
        base_url = _ask("请输入 base_url（例如 https://api.deepseek.com/v1）：").strip()
        if base_url:
            cfg["base_url"] = base_url
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
    print(f"\n已生成配置文件：{cfg_path}\n")

    key_env = ""
    key_value = ""
    if cfg.get("provider") in {"azure"}:
        key_env = str(cfg.get("azure_api_key_env") or "")
    else:
        key_env = str(cfg.get("api_key_env") or "")
    if key_env:
        if _ask_yes_no(f"是否现在把 {key_env} 写入 env 文件？（推荐）", default=True):
            key_value = _ask_secret(f"请输入 {key_env}（输入时不回显；回车跳过）：")
            if key_value:
                _write_env_kv(env_path, key_env, key_value)
                print(f"已写入：{env_path}\n")
            else:
                print("未写入 env。\n")

    if _ask_yes_no("现在运行健康检查（check-api）？", default=True):
        print("")
        code = _health_check(repo_root, config_path=str(cfg_path))
        if code == 0:
            print("\n配置完成：健康检查通过。")
            return 0
        print("\n健康检查未通过。你可以继续在向导里修正配置或补齐 env。\n")

    while True:
        print("\n下一步选项：")
        print("1) 重新运行健康检查")
        print("2) 重新输入并写入 API Key 到 env")
        print("3) 重新生成配置文件（覆盖）")
        print("0) 退出向导")
        pick = _ask("输入序号：").strip()
        if pick == "0":
            return 0
        if pick == "1":
            _health_check(repo_root, config_path=str(cfg_path))
            continue
        if pick == "2":
            if not key_env:
                print("当前 provider 未识别到 api_key_env。请先选择“重新生成配置文件”。")
                continue
            key_value = _ask_secret(f"请输入 {key_env}（输入时不回显；回车取消）：")
            if key_value:
                _write_env_kv(env_path, key_env, key_value)
                print(f"已写入：{env_path}")
            continue
        if pick == "3":
            return _wizard(repo_root)
        print("无效输入。")


def _init_llm_config(repo_root: Path, *, config_out: str | None) -> int:
    src = repo_root / "llm.yaml.example"
    interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    if config_out:
        dst = Path(config_out)
    elif interactive:
        default_dst = repo_root / "llm.yaml"
        raw = _ask(f"请输入要生成的配置文件路径（回车使用 {default_dst}）：")
        dst = Path(raw) if raw else default_dst
    else:
        raise RuntimeError("缺少配置输出路径：请使用 --config-out 指定要生成的 llm.yaml 路径")
    if not src.exists():
        print("缺少 llm.yaml.example，无法初始化。")
        return 1
    if dst.exists():
        overwrite = _ask(f"{dst.name} 已存在，是否覆盖？(y/N) ").lower() in {"y", "yes", "是", "1", "true"}
        if not overwrite:
            print("未覆盖。")
            return 0
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"已生成：{dst}")
    print(f"下一步：编辑 {dst.name}，并在 .env 中配置对应的 API Key（不要把 Key 写进 yaml）。")
    return 0


def _check_deps() -> int:
    targets = [
        ("PyYAML", "yaml"),
        ("crewai", "crewai"),
        ("langchain-openai", "langchain_openai"),
    ]
    ok = True
    for label, mod in targets:
        try:
            importlib.import_module(mod)
            print(f"已安装：{label}")
        except Exception:
            ok = False
            print(f"缺少：{label}")
    if ok:
        print("依赖检查通过。")
        return 0
    print("依赖不完整：请先 pip install -e . 或按需安装 extra（见 README）。")
    return 1


def _clean(repo_root: Path) -> int:
    from clean_repo import clean

    result = clean(repo_root)
    print(f"清理完成：删除 {result['removed_count']} 项")
    return 0


def _health_check(repo_root: Path, *, config_path: str | None) -> int:
    try:
        import yaml
        from agents.llm_factory import get_llm, load_llm_config, redact_secrets
    except Exception as e:
        print(f"缺少依赖，无法进行健康检查：{e}")
        return 1

    interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    if config_path:
        resolved = Path(config_path)
    elif interactive:
        default_cfg = repo_root / "llm.yaml"
        raw = _ask(f"请输入配置文件路径（回车使用 {default_cfg}）：")
        resolved = Path(raw) if raw else default_cfg
    else:
        raise RuntimeError("缺少配置文件路径：请使用 --config 指定")
    if not resolved.exists():
        print(f"配置文件不存在：{resolved}")
        return 1
    try:
        cfg = load_llm_config(str(resolved), strict=True)
    except Exception as e:
        print(f"配置解析失败：{redact_secrets(str(e))}")
        return 1
    try:
        llm = get_llm(config_path=str(resolved), strict=True, max_tokens=16, temperature=0)
    except Exception as e:
        print(f"模型初始化失败：{redact_secrets(str(e))}")
        return 1
    started = time.time()
    try:
        out = (llm.invoke("Return exactly: OK").content or "").strip()
    except Exception as e:
        payload = {"ok": False, "error": {"code": "invoke_failed", "message": redact_secrets(str(e))}}
        print(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
        return 1
    elapsed_ms = int((time.time() - started) * 1000)
    payload = {
        "ok": bool(out),
        "provider": cfg.provider,
        "model": cfg.model,
        "latency_ms": elapsed_ms,
        "response_preview": (out[:80] if isinstance(out, str) else ""),
    }
    print(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
    return 0 if payload["ok"] else 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="安装本仓库（不提供任何 LLM 预设/向导）")
    p.add_argument("--install", action="store_true", help="以可编辑模式安装本仓库（pip install -e . --no-deps）")
    p.add_argument("--init-config", action="store_true", help="生成 llm.yaml（从 llm.yaml.example 复制）")
    p.add_argument("--config-out", default=None, help="生成配置文件的输出路径（用于 --init-config）")
    p.add_argument("--check-api", action="store_true", help="验证 API 配置可用性（健康检查）")
    p.add_argument("--config", default=None, help="配置文件路径（用于 --check-api；交互模式可省略）")
    p.add_argument("--clean", action="store_true", help="清理项目缓存与构建产物")
    p.add_argument("--check-deps", action="store_true", help="检查依赖是否已安装")
    p.add_argument("--wizard", action="store_true", help="一键配置向导：生成配置 → 写入 env → 健康检查")
    p.add_argument("--once", action="store_true", help="执行完指定动作后退出（默认：在交互终端返回菜单）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parent
    if not (repo_root / "pyproject.toml").exists():
        raise RuntimeError("请在仓库根目录运行 letsgo.py")

    interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    actions = [
        ("--init-config", bool(args.init_config)),
        ("--check-api", bool(args.check_api)),
        ("--clean", bool(args.clean)),
        ("--check-deps", bool(args.check_deps)),
        ("--install", bool(args.install)),
        ("--wizard", bool(args.wizard)),
    ]
    if sum(1 for _, enabled in actions if enabled) > 1:
        enabled_flags = [name for name, enabled in actions if enabled]
        raise RuntimeError("一次只能执行一个动作参数：" + ", ".join(enabled_flags))

    did_action = False
    if args.wizard:
        did_action = True
        if not interactive:
            raise RuntimeError("--wizard 仅支持交互终端")
        code = _wizard(repo_root)
        if args.once:
            return code
        _pause()
    if args.init_config:
        did_action = True
        code = _init_llm_config(repo_root, config_out=args.config_out)
        if (not interactive) or args.once:
            return code
        _pause()
    if args.check_api:
        did_action = True
        code = _health_check(repo_root, config_path=args.config)
        if (not interactive) or args.once:
            return code
        _pause()
    if args.clean:
        did_action = True
        code = _clean(repo_root)
        if (not interactive) or args.once:
            return code
        _pause()
    if args.check_deps:
        did_action = True
        code = _check_deps()
        if (not interactive) or args.once:
            return code
        _pause()
    if args.install:
        did_action = True
        python = sys.executable
        subprocess.check_call([python, "-m", "pip", "install", "-e", str(repo_root), "--no-deps"])
        print("完成：已以可编辑模式安装本仓库（未安装任何 LLM 依赖）。")
        if (not interactive) or args.once:
            return 0
        _pause()
    while True:
        choice = _menu()
        if choice == "1":
            _init_llm_config(repo_root, config_out=None)
            _pause()
            continue
        if choice == "2":
            _health_check(repo_root, config_path=None)
            _pause()
            continue
        if choice == "3":
            _clean(repo_root)
            _pause()
            continue
        if choice == "4":
            _check_deps()
            _pause()
            continue
        if choice == "5":
            python = sys.executable
            subprocess.check_call([python, "-m", "pip", "install", "-e", str(repo_root), "--no-deps"])
            print("完成：已以可编辑模式安装本仓库（未安装任何 LLM 依赖）。")
            _pause()
            continue
        if choice == "6":
            _wizard(repo_root)
            _pause()
            continue
        if choice == "0":
            print("已退出。")
            return 0
        print("无效输入。")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
