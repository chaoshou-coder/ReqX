from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import yaml

from .admin import (
    check_api_main,
    check_deps_main,
    clean_main,
    init_config_main,
    install_main,
    wizard_main,
)
from .chat import chat_main
from .doctor import doctor_main
from .spec import spec_main


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _write_missing_config_yaml() -> None:
    payload = {
        "error": {
            "code": "missing_config",
            "message": "缺少 LLM 配置文件路径",
            "details": {"hint": "请使用 --config 指定配置文件，或设置环境变量 LLM_CONFIG_PATH"},
        }
    }
    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))


def _require_config(config_path: str | None) -> str | None:
    if config_path is None and not os.getenv("LLM_CONFIG_PATH"):
        _write_missing_config_yaml()
        return None
    return config_path


def _build_legacy_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Requirement excavation CLI")
    p.add_argument("--config", default=None, help="配置文件路径（不提供则使用环境变量 LLM_CONFIG_PATH）")
    p.add_argument("--doctor", action="store_true", help="输出当前有效配置与告警（不含密钥）")
    p.add_argument("--knowledge", default=None, help="项目知识文件路径（默认交互式询问）")
    p.add_argument("--transcript", default=None, help="本次对话逐字稿文件路径（优先于 --transcript-dir）")
    p.add_argument("--transcript-dir", default=None, help="本次对话逐字稿输出目录（自动生成文件名）")
    p.add_argument("--resume-transcript", action="store_true", help="继续使用已有逐字稿文件（默认：新会话并清空旧记录）")
    p.add_argument("--import-transcript", default=None, help="导入已有逐字稿文件作为参考上下文（可选）")
    p.add_argument("--dry-run", action="store_true", help="只演练不落盘（不写知识库/逐字稿/配置）")
    p.add_argument("--show", action="store_true", help="输出当前项目知识并退出（等价于 chat 中 /show）")
    p.add_argument("--spec", action="store_true", help="基于项目知识生成需求 YAML 并退出（等价于 chat 中 /spec）")
    p.add_argument("--done", action="store_true", help="生成需求 YAML + 项目名并退出（等价于 chat 中 /done）")
    p.add_argument("--project-name", default=None, help="用于 --done：直接指定项目名称（非交互推荐）")
    p.add_argument("--project-name-index", default=None, type=int, help="用于 --done：从生成的 10 个名称中选择序号（1-10）")
    p.add_argument("--auto-pick-name", action="store_true", help="用于 --done：自动选择第 1 个生成名称")
    p.add_argument("--web", action="store_true", help="启动 WebUI（与 agent 对话/浏览知识/编辑配置）")
    p.add_argument("--web-bind", default="127.0.0.1", help="WebUI 监听地址（默认 127.0.0.1）")
    p.add_argument("--web-port", type=int, default=8788, help="WebUI 监听端口（默认 8788）")
    p.add_argument("--knowledge-api", action="store_true", help="启动本地 Knowledge HTTP API")
    p.add_argument("--knowledge-api-bind", default="127.0.0.1", help="Knowledge API 监听地址（默认 127.0.0.1）")
    p.add_argument("--knowledge-api-port", type=int, default=8787, help="Knowledge API 监听端口（默认 8787）")
    p.add_argument("--knowledge-api-token-env", default="REQX_KNOWLEDGE_API_TOKEN", help="Knowledge API token env 名")
    p.add_argument("--knowledge-api-token", default=None, help="Knowledge API token 值（不推荐）")
    p.add_argument("--knowledge-api-base-dir", default=None, help="Knowledge API base_dir 路径限制（可选）")
    p.add_argument("--knowledge-api-max-body-bytes", type=int, default=2 * 1024 * 1024, help="Knowledge API 请求体最大字节数")
    return p


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Requirement excavation CLI")
    sub = p.add_subparsers(dest="command")

    p_chat = sub.add_parser("chat", help="交互式对话（默认）")
    p_chat.add_argument("--config", default=None, help="配置文件路径（不提供则使用环境变量 LLM_CONFIG_PATH）")
    p_chat.add_argument("--knowledge", default=None, help="项目知识文件路径（默认交互式询问）")
    p_chat.add_argument("--transcript", default=None, help="本次对话逐字稿文件路径（优先于 --transcript-dir）")
    p_chat.add_argument("--transcript-dir", default=None, help="本次对话逐字稿输出目录（自动生成文件名）")
    p_chat.add_argument("--resume-transcript", action="store_true", help="继续使用已有逐字稿文件（默认：新会话并清空旧记录）")
    p_chat.add_argument("--import-transcript", default=None, help="导入已有逐字稿文件作为参考上下文（可选）")
    p_chat.add_argument("--dry-run", action="store_true", help="只演练不落盘（不写知识库/逐字稿/配置）")

    p_show = sub.add_parser("show", help="输出当前项目知识并退出")
    p_show.add_argument("--config", default=None, help="配置文件路径（不提供则使用环境变量 LLM_CONFIG_PATH）")
    p_show.add_argument("--knowledge", default=None, help="项目知识文件路径（默认交互式询问）")

    p_spec = sub.add_parser("spec", help="基于项目知识生成需求 YAML 并退出")
    p_spec.add_argument("--config", default=None, help="配置文件路径（不提供则使用环境变量 LLM_CONFIG_PATH）")
    p_spec.add_argument("--knowledge", default=None, help="项目知识文件路径（默认交互式询问）")
    p_spec.add_argument("--dry-run", action="store_true", help="只演练不落盘（不写知识库/逐字稿/配置）")

    p_done = sub.add_parser("done", help="生成需求 YAML + 项目名并退出")
    p_done.add_argument("--config", default=None, help="配置文件路径（不提供则使用环境变量 LLM_CONFIG_PATH）")
    p_done.add_argument("--knowledge", default=None, help="项目知识文件路径（默认交互式询问）")
    p_done.add_argument("--dry-run", action="store_true", help="只演练不落盘（不写知识库/逐字稿/配置）")
    p_done.add_argument("--project-name", default=None, help="直接指定项目名称（非交互推荐）")
    p_done.add_argument("--project-name-index", default=None, type=int, help="从生成的 10 个名称中选择序号（1-10）")
    p_done.add_argument("--auto-pick-name", action="store_true", help="自动选择第 1 个生成名称")

    p_doctor = sub.add_parser("doctor", help="输出当前有效配置与告警（不含密钥）")
    p_doctor.add_argument("--config", default=None, help="配置文件路径（不提供则使用环境变量 LLM_CONFIG_PATH）")

    p_web = sub.add_parser("web", help="启动 WebUI（与 agent 对话/浏览知识/编辑配置）")
    p_web.add_argument("--config", default=None, help="配置文件路径（不提供则使用环境变量 LLM_CONFIG_PATH）")
    p_web.add_argument("--bind", default="127.0.0.1", help="WebUI 监听地址（默认 127.0.0.1）")
    p_web.add_argument("--port", type=int, default=8788, help="WebUI 监听端口（默认 8788）")
    p_web.add_argument("--dry-run", action="store_true", help="只演练不落盘（WebUI 禁止写入）")

    p_kapi = sub.add_parser("knowledge-api", help="启动本地 Knowledge HTTP API")
    p_kapi.add_argument("--bind", default="127.0.0.1")
    p_kapi.add_argument("--port", type=int, default=8787)
    p_kapi.add_argument("--knowledge", default=None)
    p_kapi.add_argument("--base-dir", default=None)
    p_kapi.add_argument("--token-env", default="REQX_KNOWLEDGE_API_TOKEN")
    p_kapi.add_argument("--token", default=None)
    p_kapi.add_argument("--max-body-bytes", type=int, default=2 * 1024 * 1024)

    p_init = sub.add_parser("init-config", help="从 llm.yaml.example 生成 llm.yaml")
    p_init.add_argument("--config-out", default=None, help="配置文件输出路径（默认交互式询问）")

    p_check = sub.add_parser("check-api", help="验证 API 配置是否可用（健康检查）")
    p_check.add_argument("--config", default=None, help="配置文件路径（默认交互式询问）")

    sub.add_parser("check-deps", help="检查依赖是否已安装")
    sub.add_parser("clean", help="清理项目缓存与构建产物")

    p_install = sub.add_parser("install", help="以可编辑模式安装本仓库")
    p_install.add_argument("--with-deps", action="store_true", help="安装时包含依赖（默认：--no-deps）")

    sub.add_parser("wizard", help="一键配置向导：生成配置 → 写入 env → 健康检查")

    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    if not argv or argv[0].startswith("-"):
        args = _build_legacy_parser().parse_args(argv)
        if args.knowledge_api:
            from ..api.knowledge_http_api import KnowledgeHttpApi

            api = KnowledgeHttpApi(
                bind=args.knowledge_api_bind,
                port=args.knowledge_api_port,
                base_dir=args.knowledge_api_base_dir,
                default_knowledge_path=args.knowledge,
                token_env=args.knowledge_api_token_env,
                token_value=args.knowledge_api_token,
                max_body_bytes=args.knowledge_api_max_body_bytes,
            )
            sys.stderr.write(f"Knowledge API listening on http://{args.knowledge_api_bind}:{args.knowledge_api_port}\n")
            api.serve_forever()
            return 0
        if args.web:
            if _require_config(args.config) is None:
                return 1
            from ..web.server import serve_webui

            serve_webui(repo_root=_repo_root(), bind=str(args.web_bind), port=int(args.web_port), dry_run=bool(args.dry_run))
            return 0
        if _require_config(args.config) is None:
            return 1
        if args.doctor:
            return doctor_main(config_path=args.config)
        if args.show or args.spec or args.done:
            return spec_main(
                config_path=args.config,
                knowledge_path=args.knowledge,
                show=bool(args.show),
                spec=bool(args.spec),
                done=bool(args.done),
                project_name=args.project_name,
                project_name_index=args.project_name_index,
                auto_pick_name=bool(args.auto_pick_name),
                dry_run=bool(args.dry_run),
            )
        return chat_main(
            config_path=args.config,
            knowledge=args.knowledge,
            transcript=args.transcript,
            transcript_dir=args.transcript_dir,
            resume_transcript=bool(args.resume_transcript),
            import_transcript=args.import_transcript,
            dry_run=bool(args.dry_run),
        )

    args = _build_parser().parse_args(argv)
    cmd = args.command or "chat"

    if cmd == "chat":
        if _require_config(args.config) is None:
            return 1
        return chat_main(
            config_path=args.config,
            knowledge=args.knowledge,
            transcript=args.transcript,
            transcript_dir=args.transcript_dir,
            resume_transcript=bool(args.resume_transcript),
            import_transcript=args.import_transcript,
            dry_run=bool(args.dry_run),
        )
    if cmd == "show":
        if _require_config(args.config) is None:
            return 1
        return spec_main(
            config_path=args.config,
            knowledge_path=args.knowledge,
            show=True,
            spec=False,
            done=False,
            project_name=None,
            project_name_index=None,
            auto_pick_name=False,
            dry_run=False,
        )
    if cmd == "spec":
        if _require_config(args.config) is None:
            return 1
        return spec_main(
            config_path=args.config,
            knowledge_path=args.knowledge,
            show=False,
            spec=True,
            done=False,
            project_name=None,
            project_name_index=None,
            auto_pick_name=False,
            dry_run=bool(args.dry_run),
        )
    if cmd == "done":
        if _require_config(args.config) is None:
            return 1
        return spec_main(
            config_path=args.config,
            knowledge_path=args.knowledge,
            show=False,
            spec=False,
            done=True,
            project_name=args.project_name,
            project_name_index=args.project_name_index,
            auto_pick_name=bool(args.auto_pick_name),
            dry_run=bool(args.dry_run),
        )
    if cmd == "doctor":
        if _require_config(args.config) is None:
            return 1
        return doctor_main(config_path=args.config)
    if cmd == "web":
        if _require_config(args.config) is None:
            return 1
        from ..web.server import serve_webui

        serve_webui(repo_root=_repo_root(), bind=str(args.bind), port=int(args.port), dry_run=bool(args.dry_run))
        return 0
    if cmd == "knowledge-api":
        from ..api.knowledge_http_api import KnowledgeHttpApi

        api = KnowledgeHttpApi(
            bind=args.bind,
            port=args.port,
            base_dir=args.base_dir,
            default_knowledge_path=args.knowledge,
            token_env=args.token_env,
            token_value=args.token,
            max_body_bytes=args.max_body_bytes,
        )
        sys.stderr.write(f"Knowledge API listening on http://{args.bind}:{args.port}\n")
        api.serve_forever()
        return 0
    if cmd == "init-config":
        return init_config_main(config_out=args.config_out)
    if cmd == "check-api":
        return check_api_main(config_path=args.config)
    if cmd == "check-deps":
        return check_deps_main()
    if cmd == "clean":
        return clean_main()
    if cmd == "install":
        return install_main(no_deps=not bool(args.with_deps))
    if cmd == "wizard":
        return wizard_main()

    raise RuntimeError(f"未知命令：{cmd}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
