# 全流程操作案例 (End-to-End Workflow)

本文以“做一个背单词 App”为例，从零开始完成：安装 → 初始配置 → 健康检查 → 交互式需求澄清 → 生成规约 →（可选）WebUI 与本地 API 集成。

## 目录

- [0. 约定与目标](#0-约定与目标)
- [1. 安装](#1-安装)
- [2. 初始配置（三选一）](#2-初始配置三选一)
- [3. 健康检查（推荐）](#3-健康检查推荐)
- [4. 开始交互式需求澄清（chat）](#4-开始交互式需求澄清chat)
- [5. 生成规约与收尾（spec/done）](#5-生成规约与收尾specdone)
- [6. 产物清单与常见变体](#6-产物清单与常见变体)
- [7. WebUI 操作要点（可选）](#7-webui-操作要点可选)
- [8. CI / 脚本化全流程（可选）](#8-ci--脚本化全流程可选)

## 0. 约定与目标

- 本文默认你在仓库根目录执行命令（包含 `pyproject.toml`、`llm.yaml.example` 的目录）。
- API Key 只写入环境变量或 `.env`，不要写入 `llm.yaml`。
- 目标：得到一份最终的规约 YAML（通过 `reqx done`），并在本地落盘项目知识与逐字稿，便于复盘与继续迭代。

## 1. 安装

```bash
python -m pip install -e .
```

如果希望安装依赖并保持一致的项目入口，也可以用：

```bash
reqx install --with-deps
```

## 2. 初始配置（三选一）

你需要先生成/准备 `llm.yaml`，再设置 API Key。

### 2.1 方式 A：一键生成（非交互，适合脚本/CI）

```bash
reqx init-config --config-out llm.yaml
```

### 2.2 方式 B：交互式向导（推荐新手）

```bash
reqx wizard
```

向导会按顺序完成：生成 `llm.yaml` →（可选）写入 `.env` →（可选）运行健康检查。

### 2.3 方式 C：WebUI 中编辑配置（可视化）

```bash
reqx web --config llm.yaml --bind 127.0.0.1 --port 8788
```

该命令会启动本机 Web 服务器并占用当前终端（正常现象）。在交互式终端下，程序会尝试自动打开浏览器；你也可以手动打开 `http://127.0.0.1:8788/`，在“配置”页签读取/编辑/保存 `llm.yaml`。停止服务请按 `Ctrl+C`。

## 3. 健康检查（推荐）

配置完成后先跑一次连通性测试，减少“聊到一半才发现配置不对”的概率：

```bash
reqx check-api --config llm.yaml
```

期望输出包含 `ok: true` 与 `response_preview: OK`（内容会脱敏）。

## 4. 开始交互式需求澄清（chat）

建议显式指定项目知识与逐字稿路径，便于稳定复用与断点续聊：

```bash
reqx chat --config llm.yaml --knowledge project_knowledge.db --transcript transcript.yaml
```

进入交互后，先给出你的“模糊需求”：

> 我想做一个背单词 App。

常用交互命令：

- `/show`：查看当前已沉淀的项目知识
- `/spec`：基于项目知识生成规约（预览，不结束）
- `/done`：生成最终规约 + 项目名，结束流程
- `/reset`：清空本轮对话上下文，并清空落盘逐字稿（不删除知识库）

逐字稿默认行为：

- 如果 `--transcript` 指向一个已存在文件，默认视为新会话并清空旧记录；如需继续追加，用 `--resume-transcript`。

## 5. 生成规约与收尾（spec/done）

在对话过程中，任何时刻都可以生成规约预览：

```text
/spec
```

当你认为信息足够稳定后，生成最终规约并结束：

```text
/done
```

如果你希望完全非交互（CI）生成规约，需要准备好 `--knowledge` 并运行：

```bash
reqx spec --config llm.yaml --knowledge project_knowledge.db
reqx done --config llm.yaml --knowledge project_knowledge.db --auto-pick-name
```

## 6. 产物清单与常见变体

### 6.1 常见产物

- 项目知识：`project_knowledge.db`（或你指定的 `.yaml/.db`）
- 逐字稿：`transcript.yaml`（或你指定的路径）
- 规约输出：`/spec` 与 `/done` 会在终端打印 YAML（你可以重定向到文件）

例如把规约落盘：

```bash
reqx done --config llm.yaml --knowledge project_knowledge.db --auto-pick-name > spec.yaml
```

### 6.2 变体：只演练不落盘

```bash
reqx chat --config llm.yaml --knowledge project_knowledge.db --transcript transcript.yaml --dry-run
```

`--dry-run` 会禁用知识库/逐字稿/配置写入，适合演示与调试。

## 7. WebUI 操作要点（可选）

启动 WebUI：

```bash
reqx web --config llm.yaml --bind 127.0.0.1 --port 8788
```

推荐设置 `REQX_WEB_TOKEN` 保护写入类接口（如保存配置、写提示词、发送对话写入知识）：

```bash
export REQX_WEB_TOKEN="YOUR_TOKEN"
```

WebUI 中：

- “配置”页签：读取/保存 `llm.yaml`（读取内容会脱敏展示）
- “Doctor”按钮：解析配置并输出摘要（脱敏）
- “对话”页签：发送消息并把稳定信息写入知识库（后端会解析 `<KNOWLEDGE>...</KNOWLEDGE>`）
- Token 是一个你自己设置的共享密钥，用于避免同机其它进程/网页误调用写接口；完整说明见：[06_auth_and_security.md](06_auth_and_security.md)

## 8. CI / 脚本化全流程（可选）

典型流程：

1) 生成配置（或由 CI 注入已有配置文件）

```bash
reqx init-config --config-out llm.yaml
```

2) 注入 API Key（由 CI 的 Secret/Env 提供，不要写进仓库）

3) 健康检查

```bash
reqx check-api --config llm.yaml
```

4) 基于既有知识生成规约

```bash
reqx spec --config llm.yaml --knowledge project_knowledge.db > spec_preview.yaml
reqx done --config llm.yaml --knowledge project_knowledge.db --auto-pick-name > spec_final.yaml
```
