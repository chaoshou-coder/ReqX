from __future__ import annotations

from pathlib import Path
import sys

import yaml

from .common import generate_project_names, is_interactive, log, pick_project_name, select_knowledge_path, tool_run
from ..storage.knowledge_store import open_knowledge_store
from ..core.llm_factory import get_llm, load_llm_config
from ..core.requirement_excavation_skill import RequirementExcavationSkill


def spec_main(
    *,
    config_path: str | None,
    knowledge_path: str | None,
    show: bool,
    spec: bool,
    done: bool,
    project_name: str | None,
    project_name_index: int | None,
    auto_pick_name: bool,
    dry_run: bool = False,
) -> int:
    interactive = is_interactive()
    knowledge_file = select_knowledge_path(preset=knowledge_path) if interactive else (Path(knowledge_path) if knowledge_path else None)
    if knowledge_file is None:
        payload = {
            "error": {
                "code": "missing_path",
                "message": "缺少项目知识文件路径",
                "details": {"hint": "请使用 --knowledge 指定，或在交互模式下输入"},
            }
        }
        sys.stdout.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
        return 1

    store = open_knowledge_store(knowledge_file)
    store.load()
    if show:
        sys.stdout.write((store.transcript() or "") + "\n")
        return 0

    log("正在加载配置并初始化 LLM...")
    load_llm_config(config_path, strict=True)
    llm = get_llm(config_path=config_path, strict=True)
    tool = RequirementExcavationSkill(llm=llm, config_path=config_path)

    surface = ("项目知识（按时间顺序）：\n" + store.transcript()) if store.transcript() else ""
    log("正在生成需求 YAML...")
    spec_yaml = tool_run(tool, surface)
    store.latest_spec_yaml = spec_yaml
    if not dry_run:
        store.save()
    sys.stdout.write(spec_yaml + "\n")

    if not done:
        return 0

    names = generate_project_names(llm, store.latest_spec_yaml or spec_yaml)
    if project_name:
        chosen = str(project_name).strip()
    elif isinstance(project_name_index, int) and 1 <= int(project_name_index) <= len(names):
        chosen = names[int(project_name_index) - 1]
    elif auto_pick_name:
        chosen = names[0] if names else "需求挖掘与规约生成"
    elif interactive:
        chosen = pick_project_name(names)
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

    store.project_name = chosen
    if not dry_run:
        store.save()
    sys.stdout.write(f"\n已选择项目名称：{chosen}\n")
    return 0

