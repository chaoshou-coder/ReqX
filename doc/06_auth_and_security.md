# 鉴权 Token 与安全建议

本项目包含两个“本地 HTTP 接口”（WebUI/主 Web API、Knowledge HTTP API）。它们都支持使用 `Authorization: Bearer <token>` 做**轻量鉴权**，避免“同一台机器上的其它进程/脚本/网页”在你不知情的情况下写入你的本地文件。

## 目录

- [1. Token 是什么](#1-token-是什么)
- [2. 为什么要用 Token](#2-为什么要用-token)
- [3. WebUI / Web API：REQX_WEB_TOKEN](#3-webui--web-apireqx_web_token)
- [4. Knowledge HTTP API：REQX_KNOWLEDGE_API_TOKEN](#4-knowledge-http-apireqx_knowledge_api_token)
- [5. 客户端如何携带 Token](#5-客户端如何携带-token)
- [6. 重要安全建议（不要把本服务暴露到公网）](#6-重要安全建议不要把本服务暴露到公网)

## 1. Token 是什么

- **Token 的本质**：一个你自己设置的“共享密钥字符串”。
- **它被用来做什么**：服务端在收到请求时，从 `Authorization` 头里读出 `Bearer <token>`，并与“期望 token”进行常量时间比较；不匹配则返回 `401`。
- **它不是**：登录系统、用户体系、权限模型或 OAuth。这里只有一个简单的“写入开关”。

## 2. 为什么要用 Token

启用 Token 的目的不是做“互联网级别的安全”，而是保护你的本地落盘数据：

- WebUI/主 Web API 包含会写入磁盘的接口（例如保存配置、写提示词、写入知识/发送对话）。
- 如果你把服务监听到 `0.0.0.0` 或者在同机浏览器里被恶意网页诱导发起请求，**没有 token 的写接口**更容易被滥用。
- Token 能把“误触/误调用/同机其它进程的请求”挡在门外，避免静默修改你的 `llm.yaml`、prompt、knowledge 文件。

## 3. WebUI / Web API：REQX_WEB_TOKEN

### 3.1 如何启用

把 Token 写进环境变量 `REQX_WEB_TOKEN`，然后再启动 WebUI：

PowerShell（Windows）：

```powershell
$env:REQX_WEB_TOKEN="YOUR_TOKEN"
reqx web --config llm.yaml --bind 127.0.0.1 --port 8788
```

bash（macOS/Linux）：

```bash
export REQX_WEB_TOKEN="YOUR_TOKEN"
reqx web --config llm.yaml --bind 127.0.0.1 --port 8788
```

### 3.2 启用后会影响哪些接口

- 写入类接口（例如 `/v1/chat/send`、`/v1/config/write`、`/v1/prompt/write`）会要求 `Authorization: Bearer <token>`。
- 读取类接口（例如 `/v1/config/read`、`/v1/prompt/read`、`/v1/knowledge/read`）用于查看脱敏后的内容，一般不需要 token。

注意：当你设置了 `REQX_WEB_TOKEN` 后，客户端必须携带正确 token 才能调用需要鉴权的接口；否则会返回 `401`，并带 `WWW-Authenticate: Bearer`。

### 3.3 WebUI 里怎么填

- 打开 WebUI 左侧边栏的 `Token` 输入框，粘贴你设置的 `REQX_WEB_TOKEN`。
- WebUI 会把 token 存入浏览器 localStorage，并在调用写入接口时自动带上 `Authorization: Bearer <token>`。

## 4. Knowledge HTTP API：REQX_KNOWLEDGE_API_TOKEN

Knowledge HTTP API 是另一个独立服务，专门用于让外部程序追加/读取项目知识文件。

### 4.1 如何启用

方式 A：环境变量（推荐）

```powershell
$env:REQX_KNOWLEDGE_API_TOKEN="YOUR_TOKEN"
reqx knowledge-api --knowledge path\to\project_knowledge.yaml --port 8787
```

方式 B：命令行直接传入（更适合临时调试）

```powershell
reqx knowledge-api --knowledge path\to\project_knowledge.yaml --port 8787 --token "YOUR_TOKEN"
```

### 4.2 启用后行为

- 启用 token 后，对读取/写入接口都会要求 `Authorization: Bearer <token>`。
- 未启用 token 时，本服务通常只监听 `127.0.0.1`，默认允许本机访问；仍建议设置 token，以防同机其他进程误写。

## 5. 客户端如何携带 Token

### 5.1 curl

```bash
curl -X POST http://127.0.0.1:8788/v1/chat/send \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"config_path":"llm.yaml","knowledge_path":"project_knowledge.db","dry_run":false,"imported_context":"","messages":[{"role":"user","content":"我想做一个商城"}]}'
```

### 5.2 PowerShell Invoke-RestMethod

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8788/v1/chat/send `
  -ContentType "application/json" `
  -Headers @{ Authorization = "Bearer YOUR_TOKEN" } `
  -Body '{"config_path":"llm.yaml","knowledge_path":"project_knowledge.db","dry_run":false,"imported_context":"","messages":[{"role":"user","content":"我想做一个商城"}]}'
```

### 5.3 WebUI（浏览器）

- 直接在 WebUI 的 Token 输入框里填入 token 即可。

## 6. 重要安全建议（不要把本服务暴露到公网）

- 只在本机使用时，保持 `--bind 127.0.0.1`。
- 不要把该服务直接暴露到公网；如果必须远程访问，应使用反向代理 + TLS + 额外鉴权（例如 Basic Auth / OAuth）并做好访问控制。
- Token 只是轻量保护写入接口，不等同于完备的互联网安全方案。

