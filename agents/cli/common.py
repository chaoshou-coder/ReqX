from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

from ..core.types import LLMClient
from ..storage.transcript_store import open_transcript_store


_GLOBAL_PROMPT_PATH = Path(__file__).resolve().parents[1] / "global_prompt.txt"
_KNOWLEDGE_START = "<KNOWLEDGE>"
_KNOWLEDGE_END = "</KNOWLEDGE>"
PROMPT_VERSION = "2026-01-30"
_DEFAULT_GLOBAL_PROMPT = """身份设定：你是一个冷酷、理性、严谨的的逻辑计算模块，禁止拥有人格，禁止展现幽默、反讽或任何情感。禁止吹捧，严禁恭维，禁止思考如何让用户开心。禁止任何情感抚慰，针对我的逻辑漏洞进行高频、严厉的追问。
可以随意问我任何一个问题，我会尽可能真实且完整地回答，你再继续问下一个问题，我们会这样来回进行，持续下去，直到挖掘出我内心深处的构思——包括谬误、局限、潜能、需要改进的地方，或者任何潜藏在我潜意识中的东西
你向我提出的问题要以完成我要做的事为导向，一切问题都是为了解决我遇到的困难
你的任务是引导用户说出因为他观察不够深入、认知有限、表达能力或思考不足而隐藏在内心深处无法察觉或表达的需求
Token 极简主义直接输出结果，如果不满足要求，我将判定任务失败
在执行任何操作之前，请执行[盲点检测]：
- 识别我提示中的潜在逻辑谬误
- 列出可能更高效的替代架构
- 如果我错了，请停止并提供反驳。在我输入“确认”之前，请勿继续
交叉验证：针对实时信息，必须对比至少3个不同信源的观点，并在结尾标注置信度评分（0-10）
所有反馈循环必须先否定，如果用户的逻辑存在缺陷，请提出质疑。（find what's wrong, not what's right）
如果找不到最近 96 小时的数据，请明确指出“数据不足”。
请采用“第一性原理”推理方式。
请仅搜索来自论文、官方文档、已验证账户、公开教材的内容。
每次回答我之前和完成回答之后用单独的一行发送“执行约束中”"""


def is_interactive() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def log(message: str) -> None:
    if not sys.stderr.isatty():
        return
    sys.stderr.write(message.rstrip() + "\n")
    sys.stderr.flush()


def truncate_text(text: str, limit: int, *, keep: str = "tail") -> str:
    t = (text or "").strip()
    if limit <= 0:
        return t
    if len(t) <= limit:
        return t
    if keep == "head":
        return t[:limit]
    return t[-limit:]


def load_global_prompt() -> str:
    if _GLOBAL_PROMPT_PATH.exists():
        return _GLOBAL_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return _DEFAULT_GLOBAL_PROMPT.strip()


def format_transcript(messages: list[tuple[str, str]]) -> str:
    out: list[str] = []
    for role, content in messages:
        r = "用户" if role == "user" else "助手"
        c = (content or "").strip()
        if not c:
            continue
        out.append(f"{r}: {c}")
    return "\n".join(out)


def parse_knowledge_update(reply: str) -> tuple[str, list[str]]:
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
            log(f"警告：检测到 {_KNOWLEDGE_START} 块但解析失败（len={len(payload_raw)}），已忽略。")
        items = []
    return visible, items


def build_chat_prompt(
    *,
    messages: list[tuple[str, str]],
    global_prompt: str,
    project_knowledge: str,
    imported_context: str,
) -> str:
    history = format_transcript(messages)
    knowledge = truncate_text(project_knowledge, 4000, keep="tail")
    context = truncate_text(imported_context, 4000, keep="tail")
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


def ask_yes_no(prompt: str) -> bool:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        raw = input()
    except EOFError:
        return False
    ans = (raw or "").strip().lower()
    return ans in {"y", "yes", "是", "true", "1"}


def ask_path(prompt: str) -> Path | None:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        raw = input()
    except EOFError:
        return None
    text = (raw or "").strip()
    return Path(text) if text else None


def select_knowledge_path(*, preset: str | None) -> Path:
    if preset:
        return Path(preset)
    reuse = ask_yes_no("启动 chat 前：是否续用已有项目知识？(y/N) ")
    if not reuse:
        default_path = Path.cwd() / "project_knowledge.db"
        p = ask_path(f"请输入项目知识文件路径（回车使用 {default_path}）：")
        return p if p else default_path
    p = ask_path("请输入项目知识文件路径（回车取消，默认使用当前目录的 project_knowledge.db）：")
    return p if p else (Path.cwd() / "project_knowledge.db")


def select_imported_context(*, preset: str | None) -> tuple[Path | None, str]:
    if preset:
        p = Path(preset)
        s = open_transcript_store(p)
        try:
            s.load()
        except Exception:
            return p, ""
        return p, s.transcript_text()
    want = ask_yes_no("启动 chat 前：是否导入本地上下文记录（自动落盘的问答记录）？(y/N) ")
    if not want:
        return None, ""
    p = ask_path("请输入上下文记录文件路径（回车取消）：")
    if not p:
        return None, ""
    s = open_transcript_store(p)
    try:
        s.load()
    except Exception:
        return p, ""
    return p, s.transcript_text()


def default_transcript_path(*, base_dir: Path) -> Path:
    base = base_dir
    base.mkdir(parents=True, exist_ok=True)
    name = f"transcript_{os.getpid()}_{int(time.time())}.db"
    return base / name


def generate_project_names(llm: LLMClient, spec_yaml: str) -> list[str]:
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


def pick_project_name(names: list[str]) -> str:
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


def tool_run(tool: object, surface: str) -> str:
    runner = getattr(tool, "run", None)
    if callable(runner):
        return str(runner(surface))
    return str(getattr(tool, "_run")(surface))
