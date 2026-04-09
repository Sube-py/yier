# Codex 后端包

这个目录承载 `yier_web` 中所有 Codex 专属的后端集成代码。

## 职责

- 提供 `ChatService` 使用的 Codex 后端实现
- 统一管理 Codex 会话运行态与生命周期逻辑
- 聚合 Codex 相关的 IPC、SDK、配对编辑器、后台执行模块

## 主要文件

- `backend.py`: Codex 后端编排、turn 生命周期、审批与流式事件处理
- `runtime.py`: 活跃 Codex 会话共享的运行时数据结构
- `background.py`: Codex 后台 follow-up 工具与 runner 命令构建
- `background_runner.py`: 后台 Codex 子进程执行入口

## 子模块

- `ipc/`: IPC 传输与会话状态同步
- `sdk/`: App Server 与 SDK 访问相关辅助代码
- `pairing/`: 配对编辑器 bridge、socket client、MCP server 与 proxy

## 说明

- `yier_web/codex` 是 Codex 专属后端代码的唯一归属目录。
- 通用 backend 抽象仍保留在 `yier_web/agent_backends`。
