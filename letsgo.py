from __future__ import annotations

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="已合并到 reqx；此脚本保留用于兼容旧用法")
    p.add_argument("--install", action="store_true", help="以可编辑模式安装本仓库（等价于：reqx install）")
    p.add_argument("--init-config", action="store_true", help="生成 llm.yaml（等价于：reqx init-config）")
    p.add_argument("--config-out", default=None, help="生成配置文件的输出路径（用于 --init-config）")
    p.add_argument("--check-api", action="store_true", help="验证 API 配置可用性（等价于：reqx check-api）")
    p.add_argument("--config", default=None, help="配置文件路径（用于 --check-api / --web）")
    p.add_argument("--clean", action="store_true", help="清理项目缓存与构建产物（等价于：reqx clean）")
    p.add_argument("--check-deps", action="store_true", help="检查依赖是否已安装（等价于：reqx check-deps）")
    p.add_argument("--wizard", action="store_true", help="一键配置向导（等价于：reqx wizard）")
    p.add_argument("--web", action="store_true", help="启动 WebUI（等价于：reqx web）")
    p.add_argument("--web-bind", default="127.0.0.1", help="WebUI 监听地址（默认 127.0.0.1）")
    p.add_argument("--web-port", type=int, default=8788, help="WebUI 监听端口（默认 8788）")
    return p


def main(argv: list[str] | None = None) -> int:
    from agents.cli import main as reqx_main

    args = _build_parser().parse_args(argv)
    actions = [
        ("--init-config", bool(args.init_config)),
        ("--check-api", bool(args.check_api)),
        ("--clean", bool(args.clean)),
        ("--check-deps", bool(args.check_deps)),
        ("--install", bool(args.install)),
        ("--wizard", bool(args.wizard)),
        ("--web", bool(args.web)),
    ]
    if sum(1 for _, enabled in actions if enabled) > 1:
        enabled_flags = [name for name, enabled in actions if enabled]
        raise RuntimeError("一次只能执行一个动作参数：" + ", ".join(enabled_flags))

    if args.web:
        forwarded = ["web", "--bind", str(args.web_bind), "--port", str(args.web_port)]
        if args.config:
            forwarded += ["--config", str(args.config)]
        return reqx_main(forwarded)
    if args.wizard:
        return reqx_main(["wizard"])
    if args.init_config:
        forwarded = ["init-config"]
        if args.config_out:
            forwarded += ["--config-out", str(args.config_out)]
        return reqx_main(forwarded)
    if args.check_api:
        forwarded = ["check-api"]
        if args.config:
            forwarded += ["--config", str(args.config)]
        return reqx_main(forwarded)
    if args.clean:
        return reqx_main(["clean"])
    if args.check_deps:
        return reqx_main(["check-deps"])
    if args.install:
        return reqx_main(["install"])
    sys.stderr.write("letsgo.py 已合并到 reqx。请使用：reqx --help\n")
    return reqx_main(["--help"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
