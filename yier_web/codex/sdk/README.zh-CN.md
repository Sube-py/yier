# Codex SDK 模块

这个模块封装了 Codex 集成中所有面向 App Server 和 SDK 的辅助逻辑。

## 职责

- 构建统一的 App Server 配置对象
- 将 SDK client 行为与 backend 编排逻辑解耦
- 优先通过 SDK 发现 workspace，会在必要时回退到本地磁盘

## 文件

- `config.py`: launcher 解析、sandbox 规范化、plan-mode prompt 常量与 MCP 配置辅助函数
- `client.py`: 带审批拦截能力的 App Server client 包装
- `workspace.py`: Codex workspace 发现、会话分组、配对编辑器列表与本地回退逻辑

## 说明

- 这里尽量不要放 transport 或 backend 编排代码。
- 新增 Codex SDK 入口时，优先扩展这个目录。
