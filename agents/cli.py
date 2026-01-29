from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

import yaml

from .knowledge_store import KnowledgeStore
from .llm_factory import _load_env_file, get_llm, load_llm_config, redact_secrets
from .requirement_excavation_skill import RequirementExcavationSkill
from .transcript_store import TranscriptStore


_GLOBAL_PROMPT_PATH = Path(__file__).with_name("global_prompt.txt")
_KNOWLEDGE_START = "<KNOWLEDGE>"
_KNOWLEDGE_END = "</KNOWLEDGE>"


def _log(message: str) -> None:
    if not sys.stderr.isatty():
        return
    sys.stderr.write(message.rstrip() + "\n")
    sys.stderr.flush()


def _truncate_text(text: str, limit: int) -> str:
    t = (text or "").strip()
    if limit <= 0:
        return t
    if len(t) <= limit:
        return t
    return t[-limit:]


def _load_global_prompt() -> str:
    if not _GLOBAL_PROMPT_PATH.exists():
        raise RuntimeError(f"缺少全局 prompt 文件：{_GLOBAL_PROMPT_PATH}")
    return _GLOBAL_PROMPT_PATH.read_text(encoding="utf-8").strip()


def _format_transcript(messages: list[tuple[str, str]]) -> str:
    out: list[str] = []
    for role, content in messages:
        r = "用户" if role == "user" else "助手"
        c = (content or "").strip()
        if not c:
            continue
        out.append(f"{r}: {c}")
    return "\n".join(out)


def _parse_knowledge_update(reply: str) -> tuple[str, list[str]]:
    text = (reply or "").strip()
    if not text:
        return "", []
    if _KNOWLEDGE_START not in text:
        return text, []
    start = text.rfind(_KNOWLEDGE_START)
    end = text.find(_KNOWLEDGE_END, start + len(_KNOWLEDGE_START))
    if end < 0:
        return text, []
    payload_raw = text[start + len(_KNOWLEDGE_START) : end].strip()
    visible = (text[:start].rstrip() + "\n" + text[end + len(_KNOWLEDGE_END) :].lstrip()).strip()
    items: list[str] = []
    try:
        data = json.loads(payload_raw)
        if isinstance(data, list):
            items = [x.strip() for x in data if isinstance(x, str) and x.strip()]
        elif isinstance(data, dict):
            append = data.get("append", [])
            if isinstance(append, list):
                items = [x.strip() for x in append if isinstance(x, str) and x.strip()]
    except Exception:
        if payload_raw:
            _log(f"警告：检测到 {_KNOWLEDGE_START} 块但解析失败（len={len(payload_raw)}），已忽略。")
        items = []
    return visible, items


def _build_chat_prompt(
    *,
    messages: list[tuple[str, str]],
    global_prompt: str,
    project_knowledge: str,
    imported_context: str,
) -> str:
    history = _format_transcript(messages)
    knowledge = _truncate_text(project_knowledge, 4000)
    context = _truncate_text(imported_context, 4000)
    return (
        f"{global_prompt}\n"
        "你现在处于 chat 模式：你的目标是通过多轮问答澄清需求。\n"
        "规则：\n"
        "- 不要输出 JSON/YAML/Markdown，不要输出任何规格文档。\n"
        "- 是否结束问答只能由用户输入 /spec 或 /done 判断；禁止你用自然语言宣告结束。\n"
        "- 程序会自动把每一次问答原文落盘为“上下文记录”（节省 token 的控制逻辑在程序侧）。\n"
        "- “项目知识”用于 /spec 和 /done 生成 YAML：是否写入、写入什么由你决定。\n"
        "- 当你认为某条信息已经稳定、对后续生成很关键时，在回复末尾额外输出一行：\n"
        f"  {_KNOWLEDGE_START}{{\"append\":[\"...\", \"...\"]}}{_KNOWLEDGE_END}\n"
        "  该行仅供程序解析并写入项目知识文件，不会展示给用户；不要写入任何密钥或敏感信息。\n"
        "历史上下文（可选，来自本地导入的问答记录，供你参考但不要复述全文）：\n"
        f"{context}\n"
        "已有项目知识（可能来自历史会话，供你引用但不要复述全文）：\n"
        f"{knowledge}\n"
        "本轮对话记录：\n"
        f"{history}\n"
        "请输出你的下一句话（只输出对用户可见内容）："
    )


