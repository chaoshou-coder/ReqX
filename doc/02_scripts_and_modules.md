# 各个脚本与模块详解 (Scripts and Modules)

本文档将深入剖析 **ReqX** 项目中的每一个脚本和核心模块，帮助你理解它们的功能、实现原理以及相互之间的协作关系。

## 1. 根目录工具脚本

这些脚本位于项目根目录下，是你管理和运行项目的入口。

### 1.1 开发者子命令（reqx）

项目的初始化与维护动作已收敛到 `reqx` 子命令：
- `reqx install`：以可编辑模式安装项目
- `reqx init-config`：生成配置文件
- `reqx check-api --config llm.yaml`：健康检查
- `reqx clean`：清理缓存与构建产物
- `reqx check-deps`：依赖检查
- `reqx web --config llm.yaml`：启动 WebUI

### 1.2 `run_agent.py` —— 自动化 Demo
[代码引用: run_agent.py](../run_agent.py)

这是一个演示脚本，展示了如何使用 **CrewAI** 框架来调度我们的需求挖掘技能。

*   **工作流程**：
    1.  **加载配置**：利用 `load_llm_config` 读取配置。
    2.  **连通性测试**：在正式运行前，先做一个快速的 HTTP 请求测试，确保网络通畅。这一步增加了脚本的健壮性。
    3.  **组建团队**：创建一个 CrewAI 的 `Agent`（角色是“需求架构师”），并赋予它 `RequirementExcavationSkill` 工具。
    4.  **下达任务**：给 Agent 一个预设的模糊需求（例如："Users say onboarding is confusing"）。
    5.  **执行**：Agent 会自动思考、调用工具，最终输出一份结构化的需求文档。

*   **学习价值**：如果你想把这个需求挖掘功能集成到你自己的自动化流水线中，这个脚本就是最好的参考范例。

### 1.3 `clean_repo.py` —— 清道夫
[代码引用: clean_repo.py](../clean_repo.py)

这是一个纯粹的清理工具。它会递归遍历整个项目目录，删除：
*   Python 缓存文件（`__pycache__`, `*.pyc`）
*   测试缓存（`.pytest_cache`）
*   构建产物（`build`, `dist`, `*.egg-info`）

*   **安全机制**：它会自动跳过 `.venv` 和 `venv` 目录，防止误删你的虚拟环境。

---

## 2. 核心代码库 (`agents/` 目录)

这里是项目的核心逻辑所在。当前已按职责拆分为多个子包。

推荐按以下结构理解：

- `agents/cli/`：命令行入口与各子命令实现
- `agents/web/`：WebUI 服务端
- `agents/api/`：HTTP API（knowledge-api）与脚本入口适配
- `agents/service/`：面向业务的服务层（路径限制、读写封装）
- `agents/storage/`：持久化（SQLite/YAML）
- `agents/core/`：LLM 工厂与核心技能

### 2.1 `agents/cli.py` —— 交互主脑
[代码引用: agents/cli.py](../agents/cli.py)

这是命令行入口文件（兼容层），实际实现位于 `agents/cli/main.py`。

*   **核心循环 (`_chat` 函数)**：
    *   它维护了一个 `while True` 循环，不断接收用户输入。
    *   **命令拦截**：在发送给 LLM 之前，它会先检查用户是否输入了 `/spec`, `/done`, `/reset` 等指令。如果是指令，就直接在本地处理，不消耗 Token。
    *   **上下文构建**：每次对话时，它会将“全局 Prompt”、“项目知识”和“最近的对话记录”拼装在一起，发送给 LLM。这样 LLM 就能记住之前的设定和结论。
    *   **知识解析 (`_parse_knowledge_update`)**：这是最精彩的部分。LLM 返回的内容中可能包含 `<KNOWLEDGE>...</KNOWLEDGE>` 块。这个函数会把这些块“抠”出来存入数据库，然后把剩下的纯文本展示给用户。

### 2.2 `agents/core/requirement_excavation_skill.py` —— 核心技能
[代码引用: agents/core/requirement_excavation_skill.py](../agents/core/requirement_excavation_skill.py)

这个文件定义了一个 `Tool`（工具），专门负责把模糊的一段话变成结构化的 YAML。

