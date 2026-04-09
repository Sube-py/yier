# Codex 配对编辑器模块

这个模块集中管理 Codex 相关流程使用的配对编辑器集成能力。

## 职责

- 暴露 `yier_web` 提供的本地配对编辑器 bridge
- 与在线 paired-editor 的 UNIX socket 通信
- 提供给 Codex 使用的 paired-editor MCP server 包装
- 提供用于调试和日志记录的 socket proxy

## 文件

- `bridge.py`: `yier_web` 对外发布的本地配对编辑器 bridge
- `client.py`: 在线 paired-editor 的 socket client
- `mcp.py`: 向 Codex 暴露 paired-editor 工具的 MCP server
- `proxy.py`: 用于观测和记录 socket 流量的代理

## 说明

- descriptor 文件与 socket 元数据遵循 OpenAI 桌面端配对格式。
- 这个目录是 Codex 专属模块，不建议演变成通用编辑器集成杂货铺。
