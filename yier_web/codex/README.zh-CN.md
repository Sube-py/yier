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