*   **主要职责**：
    1.  **Prompt 构造**：它包含了一个非常详细的 Prompt，告诉 LLM：“你是一个需求挖掘引擎，你要输出 JSON，字段必须包含 root_goal, proposed_solutions...”。
    2.  **JSON 校验与清洗 (`_normalize_and_validate`)**：LLM 输出的 JSON 经常会有小毛病（比如字段缺了，类型不对）。这个函数会严格检查每一个字段。如果格式不对，它会生成一个包含错误信息的 YAML，而不是直接崩溃。
    3.  **YAML 转换**：最终把合法的 JSON 转换成人类易读的 YAML 格式返回。

### 2.3 `agents/core/llm_factory.py` —— 模型工厂
[代码引用: agents/core/llm_factory.py](../agents/core/llm_factory.py)

这个模块负责解决“如何连接不同的大模型”这个问题。

*   **支持的 Provider**：
    *   `openai`: 官方接口。
    *   `azure`: 微软 Azure OpenAI。
    *   `anthropic`: Claude 系列。
    *   `google`: Gemini 系列。
    *   `openai_compatible`: 任何兼容 OpenAI 协议的模型（如 DeepSeek, Moonshot, LocalLLM）。
*   **高级配置与环境变量**：
    除了 `llm.yaml` 中的基础配置，系统还支持以下隐藏环境变量，用于高级控制：
    *   `LLM_HTTP_TIMEOUT_S` (默认 180s): 控制所有 LLM 请求的 HTTP 超时时间，网络较差时可调大。
    *   `LLM_CONFIG_PATH`: 强制指定 `llm.yaml` 的路径，适合容器化部署。
    *   `LLM_ENV_PATH`: 强制指定 `.env` 文件路径。
    *   `REQX_DEBUG_RAW_OUTPUT`: 设置为 `1` 或 `true` 时，若解析失败，会在报错信息中包含原始的 Raw Output（默认脱敏），用于调试 Prompt。
*   **安全脱敏 (`redact_secrets`)**：
    *   这是一个非常重要的安全功能。它利用正则表达式，自动扫描所有日志和错误信息。如果发现类似 `sk-xxxx` 的密钥，会自动替换为 `<redacted>`。这防止了你在截图或分享日志时意外泄露 Key。

### 2.4 Web 服务与 API (`agents/web/` & `agents/api/`)
[代码引用: agents/web/server.py](../agents/web/server.py)
[代码引用: agents/api/knowledge_http_api.py](../agents/api/knowledge_http_api.py)

除了 CLI，项目还内置了 HTTP 服务能力，用于与其他系统集成。

*   **API 端点 (Endpoints)**:
    *   `POST /v1/chat/send`: 发送对话消息。支持 `imported_context`（注入外部上下文）和 `dry_run`（仅演练不落盘）。
    *   `POST /v1/config/doctor`: 运行配置诊断，返回 JSON 格式的脱敏配置详情。
    *   `POST /v1/knowledge/append`: 允许外部工具直接向知识库追加条目（支持 `role` 和 `items` 列表）。
    *   `POST /v1/knowledge/set_project_name`: 直接修改项目元数据。
*   **鉴权机制**:
    *   **Web UI Token**: 使用环境变量 `REQX_WEB_TOKEN` 进行 Bearer Token 认证。
    *   **Knowledge API Token**: 使用环境变量 `REQX_KNOWLEDGE_API_TOKEN` 进行保护。
    *   *注：写入类接口强制鉴权，读取类接口在未配置 Token 时允许匿名访问（但会对敏感字段脱敏）。*

### 2.5 数据存储模块
[代码引用: agents/storage/knowledge_store.py](../agents/storage/knowledge_store.py)
[代码引用: agents/storage/transcript_store.py](../agents/storage/transcript_store.py)

这两个模块负责数据的持久化。

*   **`KnowledgeStore`**：
    *   存储文件：由 CLI 启动时交互输入或参数 `--knowledge` 指定
    *   内容：结构化的知识点（如“用户决定使用 Python 开发”、“目标是 Web 应用”）。
*   **`TranscriptStore`**：
    *   存储路径：由 CLI 启动时交互输入或参数 `--transcript/--transcript-dir` 指定
    *   内容：完整的对话流水账。
*   **持久化后端**：
    *   支持 `.db`（SQLite）与 `.yaml` 两种后端，默认建议用 `.db`（更适合频繁追加与并发）。
    *   YAML 后端仍使用“先写临时文件，再重命名”的原子写策略，降低损坏风险。

---
*下一步，请阅读 `03_使用说明书.md` 学习如何上手使用。*
