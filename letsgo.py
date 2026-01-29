from __future__ import annotations

import argparse
import importlib
from pathlib import Path
import subprocess
import sys
import time


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
    print("0) 退出")
    return _ask("输入序号：")


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
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parent
    if not (repo_root / "pyproject.toml").exists():
        raise RuntimeError("请在仓库根目录运行 letsgo.py")

    if args.init_config:
        return _init_llm_config(repo_root, config_out=args.config_out)
    if args.check_api:
        return _health_check(repo_root, config_path=args.config)
    if args.clean:
        return _clean(repo_root)
    if args.check_deps:
        return _check_deps()
    if args.install:
        python = sys.executable
        subprocess.check_call([python, "-m", "pip", "install", "-e", str(repo_root), "--no-deps"])
        print("完成：已以可编辑模式安装本仓库（未安装任何 LLM 依赖）。")
        return 0
    while True:
        choice = _menu()
        if choice == "1":
            return _init_llm_config(repo_root, config_out=None)
        if choice == "2":
            return _health_check(repo_root, config_path=None)
        if choice == "3":
            return _clean(repo_root)
        if choice == "4":
            return _check_deps()
        if choice == "5":
            python = sys.executable
            subprocess.check_call([python, "-m", "pip", "install", "-e", str(repo_root), "--no-deps"])
            print("完成：已以可编辑模式安装本仓库（未安装任何 LLM 依赖）。")
            return 0
        if choice == "0":
            print("已退出。")
            return 0
        print("无效输入。")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
