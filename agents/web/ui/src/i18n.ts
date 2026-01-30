import type { Lang } from "./types";

type Dict = Record<string, string>;

const ZH: Dict = {
  app_name: "ReqX",
  nav_chat: "对话",
  nav_knowledge: "知识库",
  nav_config: "配置",
  nav_prompt: "提示词",
  lang_label: "语言",

  new_chat: "新对话",
  chat_history: "对话历史",
  rename: "重命名",
  delete: "删除",
  rename_chat_prompt: "输入新标题",

  welcome_title: "你好，我是 ReqX",
  welcome_desc: "我可以帮你分析需求、生成代码或管理项目配置。",
  input_placeholder: "输入指令或问题…",
  send: "发送",
  cmd_help: "帮助",
  cmd_show: "显示",
  cmd_spec: "生成规格",
  cmd_done: "完成",
  cmd_reset: "重置",
  cmd_exit: "退出",

  token: "鉴权 Token",
  token_ph: "可选（不填则匿名只读）",
  token_hint: "用于保护写入类接口；详见 doc/06_auth_and_security.md",
  cfg_path: "配置文件路径（YAML）",
  knowledge_path: "知识库文件路径",
  dry_run: "Dry Run（不落盘）",

  try_spec: "先生成一次规约",
  suggest_1: "我想做一个背单词 App",
  suggest_2: "帮我把需求整理成验收标准与里程碑",

  edit: "编辑",
  cancel: "取消",
  save_and_run: "保存并重新生成",

  copy: "复制",
  copied: "已复制",
  copy_failed: "复制失败",
  code_block: "代码",
  thumb_up: "赞",
  thumb_down: "踩",
  assistant_typing: "正在生成…",
  jump_to_bottom: "回到底部",

  load: "读取",
  save: "保存",
  refresh: "刷新",
  doctor: "Doctor 检查",

  status_ready: "就绪",
  status_thinking: "思考中…",
  status_done: "完成",
  status_error: "错误",

  config_content: "配置内容",
  doctor_output: "Doctor 输出",
  knowledge_snapshot: "快照（只读）",
  prompt_content: "全局提示词",
};

const EN: Dict = {
  app_name: "ReqX",
  nav_chat: "Chat",
  nav_knowledge: "Knowledge",
  nav_config: "Config",
  nav_prompt: "Prompt",
  lang_label: "Language",

  new_chat: "New Chat",
  chat_history: "Chat History",
  rename: "Rename",
  delete: "Delete",
  rename_chat_prompt: "New title",

  welcome_title: "Hi, I'm ReqX",
  welcome_desc: "I can help analyze requirements, generate code, and manage configuration.",
  input_placeholder: "Type a message…",
  send: "Send",
  cmd_help: "Help",
  cmd_show: "Show",
  cmd_spec: "Spec",
  cmd_done: "Done",
  cmd_reset: "Reset",
  cmd_exit: "Exit",

  token: "Auth Token",
  token_ph: "Optional (empty = anonymous read-only)",
  token_hint: "Protects write endpoints; see doc/06_auth_and_security.md",
  cfg_path: "Config Path (YAML)",
  knowledge_path: "Knowledge DB Path",
  dry_run: "Dry Run (no writes)",

  try_spec: "Generate a spec first",
  suggest_1: "I want to build a vocabulary app",
  suggest_2: "Turn my requirements into milestones and acceptance criteria",

  edit: "Edit",
  cancel: "Cancel",
  save_and_run: "Save & Regenerate",

  copy: "Copy",
  copied: "Copied",
  copy_failed: "Copy failed",
  code_block: "Code",
  thumb_up: "Thumb up",
  thumb_down: "Thumb down",
  assistant_typing: "Generating…",
  jump_to_bottom: "Jump to bottom",

  load: "Load",
  save: "Save",
  refresh: "Refresh",
  doctor: "Run Doctor",

  status_ready: "Ready",
  status_thinking: "Thinking…",
  status_done: "Done",
  status_error: "Error",

  config_content: "Content",
  doctor_output: "Doctor Output",
  knowledge_snapshot: "Snapshot (Read Only)",
  prompt_content: "Global Prompt",
};

export function normalizeLang(v: string | null | undefined): Lang {
  const raw = (v ?? "").trim();
  if (!raw) return "zh-CN";
  if (raw === "en") return "en";
  if (raw.toLowerCase().startsWith("zh")) return "zh-CN";
  return "en";
}

export function t(lang: Lang, key: keyof typeof ZH): string {
  const dict = lang === "en" ? EN : ZH;
  return dict[key] ?? String(key);
}
