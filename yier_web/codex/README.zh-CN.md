# Codex IPC 工作区

这个目录承载独立 `/codex` 工作区所需的 Codex 后端集成代码。

## 职责

- 根据 yier 持久化的 Codex 设置构建 `codex_ipc.CodexIpcConfig`。
- 为每个活跃 thread 保持一个长期存在的 `CodexIpcSession`。
- 通过共享 session event hub 将 Codex session 事件分发给 WebSocket、
  SSE 监听者，以及未来的外部 channel sink。
- 承载 Web 应用的 Codex-only 后端能力。

## 主要文件

- `ipc_manager.py`: 会话生命周期、workspace 列表、thread 命令与 session
  event 分发。
- `session_events.py`: Codex session event fanout 使用的 thread 订阅者与
  channel sink 注册表。

## 说明

- HTTP 与 WebSocket 路由在 `yier_web/routes/codex.py`。

## iframe 嵌入

Codex iframe 路由是 `/codex/embed?embed_token=...`。它复用 Codex
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
