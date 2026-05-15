# Codex IPC 工作区

这个目录承载独立 `/codex` 工作区所需的 Codex 后端集成代码。

## 职责

- 根据 yier 持久化的 Codex 设置构建 `codex_ipc.CodexIpcConfig`。
- 为每个活跃 thread 保持一个长期存在的 `CodexIpcSession`。
- 将原始 `ConversationState` 更新分发给 WebSocket 订阅者。
- 让 Codex 与 Yier chat backend 和 agent tools 解耦。

## 主要文件

- `ipc_manager.py`: 会话生命周期、workspace 列表、thread 命令与 WebSocket 状态分发。

## 说明

- HTTP 与 WebSocket 路由在 `yier_web/routes/codex.py`。
- 通用 backend 抽象仍保留在 `yier_web/agent_backends`。

## iframe 嵌入

纯聊天 iframe 路由是 `/codex/embed?embed_token=...`。它复用 Codex
WebSocket，未登录访问需要配置 `YIER_CODEX_EMBED_TOKEN`。

- 新建会话：父窗口发送 `postMessage({ type: 'yier:codex-start', cwd, mode, prompt })`
- 恢复会话：父窗口发送 `postMessage({ type: 'yier:codex-resume', threadId, mode })`

`mode` 是可选参数，支持 `build` 或 `plan`；不传时使用会话当前/默认模式。
`prompt` 是可选参数，并且只能和 `yier:codex-start` 一起用于新建会话；
iframe 会先创建会话、应用 `mode`，再发送这个初始 prompt。成功后 iframe
会向父窗口发送 `postMessage` 事件：

- `yier:codex-ready`
- `yier:codex-thread-created`
- `yier:codex-thread-resumed`
- `yier:codex-prompt-sent`
- `yier:codex-error`
