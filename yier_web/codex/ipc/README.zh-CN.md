# Codex IPC 模块

这个模块负责 `yier_web` 与 Codex follower IPC 消息之间的桥接。

## 职责

- 维护 follower IPC client 连接
- 向兼容 Codex 的消费者广播 stream patch 与 snapshot
- 构建并应用 Codex UI 所需的会话状态更新

## 文件

- `bridge.py`: IPC client、广播处理与 follower bridge 逻辑
- `state.py`: 会话状态构建、patch 应用与排队 follow-up 组装

## 集成关系

- `ChatService` 通过这里把流式事件同步到 IPC
- 读取 `yier_web.codex.backend` 中的 Codex backend 状态
- 通过 `ChatService` 回写规范化后的 IPC 会话状态