def _ask_yes_no(prompt: str) -> bool:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        raw = input()
    except EOFError:
        return False
    ans = (raw or "").strip().lower()
    return ans in {"y", "yes", "是", "true", "1"}


def _ask_path(prompt: str) -> Path | None:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        raw = input()
    except EOFError:
        return None
    text = (raw or "").strip()
    return Path(text) if text else None


def _select_knowledge_path(*, preset: str | None) -> Path:
    if preset:
        return Path(preset)
    reuse = _ask_yes_no("启动 chat 前：是否续用已有项目知识？(y/N) ")
    if not reuse:
        default_path = Path.cwd() / "project_knowledge.yaml"
        p = _ask_path(f"请输入项目知识文件路径（回车使用 {default_path}）：")
        return p if p else default_path
    p = _ask_path("请输入项目知识文件路径（回车取消，默认使用当前目录的 project_knowledge.yaml）：")
    return p if p else (Path.cwd() / "project_knowledge.yaml")


def _select_imported_context(*, preset: str | None) -> tuple[Path | None, str]:
    if preset:
        p = Path(preset)
        s = TranscriptStore(p)
        try:
            s.load()
        except Exception:
            return p, ""
        return p, s.transcript_text()
    want = _ask_yes_no("启动 chat 前：是否导入本地上下文记录（自动落盘的问答记录）？(y/N) ")
    if not want:
        return None, ""
    p = _ask_path("请输入上下文记录文件路径（回车取消）：")
    if not p:
        return None, ""
    s = TranscriptStore(p)
    try:
        s.load()
    except Exception:
        return p, ""
    return p, s.transcript_text()


def _default_transcript_path(*, base_dir: Path) -> Path:
    base = base_dir
    base.mkdir(parents=True, exist_ok=True)
    name = f"transcript_{os.getpid()}_{int(time.time())}.yaml"
    return base / name


def _generate_project_names(llm: object, spec_yaml: str) -> list[str]:
    prompt = (
        "你是命名引擎。\n"
        "基于下面的项目规约 YAML，为该项目生成 10 个中文项目名称。\n"
        "要求：名称不超过 14 个汉字；尽量具体；避免生僻字；不要加引号。\n"
        "只返回 minified JSON 数组，包含 10 个字符串。不要输出 Markdown。\n\n"
        f"YAML:\n{spec_yaml}\n"
    )
    try:
        raw = getattr(llm.invoke(prompt), "content", "")
    except Exception:
        raw = ""
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            names = [x.strip() for x in data if isinstance(x, str) and x.strip()]
            if len(names) >= 10:
                return names[:10]
    except Exception:
        pass
    base = "需求挖掘与规约生成"
    return [
        f"{base}引擎",
        f"{base}助手",
        "需求澄清工作台",
        "需求规约生成器",
        "多轮澄清到YAML",
        "项目规约编译器",
        "产品需求剖析器",
        "工程规约提炼器",
        "需求对话挖掘器",
        "规约落地中枢",
    ]


def _pick_project_name(names: list[str]) -> str:
    sys.stdout.write("\n可选项目名称（输入序号或直接输入名称）：\n")
    for i, n in enumerate(names, 1):
        sys.stdout.write(f"{i}. {n}\n")
    sys.stdout.write("\n你> ")
    sys.stdout.flush()
    try:
        raw = input()
    except EOFError:
        raw = ""
    choice = (raw or "").strip()
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(names):
            return names[idx - 1]
    return choice if choice else names[0]


