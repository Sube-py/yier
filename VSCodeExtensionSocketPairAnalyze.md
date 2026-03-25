`app_pairing_extensions` 不是 socket，本质上是一个“配对注册目录”。这个 VS Code 插件在 macOS 上启动后，会把自己当前会话的信息写到 `~/Library/Application Support/com.openai.chat/app_pairing_extensions/<session-id>` 这个文件里，同时真正开一个 Unix domain socket `/tmp/<session-id>.sock`。桌面 App 再通过这个注册文件找到 socket，和插件通信。

macOS 激活入口在这里：
[extension.js:693](/Users/sube/.vscode/extensions/openai.chatgpt-26.318.11754-darwin-arm64/out/extension.js#L693)

**1. `app_pairing_extensions` 在做什么**

在 `extension.js` 里有这段逻辑：

- `HM()` 生成路径  
  `~/Library/Application Support/com.openai.chat/app_pairing_extensions/<Um>`
- `Um` 是当前插件会话 id，格式大致是  
  `${vscodeAppName}-${randomUUID()}`
- `register()` 会把一个 JSON payload 写进这个文件
- 同时 `startServer()` 在 `/tmp/${Um}.sock` 上监听

也就是说它做的是“登记自己可被桌面 App 发现”，不是自己承担消息通道。

这个注册文件里写入的内容包括：

- `appName`
- `bundleID`
- `extensionVersion`
- `marketplaceID`
- `extensionName`
- `workspaceName`
- `id`
- `capabilities`
- `needsReload`
- `socketPath`
- `timestamp`

其中 `bundleID` 不是固定一个值，它根据宿主编辑器决定，比如：

- VS Code: `com.microsoft.VSCode`
- VS Code Insiders: `com.microsoft.VSCodeInsiders`
- VSCodium: `com.vscodium`
- Cursor: `com.todesktop.230313mzl4w4u92`
- Windsurf: `com.exafunction.windsurf`

所以这个文件其实像一条“我是哪个编辑器、哪个工作区、当前支持哪些能力、请连哪个 socket”的注册记录。

**2. socket 怎么分享消息**

真正通信走的是 Unix socket：

- 路径：`/tmp/${Um}.sock`
- 服务端：插件用 `net.createServer(...)` 起服务
- 协议：4 字节小端长度前缀 + UTF-8 JSON

也就是：

1. 先读 4 字节长度
2. 再读对应长度的 JSON 文本
3. `JSON.parse(...)`
4. 按 `command` 分发
5. 返回时再把响应 JSON 做成 `4-byte length + body`

这部分在 `Qee()` 和 `zm()` 里很清楚：

- `Qee()` 负责收包解帧
- `zm()` 负责回包封帧

所以它不是 WebSocket，也不是 HTTP，而是非常简单的本地 IPC 协议。

**3. socket 上到底传了什么消息**

桌面桥接支持的命令集在 `bx` 里定义，分三类：

- `queryGet`
- `queryPost`
- `mutationPost`

具体命令有：

- `ping`
- `content`
- `selections`
- `reload`
- `markForReload`
- `removeHighlights`
- `highlightLines`
- `highlight`
- `setContent`
- `replaceSelection`

请求体形状大致是：

```json
{
  "command": "content",
  "payload": {}
}
```

不同命令的 `payload` 不同。

返回也是 JSON，常见形状像：

```json
{
  "status": "success",
  ...
}
```

或者错误：

```json
{
  "status": 400,
  "error": "..."
}
```

**4. 实际共享了哪些数据**

`content` 会把当前所有可见编辑器内容发出去，字段包括：

- `id`: 这里其实就是文件名路径
- `content`: 整个编辑器文本
- `filename`
- `selectedText`
- `selectionRange`
- `selectionLine`

`selectionRange` 结构是：

- `location`
- `length`

`selections` 会共享当前可见编辑器中的选中内容列表：

- `selectedText`
- `selectionLine`

`ping` 返回插件自身信息：

- `name`
- `version`

`reload` 会直接触发：

- `workbench.action.reloadWindow`

`markForReload` 会把 `needsReload` 标记为 `true`，然后重新写注册文件

编辑类 mutation 命令会让桌面 App 反向驱动编辑器：

- `removeHighlights`
  payload: `textfieldID`, `animated`
- `highlightLines`
  payload: `lines`, `textfieldID`
- `highlight`
  payload: `startChar`, `endChar`, `textfieldID`
- `setContent`
  payload: `content`, `textfieldID`
- `replaceSelection`
  payload: `content`, `textfieldID`

所以如果你问“分享了什么消息”，对这条 socket 来说，核心就是两类：

- 读编辑器状态：文件内容、选区、插件版本
- 改编辑器状态：高亮、替换选区、整文件改写、触发 reload

**5. 一个重要限制**

这条桌面桥接会检查 workspace trust。工作区不受信任时，除了 `ping`，其他命令都返回 405 风格错误，不允许读取编辑器内容。

---

## 6. Thread 实时同步协议逐字段对照

上面那套 `/tmp/<session-id>.sock` + `app_pairing_extensions`，本质上是“编辑器内容桥”。  
真正和 Codex thread 实时同步相关的，不是那套 `command/payload` 协议，而是另一套本地 IPC 总线：

- socket: `os.tmpdir()/codex-ipc/ipc-<uid>.sock`
- framing: `4-byte little-endian length + UTF-8 JSON`
- top-level message type:
  - `initialize`
  - `request`
  - `response`
  - `broadcast`
  - `client-discovery-request`
  - `client-discovery-response`

Yier 当前实现文件：

- [codex_ipc.py](/Users/sube/me/yier/yier_web/codex_ipc.py)
- [chat.py](/Users/sube/me/yier/yier_web/chat.py)
- [codex_backend.py](/Users/sube/me/yier/yier_web/agent_backends/codex_backend.py)

### 6.1 顶层 IPC envelope 对照

| 项目 | Codex 真实协议 | Yier 当前实现 | 结论 |
| --- | --- | --- | --- |
| `type` | `initialize/request/response/broadcast/client-discovery-request/client-discovery-response` | 全部已实现 | 已对齐 |
| 帧格式 | 4 字节小端长度 + JSON | 已实现 | 已对齐 |
| `request.requestId` | 字符串 | 已实现 | 已对齐 |
| `request.method` | 字符串 | 已实现 | 已对齐 |
| `request.params` | object | 已实现 | 已对齐 |
| `request.version` | 按 method 固定版本 | 已实现 method-version 校验 | 已对齐 |
| `request.targetClientId` | 可选 | 已实现发送支持 | 已对齐 |
| `request.sourceClientId` | 发送方 client id | 已实现 | 已对齐 |
| `response.resultType` | `success` / `error` | 已实现 | 已对齐 |
| `response.handledByClientId` | 处理端 client id | 已实现 | 已对齐 |
| `broadcast.sourceClientId` | 广播源 client id | 已实现 | 已对齐 |

### 6.2 method version 对照

| method | Codex 真实版本 | Yier 当前实现 | 结论 |
| --- | --- | --- | --- |
| `thread-stream-state-changed` | `5` | `5` | 已对齐 |
| `thread-archived` | `2` | `2` | 已对齐 |
| `thread-unarchived` | `1` | `1` | 已对齐 |
| 全部 `thread-follower-*` | `1` | `1` | 已对齐 |
| `thread-queued-followups-changed` | `1` | `1` | 已对齐 |

### 6.3 broadcast: `thread-stream-state-changed`

真实协议：

```json
{
  "type": "broadcast",
  "method": "thread-stream-state-changed",
  "version": 5,
  "params": {
    "conversationId": "...",
    "change": {
      "type": "snapshot",
      "conversationState": {}
    }
  }
}
```

或者：

```json
{
  "params": {
    "conversationId": "...",
    "change": {
      "type": "patches",
      "patches": []
    }
  }
}
```

逐字段对照：

| 字段 | Codex 真实协议 | Yier 当前实现 | 结论 |
| --- | --- | --- | --- |
| `params.conversationId` | 必需 | 已发 | 已对齐 |
| `params.change.type` | `snapshot` 或 `patches` | 只发 `snapshot` | 宽松兼容 |
| `params.change.conversationState` | snapshot 时必需 | 已发 | 已对齐 |
| `params.change.patches` | patches 时必需 | 未实现 | 未实现但非首要阻塞 |
| snapshot 额外字段 | 未见 Codex 原生使用 `_yier_*` 字段 | 额外注入 `_yier_trigger_event`、`_yier_updated_at` | 宽松兼容，建议最终去掉 |

### 6.4 `conversationState` 字段对照

Codex owner 侧 `createConversationStateFromThread()` 反出来至少有这些顶层字段：

- `id`
- `hostId`
- `turns`
- `pendingSteers`
- `requests`
- `createdAt`
- `updatedAt`
- `title`
- `source`
- `latestModel`
- `latestReasoningEffort`
- `previousTurnModel`
- `latestCollaborationMode`
- `hasUnreadTurn`
- `rolloutPath`
- `gitInfo`
- `resumeState`
- `latestTokenUsageInfo`
- `cwd`

Yier 当前 `build_codex_ipc_conversation_state()` 返回：

- `id`
- `hostId`
- `turns`
- `pendingSteers`
- `requests`
- `createdAt`
- `updatedAt`
- `title`
- `source`
- `latestModel`
- `latestReasoningEffort`
- `previousTurnModel`
- `latestCollaborationMode`
- `hasUnreadTurn`
- `rolloutPath`
- `gitInfo`
- `resumeState`
- `latestTokenUsageInfo`
- `cwd`
- `threadId`
- `threadRuntimeStatus`

逐字段对照：

| 字段 | Codex 真实协议 | Yier 当前实现 | 结论 |
| --- | --- | --- | --- |
| `id` | thread/conversation id | `session_id` | 已对齐 |
| `hostId` | 存在 | 固定 `"local"` | 基本可用 |
| `turns` | 真实 `AppServerConversationTurn[]` | transcript 拼出来的简化 turn | 高风险不一致 |
| `pendingSteers` | 存在，默认可空数组 | 固定 `[]` | 基本可用 |
| `requests` | pending raw requests | 直接塞审批请求 model_dump | 高风险不一致 |
| `createdAt` | 时间戳 ms | 目前直接复用 `updated_at` | 宽松兼容 |
| `updatedAt` | 时间戳 ms | 已实现 | 已对齐 |
| `title` | `string \| null` | 直接从 metadata 取 | 已对齐 |
| `source` | 存在 | 默认 `"chat"` | 基本可用 |
| `latestModel` | 存在 | 已实现 | 已对齐 |
| `latestReasoningEffort` | 存在 | 已实现 | 已对齐 |
| `previousTurnModel` | 存在 | 固定 `None` | 宽松兼容 |
| `latestCollaborationMode` | 存在 | 已实现 | 已对齐 |
| `hasUnreadTurn` | 存在 | 固定 `False` | 基本可用 |
| `rolloutPath` | 存在 | 已实现 | 已对齐 |
| `gitInfo` | 存在 | 透传 backend_state | 可能对齐，待抓包确认子字段 |
| `resumeState` | 存在 | 默认 `"resumed"` | 基本可用 |
| `latestTokenUsageInfo` | 存在 | 透传 backend_state | 可能对齐，待确认子字段 |
| `cwd` | 存在 | `project_path` | 已对齐 |
| `threadId` | 未确认是 Codex 原生顶层字段 | 额外增加 | 建议移除或只本地调试用 |
| `threadRuntimeStatus` | 未确认是 Codex 原生顶层字段 | 额外增加 | 建议移除或只本地调试用 |

### 6.5 `turns[*]` 字段对照

Yier 当前 turn 结构：

```json
{
  "turnId": "session:turn:1",
  "status": "completed|inProgress",
  "items": [
    {
      "type": "userMessage",
      "content": [{"type": "text", "text": "..."}]
    },
    {
      "type": "agentMessage",
      "text": "..."
    }
  ],
  "params": {
    "input": [{"type": "text", "text": "..."}]
  },
  "turnStartedAtMs": 0,
  "finalAssistantStartedAtMs": 0
}
```

这里最大的风险不是“字段名差一点”，而是整个 turn/item schema 现在只是“能表达聊天内容”，不一定等于 Codex app 真正反序列化所需的 `AppServerConversationTurn` / `ConversationItem`。  
也就是说，follower 端可能能看到 thread 存在，但很可能显示不全、状态不更新、按钮行为不正常。

### 6.6 broadcast: `thread-queued-followups-changed`

真实协议：

```json
{
  "type": "broadcast",
  "method": "thread-queued-followups-changed",
  "version": 1,
  "params": {
    "conversationId": "...",
    "messages": []
  }
}
```

逐字段对照：

| 字段 | Codex 真实协议 | Yier 当前实现 | 结论 |
| --- | --- | --- | --- |
| `params.conversationId` | 必需 | 已发 | 已对齐 |
| `params.messages` | `QueuedFollowUpMessage[]` | 已发对象数组 | 外层已对齐 |
| `messages[*].id` | 存在 | `item.queue_id` | 取值逻辑待确认 |
| `messages[*].text` | 存在 | 已实现 | 已对齐 |
| `messages[*].context.workspaceRoots` | 存在 | 已实现 | 已对齐 |
| `messages[*].cwd` | 存在 | 已实现 | 已对齐 |
| `messages[*].createdAt` | 存在 | 已实现 | 已对齐 |

这里的关键问题不在广播，而在“反向写回”。

### 6.7 request: `thread-follower-start-turn`

真实协议：

```json
{
  "params": {
    "conversationId": "...",
    "turnStartParams": {
      "...": "..."
    }
  }
}
```

真实响应业务体：

```json
{
  "result": "AppServer.v2.TurnStartResponse"
}
```

Yier 当前行为：

| 字段 | Codex 真实协议 | Yier 当前实现 | 结论 |
| --- | --- | --- | --- |
| `conversationId` | 必需 | 已支持 | 已对齐 |
| `turnStartParams` | 必需 | 已支持；同时兼容扁平 params | 宽松兼容 |
| `turnStartParams.input` | 真实协议核心输入 | 可从 `prompt/message/text/content/input/items` 抽取 | 宽松兼容但不原生 |
| `turnStartParams.model` | 可选 | 已支持 | 已对齐 |
| `turnStartParams.reasoningEffort` | 可选 | 已支持 | 已对齐 |
| `turnStartParams.collaborationMode` | 可选 | 已支持 | 已对齐 |
| 真实 `TurnStartResponse` | 可能含 turn/thread 的更完整信息 | 当前只返回 `{"result":{"ok":true,"conversationId":"..."}}` | 高风险不一致 |

注意：因为 IPC 外层还会再包一层 `response.result`，所以线上的实际返回会变成：

```json
{
  "type": "response",
  "resultType": "success",
  "result": {
    "result": {
      "ok": true,
      "conversationId": "..."
    }
  }
}
```

这和“真实业务体是 `TurnStartResponse`”之间仍然有明显差距。

### 6.8 request: `thread-follower-steer-turn`

真实协议：

```json
{
  "params": {
    "conversationId": "...",
    "input": [],
    "attachments": [],
    "restoreMessage": {}
  }
}
```

逐字段对照：

| 字段 | Codex 真实协议 | Yier 当前实现 | 结论 |
| --- | --- | --- | --- |
| `conversationId` | 必需 | 已支持 | 已对齐 |
| `input` | 必需 | 已支持 | 已对齐 |
| `attachments` | 可选 | 读取了但没有真正消费 | 高风险不一致 |
| `restoreMessage` | 可选 | 读取了语义入口，但没有真正消费 | 高风险不一致 |
| `turnId` | 真实协议里没有硬要求 | Yier 允许 `expectedTurnId/turnId`，缺失时自动查 active/latest turn | 这是兼容增强，不是问题 |
| 响应体 | `AppServer.v2.TurnSteerResponse` | 直接透传 backend raw response | 大概率接近真实 |

### 6.9 request: `thread-follower-interrupt-turn`

真实协议：

```json
{
  "params": {
    "conversationId": "..."
  }
}
```

真实响应业务体：

```json
{
  "ok": true
}
```

Yier 当前行为：

| 字段 | Codex 真实协议 | Yier 当前实现 | 结论 |
| --- | --- | --- | --- |
| `conversationId` | 必需 | 已支持 | 已对齐 |
| `turnId` | 通常不需要 | 可选兼容 | 不是问题 |
| 响应体 | `{"ok": true}` | `{"ok": true, "result": {...}}` | 高风险不一致 |

### 6.10 request: 设置类方法

#### `thread-follower-set-model-and-reasoning`

真实：

```json
{
  "params": {
    "conversationId": "...",
    "model": "...",
    "reasoningEffort": "..."
  }
}
```

Yier：

- `model` 已支持
- `reasoningEffort` 已支持
- 还兼容了 `reasoning_effort` / `serviceTier` / `service_tier` / `effort`

结论：外层可用，属于“宽松兼容”。

#### `thread-follower-set-collaboration-mode`

真实：

```json
{
  "params": {
    "conversationId": "...",
    "collaborationMode": "build|plan|..."
  }
}
```

Yier：

- 已支持 `collaborationMode`
- 还兼容 `collaboration_mode`

结论：已对齐。

### 6.11 request: 编辑最后一条用户消息

真实协议：

```json
{
  "params": {
    "conversationId": "...",
    "turnId": null,
    "message": "...",
    "agentMode": "..."
  }
}
```

Yier 当前行为：

| 字段 | Codex 真实协议 | Yier 当前实现 | 结论 |
| --- | --- | --- | --- |
| `conversationId` | 必需 | 已支持 | 已对齐 |
| `turnId` | 可空 | 读取但实际上未用于精确定位 turn | 宽松兼容 |
| `message` | 必需 | 已支持 | 已对齐 |
| `agentMode` | 可选 | 当前未消费 | 可能不完整 |
| 响应体 | `{"ok": true}` | `{"ok": true}` | 已对齐 |

### 6.12 request: approval / user-input / elicitation

真实协议：

- `thread-follower-command-approval-decision`
  - params: `{ conversationId, requestId, decision }`
  - response: `{ ok: true }`
- `thread-follower-file-approval-decision`
  - params: `{ conversationId, requestId, decision }`
  - response: `{ ok: true }`
- `thread-follower-submit-user-input`
  - params: `{ conversationId, requestId, response }`
  - response: `{ ok: true }`
- `thread-follower-submit-mcp-server-elicitation-response`
  - params: `{ conversationId, requestId, response }`
  - response: `{ ok: true }`

Yier 当前行为：

| 项目 | Yier 当前实现 | 结论 |
| --- | --- | --- |
| `requestId` | 可缺省，缺省时会从 pending approvals 里推一个 | 宽松兼容 |
| `decision` | 支持 `approve/allow/reject/...` 映射到 `accept/decline/...` | 已对齐 |
| command/file approval 回填 | 直接回填 raw payload，例如 `{"decision":"accept"}` | 已基本对齐 |
| submit-user-input | 直接把 `response` 对象原样塞回 pending request | 大概率接近真实 |
| submit-mcp-server-elicitation-response | 同上 | 大概率接近真实 |

这里真正风险是：`requests[*]` 的快照结构若不够像 Codex 原生，Codex app 可能根本不会正确渲染出这些 pending request，导致你虽然“能回复”，但对端 UI 不一定能发得出来。

### 6.13 request: `thread-follower-set-queued-follow-ups-state`

真实协议：

```json
{
  "params": {
    "conversationId": "...",
    "state": {}
  }
}
```

真实响应：

```json
{
  "ok": true
}
```

Yier 当前行为：

| 字段 | Codex 真实协议 | Yier 当前实现 | 结论 |
| --- | --- | --- | --- |
| `conversationId` | 必需 | 已支持 | 已对齐 |
| `state` | 必需 | 收到了，但只是整体存入 `backend_state["queued_followups_state"]` | 高风险不一致 |
| 真实副作用 | 应驱动 owner 端 queued follow-ups 队列状态 | 当前没有真正驱动 `FollowupQueueManager` | 高风险不一致 |
| 响应体 | `{"ok": true}` | `{"ok": true}` | 已对齐 |

### 6.14 哪些地方最可能导致“看得到会话但不同步”

按风险排序，当前最值得怀疑的是这 4 个点：

1. `conversationState.turns` 还是简化版，不是 Codex 原生 turn schema。
2. `thread-follower-start-turn` 的业务响应体不像真实 `TurnStartResponse`。
3. `thread-follower-interrupt-turn` 多返回了一个 `result` 字段。
4. `thread-follower-set-queued-follow-ups-state` 只存档，不真正驱动 follow-up queue。

### 6.15 当前阶段的判断

如果目标是“能基本同步文本 thread，Codex app 能认这个会话”，那么：

- IPC 外层
- method version
- request/broadcast 路由
- approval 决策回填

这些已经八九不离十了。

如果目标是“让 Codex app 像对待原生 owner 一样完整同步、可编辑、可中断、可恢复、可 follow-up 管理”，那现在最大的缺口不是 socket 本身，而是：

- `conversationState` 的原生字段完整度
- `turns[*]` / `requests[*]` 的真实 schema
- `TurnStartResponse` / `TurnInterruptResponse` 的真实业务体
- queued follow-ups 的真实 owner 侧状态机
