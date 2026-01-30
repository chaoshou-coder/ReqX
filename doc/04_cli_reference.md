# 命令与参数参考 (CLI Reference)

## 目录

- [1. 需求挖掘主命令：reqx / requirements-excavate / python -m agents](#1-需求挖掘主命令reqx--requirements-excavate--python--m-agents)
- [2. 清理脚本：clean_repo.py](#2-清理脚本clean_repopy)
- [3. Demo 脚本：run_agent.py](#3-demo-脚本run_agentpy)
- [4. 环境变量（可选）](#4-环境变量可选)

本文档列出本项目**所有可执行命令**、**所有参数**、以及各参数的典型使用场景（交互 / CI）。

## 1. 需求挖掘主命令：reqx / requirements-excavate / python -m agents

入口脚本：`agents/cli.py`（console_scripts：`reqx`、`requirements-excavate`；模块入口：`python -m agents`）。

### 1.1 获取帮助

```bash
reqx --help
python -m agents --help
```

### 1.2 子命令（推荐）

推荐使用子命令形式（更清晰，也更适合脚本化）：

```bash
reqx chat --help
reqx show --help
reqx spec --help
reqx done --help
reqx doctor --help
reqx web --help
reqx knowledge-api --help
reqx init-config --help
reqx check-api --help
reqx clean --help
reqx check-deps --help
reqx install --help
reqx wizard --help
```

### 1.3 兼容参数（旧用法仍可用）

| 参数 | 作用 | 典型场景 |
|:---|:---|:---|
| `--config PATH` | LLM 配置文件路径（未提供则读取环境变量 `LLM_CONFIG_PATH`） | 本地与 CI 均建议显式指定 |
| `--doctor` | 输出当前有效配置与告警（不含密钥）并退出 | CI/排障自检 |
| `--knowledge PATH` | 项目知识文件路径（支持 `.db` 或 `.yaml`） | 交互：跳过启动询问；CI：必填（spec/done/show） |
| `--transcript PATH` | 本次对话逐字稿文件路径（优先于 `--transcript-dir`） | CI：希望逐字稿落到固定文件名 |
| `--transcript-dir DIR` | 本次对话逐字稿输出目录（自动生成文件名） | 交互：把逐字稿统一放到某目录 |
| `--resume-transcript` | 继续使用已有逐字稿文件（默认：新会话并清空旧记录） | 需要在同一逐字稿文件中追加 |
| `--import-transcript PATH` | 导入已有逐字稿文件作为参考上下文 | 从历史会话继续澄清 |
| `--dry-run` | 只演练不落盘（不写知识库/逐字稿/配置） | CI 或调试 |
| `--show` | 输出当前项目知识并退出（等价于 chat 中 `/show`） | CI：快速查看已沉淀内容 |
| `--spec` | 基于项目知识生成需求 YAML 并退出（等价于 chat 中 `/spec`） | CI：从 knowledge 生成规约 |
| `--done` | 生成需求 YAML + 项目名并退出（等价于 chat 中 `/done`） | CI：全流程产物落盘（spec + project_name） |
| `--project-name TEXT` | 用于 `--done`：直接指定项目名称 | CI：避免交互选择 |
| `--project-name-index N` | 用于 `--done`：从生成的 10 个名称中选序号（1-10） | CI：固定选第 N 个 |
| `--auto-pick-name` | 用于 `--done`：自动选择第 1 个生成名称 | CI：无需人工干预 |

### 1.4 交互式 chat（默认行为）

```bash
reqx --config llm.yaml
```

启动后支持的交互命令：
- `/help`：显示帮助
- `/show`：显示当前项目知识
- `/spec`：基于项目知识生成需求 YAML（不结束）
- `/done`：生成需求 YAML → 生成项目名 → 选择后结束流程提示
- `/reset`：清空**本轮对话记录**并清空落盘逐字稿（不删除已落盘项目知识）
- `/exit`：退出

典型场景：
- 个人开发：需要多轮澄清、希望落盘复盘、并在关键节点用 `/spec` 多次迭代。

### 1.5 非交互：show/spec/done（用于 CI）

前置要求：
- 必须提供 `--config`（或设置 `LLM_CONFIG_PATH`）
- 必须提供 `--knowledge`（非交互不再隐式使用默认文件）

示例：仅查看项目知识
```bash
reqx show --config llm.yaml --knowledge path/to/project_knowledge.db
```

示例：从项目知识生成规约
```bash
reqx spec --config llm.yaml --knowledge path/to/project_knowledge.db
```

示例：生成规约 + 自动选择项目名
```bash
reqx done --config llm.yaml --knowledge path/to/project_knowledge.db --auto-pick-name
```

### 1.6 初始配置：init-config / wizard / web

一键生成配置（可脚本化）：

```bash
reqx init-config --config-out llm.yaml
```

交互式生成（未提供 `--config-out` 时会提示输入路径；目标文件已存在会提示是否覆盖）：

```bash
reqx init-config
```

交互式向导（生成配置 → 可选写入 `.env` → 可选健康检查）：

```bash
reqx wizard
```

WebUI 中编辑配置（可视化；同时暴露 `/v1/config/*` 与 `/v1/chat/send` 等接口）：

```bash
reqx web --config llm.yaml --bind 127.0.0.1 --port 8788
```

说明：
- 该命令会启动一个本地 Web 服务器并占用当前终端，停止服务按 `Ctrl+C`。
- 默认会在交互式终端中自动打开浏览器；可用 `--no-open-browser` 关闭，或用 `--open-browser` 强制开启。

### 1.7 本地知识库编辑 API：reqx knowledge-api / reqx-knowledge-api

用途：为外部 agent 提供一个本机 HTTP 接口，用于追加/读取项目知识库（不做内容抽取与判断，仅负责校验与原子落盘）。

```bash
reqx knowledge-api --knowledge path/to/project_knowledge.db --port 8787
```

常用参数：

| 参数 | 作用 |
|:---|:---|
| `--bind HOST` | 监听地址（默认 `127.0.0.1`） |
| `--port N` | 监听端口（默认 `8787`） |
| `--knowledge PATH` | 默认知识库文件路径（请求体可省略 `knowledge_path`） |
| `--base-dir DIR` | 限制 `knowledge_path` 只能落在该目录下 |
| `--token-env NAME` | 从环境变量读取 Bearer token（默认 `REQX_KNOWLEDGE_API_TOKEN`） |
| `--token TEXT` | 直接指定 Bearer token（优先于 env） |

## 2. 清理脚本：clean_repo.py

### 3.1 获取帮助

```bash
python clean_repo.py --help
```

### 3.2 参数一览

| 参数 | 作用 | 典型场景 |
|:---|:---|:---|
| `--root PATH` | 要清理的根目录（默认脚本所在目录） | 多仓库/临时目录清理 |
| `--dry-run` | 只列出将删除的路径，不执行删除 | CI 预检查 |
| `--json` | 输出 JSON（便于 CI 解析） | CI 统计/日志采集 |

## 3. Demo 脚本：run_agent.py

### 4.1 获取帮助

```bash
python run_agent.py --help
```

### 4.2 参数一览

| 参数 | 作用 | 典型场景 |
|:---|:---|:---|
| `--config PATH` | 配置文件路径（或使用 `LLM_CONFIG_PATH`） | 本地/CI |
| `--mode {direct,crew}` | direct 直接调用 Tool；crew 使用 CrewAI 编排 | 示例对比 |
| `--surface TEXT` | 表层问题描述 | 自定义 demo 输入 |
| `--max-tokens N` | 覆盖生成最大 tokens（上限 1024） | 限制成本/速度 |
| `--skip-connectivity-test` | 跳过 GET /models 连通性测试 | 兼容不支持 /models 的服务 |
| `--connectivity-timeout-s S` | 连通性测试超时秒数 | 慢网络/代理环境 |
| `--no-openai-key-map` | 不将 cfg.api_key_env 映射到 `OPENAI_API_KEY` | 环境隔离/自定义 key 管理 |

## 4. 环境变量（可选）

| 环境变量 | 作用 | 典型场景 |
|:---|:---|:---|
| `LLM_CONFIG_PATH` | 默认配置文件路径 | 不想每次传 `--config` |
| `LLM_ENV_PATH` | 强制指定 env 文件路径 | 不使用默认 `.env` |
| `LLM_HTTP_TIMEOUT_S` | HTTP 超时时间（秒） | 网络抖动/企业代理 |
| `REQX_DEBUG_RAW_OUTPUT` | 开启错误时的模型原始输出脱敏预览 | 仅用于排障（默认关闭） |
| `RUN_AGENT_MODE` | run_agent 默认运行模式 | 不传 `--mode` 的兼容方式 |
| `REQX_WEB_TOKEN` | WebUI 写入接口的 Bearer token（建议设置） | 防止本机浏览器侧攻击面导致误写 |