def _chat(
    *,
    config_path: str | None,
    knowledge: str | None,
    transcript: str | None,
    transcript_dir: str | None,
    import_transcript: str | None,
) -> int:
    interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    knowledge_path_resolved = _select_knowledge_path(preset=knowledge) if interactive else (Path(knowledge) if knowledge else None)
    if knowledge_path_resolved is None:
        raise RuntimeError("缺少项目知识文件路径：请通过 --knowledge 指定，或在交互模式下输入。")
    knowledge = KnowledgeStore(knowledge_path_resolved)
    knowledge.load()

    imported_path, imported_text = _select_imported_context(preset=import_transcript) if interactive else (Path(import_transcript) if import_transcript else None, "")
    if imported_path and not imported_text:
        try:
            s = TranscriptStore(imported_path)
            s.load()
            imported_text = s.transcript_text()
        except Exception:
            imported_text = ""

    transcript_path_resolved: Path | None = None
    if transcript:
        transcript_path_resolved = Path(transcript)
    elif transcript_dir:
        transcript_path_resolved = _default_transcript_path(base_dir=Path(transcript_dir))
    elif interactive:
        default_dir = Path.cwd() / "transcripts"
        p = _ask_path(f"请输入本次上下文记录输出目录（回车使用 {default_dir}）：")
        transcript_path_resolved = _default_transcript_path(base_dir=(p if p else default_dir))
    else:
        raise RuntimeError("缺少上下文记录输出路径：请通过 --transcript 或 --transcript-dir 指定，或在交互模式下输入。")
    transcript = TranscriptStore(transcript_path_resolved)

    global_prompt = _load_global_prompt()

    _log("正在加载配置并初始化 LLM...")
    cfg = load_llm_config(config_path, strict=True)
    llm = get_llm(config_path=config_path, strict=True)
    tool = RequirementExcavationSkill(llm=llm, config_path=config_path)

    sys.stdout.write("本轮对话记录已清空。\n")
    sys.stdout.write(f"项目知识文件：{knowledge_path_resolved}\n")
    sys.stdout.write(f"本次上下文记录将自动落盘到：{transcript_path_resolved}\n")
    if imported_path:
        sys.stdout.write(f"已导入上下文记录：{imported_path}\n")
    sys.stdout.write("快捷命令：/help /done /spec /show /reset /exit\n\n")

    messages: list[tuple[str, str]] = []
    finished = False
    while True:
        try:
            raw = input("你> ")
        except EOFError:
            raw = "/done"
        line = (raw or "").strip()
        if not line:
            continue

        cmd = line.lower()
        if cmd in {"/exit", "/quit"}:
            return 0
        if cmd in {"/help", "/h"}:
            sys.stdout.write(
                "命令说明：\n"
                "- /spec: 基于项目知识生成需求 YAML（不结束）\n"
                "- /done: 生成需求 YAML → 生成 10 个项目名 → 选择后结束流程\n"
                "- /show: 显示当前项目知识\n"
                "- /reset: 清空本轮对话记录\n"
                "- /exit: 退出\n\n"
            )
            continue
        if cmd == "/reset":
            messages.clear()
            sys.stdout.write("本轮对话记录已清空。\n\n")
            continue
        if cmd == "/show":
            sys.stdout.write((knowledge.transcript() or "") + "\n\n")
            continue

        if cmd in {"/spec", "/done"}:
            surface = ("项目知识（按时间顺序）：\n" + knowledge.transcript()) if knowledge.transcript() else ""
            _log("正在生成需求 YAML...")
            try:
                spec_yaml = tool._run(surface)
                knowledge.latest_spec_yaml = spec_yaml
                knowledge.save()
                sys.stdout.write(spec_yaml + "\n")
            except KeyboardInterrupt:
                _log("已中断本次生成。")
                sys.stdout.write("\n")
                continue

            if cmd == "/done":
                names = _generate_project_names(llm, knowledge.latest_spec_yaml or spec_yaml)
                project_name = _pick_project_name(names)
                knowledge.project_name = project_name
                knowledge.save()
                sys.stdout.write(f"\n已选择项目名称：{project_name}\n")
                sys.stdout.write("全流程结束。请输入 /exit 退出。\n\n")
                finished = True
            continue

        if finished:
            sys.stdout.write("流程已结束。请输入 /exit 退出。\n\n")
            continue

        transcript.append("user", line)
        messages.append(("user", line))

        prompt = _build_chat_prompt(
            messages=messages,
            global_prompt=global_prompt,
            project_knowledge=knowledge.transcript(),
            imported_context=imported_text,
        )
        try:
            _log(f"正在调用模型（{cfg.provider}/{cfg.model}；可 Ctrl+C 中断）...")
            raw_reply = (llm.invoke(prompt).content or "").strip()
        except KeyboardInterrupt:
            messages.pop()
            transcript.turns.pop()
            transcript.save()
            _log("已中断本次调用。")
            sys.stdout.write("\n")
            continue
        except Exception as e:
            payload = {
                "error": {
                    "code": "chat_failed",
                    "details": {
                        "exception": redact_secrets(str(e)),
                        "model": cfg.model,
                        "provider": cfg.provider,
                        "base_url": cfg.base_url,
                    },
                }
            }
            sys.stdout.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True) + "\n")
            continue

        visible_reply, knowledge_items = _parse_knowledge_update(raw_reply)
        for item in knowledge_items:
            knowledge.append("system", item)

        sys.stdout.write(f"\n助手> {visible_reply}\n\n")
        transcript.append("assistant", visible_reply)
        messages.append(("assistant", visible_reply))


