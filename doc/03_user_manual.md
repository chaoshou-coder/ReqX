# 使用说明书 (User Manual)

欢迎使用 **ReqX**！本手册将手把手教你如何从零开始配置环境，并利用该工具进行高效的需求分析。

## 1. 环境准备

### 1.1 前置要求
*   **操作系统**：Windows, macOS, 或 Linux 均可。
*   **Python**：需要安装 Python 3.10 或更高版本。

### 1.2 安装步骤

1.  **获取代码**：
    将本项目下载到你的本地目录（例如 `E:\code\reqx`）。

2.  **安装依赖**：
    打开终端（Terminal），进入项目根目录，运行以下命令来安装项目所需的依赖包：
    ```powershell
    # Windows 用户
    python letsgo.py --install
    
    # Mac/Linux 用户
    python3 letsgo.py --install
    ```
    *注：这个命令会以“可编辑模式”安装本项目，同时会自动下载 `crewai`, `langchain`, `httpx` 等必要的第三方库。*

## 2. 配置指南

在开始对话之前，你需要告诉程序使用哪个大模型。

### 2.1 初始化配置文件
在终端运行：
```powershell
python letsgo.py --init-config
```
这会提示你输入要生成的配置文件路径（也可用 `--config-out` 直接指定），并从 `llm.yaml.example` 复制生成。

### 2.2 编辑 `llm.yaml`
用你喜欢的文本编辑器打开 `llm.yaml`，根据你的模型服务商进行修改。

**示例 1：使用 OpenAI (官方)**
```yaml
provider: openai
model: gpt-4o
api_key_env: OPENAI_API_KEY
```

**示例 2：使用 Azure OpenAI**
```yaml
provider: azure
model: gpt-4
azure_endpoint: https://你的资源名.openai.azure.com/
azure_deployment: 你的部署名
azure_api_version: 2024-02-15-preview
api_key_env: AZURE_OPENAI_API_KEY
```

**示例 3：使用 DeepSeek / Moonshot (OpenAI 兼容模式)**
```yaml
provider: openai_compatible
model: deepseek-chat
base_url: https://api.deepseek.com/v1  # 注意：不同厂商的 URL 可能不同，请查阅其文档
api_key_env: DEEPSEEK_API_KEY
```

### 2.3 设置 API Key
**千万不要把 API Key 直接写在 `llm.yaml` 里！** 这是一个非常危险的习惯。
请在项目根目录创建一个名为 `.env` 的文件（注意前面有个点），并在里面写入：

```env
# 如果你是 OpenAI
OPENAI_API_KEY=YOUR_OPENAI_API_KEY

# 如果你是 DeepSeek
DEEPSEEK_API_KEY=YOUR_DEEPSEEK_API_KEY
```
程序会自动读取这个文件。

### 2.4 验证配置
配置完成后，运行以下命令进行体检：
```powershell
python letsgo.py --check-api
```
如果你看到类似下面的输出，说明一切就绪：
```yaml
ok: true
provider: openai
model: gpt-4o
latency_ms: 1200
response_preview: OK
```

## 3. 实战教程：开始需求挖掘

### 3.1 启动对话
在终端运行：
```powershell
python -m agents
```
或者，如果你已经正确安装，可以直接输入简写命令：
```powershell
reqx
```

查看全部参数（交互/CI 通用）：
```powershell
reqx --help
```

### 3.2 对话流程示例
程序启动后，你会看到欢迎语。现在，你可以像和人聊天一样开始描述你的想法。

> **你**：我想做一个背单词的 App。
>
> **助手**：好的。这个 App 的目标用户是谁？是考研学生，还是出国留学的托福雅思党？
>
> **你**：主要是考研党。
>
> **助手**：明白了。针对考研党，你需要包含真题词汇吗？是否需要根据艾宾浩斯遗忘曲线来安排复习？
>
> **你**：对，必须有艾宾浩斯曲线，还要能导入 PDF 真题。

*(在你们对话的过程中，助手会默默地把“目标用户：考研党”、“功能：艾宾浩斯曲线”、“功能：PDF 导入”记入小本本。)*

### 3.3 常用指令

在对话过程中，你可以随时输入以下指令（以 `/` 开头）：

*   **`/spec`**：**最核心的功能**。查看当前生成的“项目规约”。它会把零散的对话整理成一份 YAML 文档展示给你。你可以多轮对话，多次生成，直到满意为止。
*   **`/show`**：查看助手目前记录了哪些“项目知识”。
*   **`/reset`**：清空当前对话上下文，并清空落盘逐字稿（但已保存的知识不会丢）。
*   **`/done`**：**任务完成**。
    1.  系统会生成最终版的规约。
    2.  根据规约，自动为你构思 10 个项目名称供你选择。
    3.  保存所有文件，并结束程序。
*   **`/exit`**：直接退出程序。

### 3.3.1 非交互（CI）用法

非交互模式下，所有“可落盘的路径”都必须显式通过参数指定（例如 `--knowledge`、`--transcript` 或 `--transcript-dir`）。

示例：基于已存在的项目知识生成规约并退出
```powershell
reqx --config llm.yaml --knowledge path\to\project_knowledge.yaml --spec
```

示例：生成规约 + 自动选择项目名并退出
```powershell
reqx --config llm.yaml --knowledge path\to\project_knowledge.yaml --done --auto-pick-name
```

### 3.3.2 外部 Agent 写入项目知识（本地 API）

如果你有一个“通过 API 接入的 agent”，并希望它**直接编辑本机的项目知识库文件**，可以启动本地知识库 API，然后由 agent 调用 HTTP 接口完成写入。

启动服务（默认仅监听本机 127.0.0.1）：

```powershell
reqx-knowledge-api --knowledge path\to\project_knowledge.yaml --port 8787
```

追加知识条目：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8787/v1/knowledge/append `
  -ContentType "application/json" `
  -Body '{"items":["目标用户：考研党","功能：导入 PDF 真题"]}'
```

读取当前知识库快照：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8787/v1/knowledge/read"
```

### 3.4 结果文件
对话结束后，你可以在项目目录下找到以下文件：
*   项目知识文件：启动时你选择或通过参数 `--knowledge` 指定的路径（默认提示为当前目录的 `project_knowledge.yaml`）。
*   逐字稿文件：启动时你选择输出目录或通过参数 `--transcript/--transcript-dir` 指定的路径；若 `--transcript` 指向已存在文件，默认新会话会清空旧记录，使用 `--resume-transcript` 才会继续追加。

## 4. 常见问题 (FAQ)

**Q: 运行 `check-api` 报错 "Connection error"？**
A: 请检查：
1.  网络是否通畅（是否需要开启/关闭 VPN）。
2.  `base_url` 是否填写正确（特别是 `openai_compatible` 模式，注意末尾是否有 `/v1`）。
3.  API Key 是否有效（是否欠费）。

**Q: 模型回复的内容很奇怪，或者不理我？**
A: 可能是因为使用的模型较弱（如 gpt-3.5 或某些小参数模型），无法很好地遵循复杂的指令。建议使用 GPT-4o, Claude 3.5 Sonnet 或 DeepSeek V3 等强力模型。

**Q: 如何清空所有历史数据重新开始？**
A: 运行 `python letsgo.py --clean`，然后手动删除你实际使用的项目知识文件（`--knowledge` 指定的路径）以及逐字稿输出目录即可。

---
*祝你开发愉快！如有 Bug，欢迎提交 Issue。*
