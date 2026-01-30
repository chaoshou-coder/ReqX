from __future__ import annotations

from pathlib import Path
import sys

import yaml

from .common import (
    build_chat_prompt,
    is_interactive,
    load_global_prompt,
    log,
    parse_knowledge_update,
    select_imported_context,
    select_knowledge_path,
    default_transcript_path,
    generate_project_names,
    pick_project_name,
    tool_run,
)
from ..storage.knowledge_store import open_knowledge_store
from ..core.llm_factory import get_llm, load_llm_config, redact_secrets
from ..core.requirement_excavation_skill import RequirementExcavationSkill
from ..storage.transcript_store import open_transcript_store


def chat_main(
    *,
    config_path: str | None,
    knowledge: str | None,
    transcript: str | None,
    transcript_dir: str | None,
    resume_transcript: bool = False,
    import_transcript: str | None,
    dry_run: bool = False,
) -> int:
    interactive = is_interactive()
    knowledge_path_resolved = select_knowledge_path(preset=knowledge) if interactive else (Path(knowledge) if knowledge else None)
    if knowledge_path_resolved is None:
        raise RuntimeError("缺少项目知识文件路径：请通过 --knowledge 指定，或在交互模式下输入。")
    knowledge_store = open_knowledge_store(knowledge_path_resolved)
    knowledge_store.load()

    imported_path, imported_text = select_imported_context(preset=import_transcript) if interactive else (
        Path(import_transcript) if import_transcript else None,
        "",
    )
    if imported_path and not imported_text:
        try:
            s = open_transcript_store(imported_path)
            s.load()
            imported_text = s.transcript_text()
        except Exception:
            imported_text = ""

    transcript_path_resolved: Path | None = None
    if transcript:
        transcript_path_resolved = Path(transcript)
    elif transcript_dir:
        transcript_path_resolved = default_transcript_path(base_dir=Path(transcript_dir))
    elif interactive:
        default_dir = Path.cwd() / "transcripts"
        sys.stdout.write(f"请输入本次上下文记录输出目录（回车使用 {default_dir}）：")
        sys.stdout.flush()
        try:
            raw = input()
        except EOFError:
            raw = ""
        p = Path((raw or "").strip()) if (raw or "").strip() else None
        transcript_path_resolved = default_transcript_path(base_dir=(p if p else default_dir))
    else:
        raise RuntimeError("缺少上下文记录输出路径：请通过 --transcript 或 --transcript-dir 指定，或在交互模式下输入。")
    transcript_store = open_transcript_store(transcript_path_resolved)
    if resume_transcript:
        try:
            transcript_store.load()
        except Exception:
            transcript_store.clear(autosave=False)
    else:
        if transcript_path_resolved.exists():
            transcript_store.clear(autosave=(not dry_run))
        else:
            transcript_store.clear(autosave=False)

    global_prompt = load_global_prompt()

    log("正在加载配置并初始化 LLM...")
    cfg = load_llm_config(config_path, strict=True)
    llm = get_llm(config_path=config_path, strict=True)
    tool = RequirementExcavationSkill(llm=llm, config_path=config_path)

    if resume_transcript:
        sys.stdout.write("已加载历史逐字稿，将继续追加。\n")
    else:
        sys.stdout.write("已开始新会话（逐字稿已清空）。\n")
    sys.stdout.write(f"项目知识文件：{knowledge_path_resolved}\n")
    sys.stdout.write(f"本次上下文记录将自动落盘到：{transcript_path_resolved}\n")
    if imported_path:
        sys.stdout.write(f"已导入上下文记录：{imported_path}\n")
    if dry_run:
        sys.stdout.write("dry-run：本次运行不会写入知识库/逐字稿/配置。\n")
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
                "- /reset: 清空本次页面对话记录\n"
                "- /exit: 退出\n\n"
            )
            continue
        if cmd == "/reset":
            messages.clear()
            transcript_store.clear(autosave=(not dry_run))
            sys.stdout.write("本轮对话记录已清空（含落盘逐字稿）。\n\n")
            continue
        if cmd == "/show":
            sys.stdout.write((knowledge_store.transcript() or "") + "\n\n")
            continue

        if cmd in {"/spec", "/done"}:
            surface = ("项目知识（按时间顺序）：\n" + knowledge_store.transcript()) if knowledge_store.transcript() else ""
            log("正在生成需求 YAML...")
            try:
                spec_yaml = tool_run(tool, surface)
                knowledge_store.latest_spec_yaml = spec_yaml
                if not dry_run:
                    knowledge_store.save()
                sys.stdout.write(spec_yaml + "\n")
            except KeyboardInterrupt:
                log("已中断本次生成。")
                sys.stdout.write("\n")
                continue

            if cmd == "/done":
                names = generate_project_names(llm, knowledge_store.latest_spec_yaml or spec_yaml)
                project_name = pick_project_name(names)
                knowledge_store.project_name = project_name
                if not dry_run:
                    knowledge_store.save()
                sys.stdout.write(f"\n已选择项目名称：{project_name}\n")
                sys.stdout.write("全流程结束。请输入 /exit 退出。\n\n")
                finished = True
            continue

        if finished:
            sys.stdout.write("流程已结束。请输入 /exit 退出。\n\n")
            continue

        transcript_store.append("user", line, autosave=False)
        if not dry_run:
            transcript_store.save()
        messages.append(("user", line))

        prompt = build_chat_prompt(
            messages=messages,
            global_prompt=global_prompt,
            project_knowledge=knowledge_store.transcript(),
            imported_context=imported_text,
        )
        try:
            log(f"正在调用模型（{cfg.provider}/{cfg.model}；可 Ctrl+C 中断）...")
            content = getattr(llm.invoke(prompt), "content", "") or ""
            raw_reply = content if isinstance(content, str) else yaml.safe_dump(content, sort_keys=False, allow_unicode=True)
            raw_reply = raw_reply.strip()
        except KeyboardInterrupt:
            messages.pop()
            transcript_store.turns.pop()
            if not dry_run:
                transcript_store.save()
            log("已中断本次调用。")
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

        visible_reply, knowledge_items = parse_knowledge_update(raw_reply)
        appended = 0
        for item in knowledge_items:
            knowledge_store.append("system", item, autosave=False)
            appended += 1
        if appended and (not dry_run):
            knowledge_store.save()

        sys.stdout.write(f"\n助手> {visible_reply}\n\n")
        transcript_store.append("assistant", visible_reply, autosave=False)
        if not dry_run:
            transcript_store.save()
        messages.append(("assistant", visible_reply))