def _doctor(*, config_path: str | None) -> int:
    cfg = load_llm_config(config_path, strict=True)
    provider = cfg.provider.lower().strip()
    resolved_config_path = Path(config_path) if config_path is not None else Path(os.environ["LLM_CONFIG_PATH"])
    config_dir = resolved_config_path.parent if resolved_config_path.parent else Path.cwd()
    forced_env = os.getenv("LLM_ENV_PATH")
    env_path: Path | None = None
    if forced_env:
        env_path = Path(forced_env)
    elif cfg.env_file:
        env_file_path = Path(cfg.env_file)
        env_path = env_file_path if env_file_path.is_absolute() else (config_dir / cfg.env_file)

    if provider in {"azure", "azure_openai"}:
        key_env = cfg.azure_api_key_env or ""
    else:
        key_env = cfg.api_key_env

    allowed_keys: set[str] = {cfg.api_key_env}
    if cfg.azure_api_key_env:
        allowed_keys.add(cfg.azure_api_key_env)
    _load_env_file(cfg.env_file, allowed_keys=allowed_keys, config_dir=config_dir)

    payload = {
        "schema_version": 1,
        "config_path": str(resolved_config_path),
        "provider": cfg.provider,
        "model": cfg.model,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "input_char_limit": cfg.input_char_limit,
        "output_char_limit": cfg.output_char_limit,
        "env_file": cfg.env_file,
        "env_path_effective": str(env_path) if env_path else None,
        "api_key_env": key_env,
        "api_key_present_in_process_env": bool(os.getenv(key_env)) if key_env else False,
        "base_url": cfg.base_url,
        "azure_endpoint": cfg.azure_endpoint,
        "azure_deployment": cfg.azure_deployment,
        "azure_api_version": cfg.azure_api_version,
        "warnings": [],
    }

    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Requirement excavation CLI")
    p.add_argument("--config", default=None, help="配置文件路径（不提供则使用环境变量 LLM_CONFIG_PATH）")
    p.add_argument("--doctor", action="store_true", help="输出当前有效配置与告警（不含密钥）")
    p.add_argument("--knowledge", default=None, help="项目知识文件路径（默认交互式询问）")
    p.add_argument("--transcript", default=None, help="本次对话逐字稿文件路径（优先于 --transcript-dir）")
    p.add_argument("--transcript-dir", default=None, help="本次对话逐字稿输出目录（自动生成文件名）")
    p.add_argument("--import-transcript", default=None, help="导入已有逐字稿文件作为参考上下文（可选）")
    p.add_argument("--show", action="store_true", help="输出当前项目知识并退出（等价于 chat 中 /show）")
    p.add_argument("--spec", action="store_true", help="基于项目知识生成需求 YAML 并退出（等价于 chat 中 /spec）")
    p.add_argument("--done", action="store_true", help="生成需求 YAML + 项目名并退出（等价于 chat 中 /done）")
    p.add_argument("--project-name", default=None, help="用于 --done：直接指定项目名称（非交互推荐）")
    p.add_argument("--project-name-index", default=None, type=int, help="用于 --done：从生成的 10 个名称中选择序号（1-10）")
    p.add_argument("--auto-pick-name", action="store_true", help="用于 --done：自动选择第 1 个生成名称")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    if args.config is None and not os.getenv("LLM_CONFIG_PATH"):
        payload = {
            "error": {
                "code": "missing_config",
                "message": "缺少 LLM 配置文件路径",
                "details": {"hint": "请使用 --config 指定配置文件，或设置环境变量 LLM_CONFIG_PATH"},
            }
        }
        sys.stdout.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
        return 1
    if args.doctor:
        return _doctor(config_path=args.config)

    if args.show or args.spec or args.done:
        knowledge_path = _select_knowledge_path(preset=args.knowledge) if interactive else (Path(args.knowledge) if args.knowledge else None)
        if knowledge_path is None:
            payload = {
                "error": {
                    "code": "missing_path",
                    "message": "缺少项目知识文件路径",
                    "details": {"hint": "请使用 --knowledge 指定，或在交互模式下输入"},
                }
            }
            sys.stdout.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
            return 1
        store = KnowledgeStore(knowledge_path)
        store.load()
        if args.show:
            sys.stdout.write((store.transcript() or "") + "\n")
            return 0

        _log("正在加载配置并初始化 LLM...")
        load_llm_config(args.config, strict=True)
        llm = get_llm(config_path=args.config, strict=True)
        tool = RequirementExcavationSkill(llm=llm, config_path=args.config)

        surface = ("项目知识（按时间顺序）：\n" + store.transcript()) if store.transcript() else ""
        _log("正在生成需求 YAML...")
        spec_yaml = tool._run(surface)
        store.latest_spec_yaml = spec_yaml
        store.save()
        sys.stdout.write(spec_yaml + "\n")

        if not args.done:
            return 0

        names = _generate_project_names(llm, store.latest_spec_yaml or spec_yaml)
        if args.project_name:
            project_name = str(args.project_name).strip()
        elif isinstance(args.project_name_index, int) and 1 <= int(args.project_name_index) <= len(names):
            project_name = names[int(args.project_name_index) - 1]
        elif args.auto_pick_name:
            project_name = names[0] if names else "需求挖掘与规约生成"
        elif interactive:
            project_name = _pick_project_name(names)
        else:
            payload = {
                "error": {
                    "code": "missing_project_name",
                    "message": "非交互模式下 --done 需要指定项目名称",
                    "details": {"hint": "请使用 --project-name 或 --project-name-index 或 --auto-pick-name"},
                }
            }
            sys.stdout.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
            return 1

        store.project_name = project_name
        store.save()
        sys.stdout.write(f"\n已选择项目名称：{project_name}\n")
        return 0

    return _chat(
        config_path=args.config,
        knowledge=args.knowledge,
        transcript=args.transcript,
        transcript_dir=args.transcript_dir,
        import_transcript=args.import_transcript,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
