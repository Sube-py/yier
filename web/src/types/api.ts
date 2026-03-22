export interface FrontendHealth {
  ready: boolean
  mode: 'proxy' | 'static' | 'missing'
  detail?: string | null
}

export interface LlmHealth {
  ready: boolean
  detail?: string | null
}

export interface McpRuntimeEntry {
  status: string
  tool_count: number
  error?: string | null
}

export interface McpHealth {
  ready: boolean
  detail?: string | null
  runtime: Record<string, McpRuntimeEntry>
}

export interface HealthResponse {
  frontend: FrontendHealth
  llm: LlmHealth
  mcp: McpHealth
  allowed_roots: string[]
}

export interface ConfigResponse {
  llm: {
    base_url: string
    model: string
    has_api_key: boolean
  }
  allowed_roots: string[]
  mcp_runtime: Record<string, McpRuntimeEntry>
}

export interface McpServerConfig {
  type: 'stdio' | 'http' | 'sse'
  enabled?: boolean
  status?: string
  command?: string
  url?: string
  args?: string[]
  env?: Record<string, string>
  headers?: Record<string, string>
}

export interface McpConfigResponse {
  mcp_servers: Record<string, McpServerConfig>
  runtime: Record<string, McpRuntimeEntry>
}

export interface StoredMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string | null
  reasoning_content?: string | null
  tool_call_id?: string | null
}

export interface StoredActivityEvent {
  event: ChatStreamEvent['event']
  data: Record<string, unknown>
}

export interface SessionTranscriptResponse {
  session_id: string
  messages: StoredMessage[]
  activity_events: StoredActivityEvent[]
}

export interface SessionSummary {
  session_id: string
  title: string
  preview: string
  updated_at: number
  message_count: number
}

export interface SessionListResponse {
  sessions: SessionSummary[]
}

export interface DeleteSessionResponse {
  session_id: string
  deleted: boolean
}

export interface ChatStreamRequest {
  session_id: string
  message: string
}

export interface ChatRunStartedEvent {
  event: 'run_started'
  data: {
    session_id: string
  }
}

export interface ChatToolStartEvent {
  event: 'tool_call_start'
  data: {
    session_id: string
    tool_name: string
    tool_call_id: string
    arguments: Record<string, unknown>
    iteration: number
  }
}

export interface ChatToolEndEvent {
  event: 'tool_call_end'
  data: {
    session_id: string
    tool_name: string
    tool_call_id: string
    result: string
    is_error: boolean
    iteration: number
    metadata?: Record<string, unknown>
    raw?: Record<string, unknown>
  }
}

export interface ShellStreamSnapshot {
  text: string
  truncated: boolean
}

export interface ShellProcessSnapshot {
  session_id: string | null
  state: string
  exit_code: number | null
  started_at: number
  finished_at: number | null
  runtime_seconds: number
  timed_out: boolean
}

export interface ShellEventEntry {
  index: number
  timestamp: number
  type: string
  command?: string
  cwd?: string
  text?: string
  stream?: 'stdout' | 'stderr'
  state?: string
  exit_code?: number
  timed_out?: boolean
  allow_shell?: boolean
  shell_program?: string | null
  append_newline?: boolean
}

export interface ShellRawPayload {
  kind: 'shell_command' | 'background_command'
  request: Record<string, unknown>
  process: ShellProcessSnapshot
  events: ShellEventEntry[]
  latest_event_index: number | null
  streams: {
    stdout: ShellStreamSnapshot
    stderr: ShellStreamSnapshot
  }
  events_truncated: boolean
  dropped_event_count: number
}

export interface ShellActivityState {
  kind: 'shell_command' | 'background_command'
  tool_name: string
  tool_call_id: string
  session_id: string | null
  request: Record<string, unknown>
  process: ShellProcessSnapshot | null
  events: ShellEventEntry[]
  latest_event_index: number | null
  streams: {
    stdout: ShellStreamSnapshot
    stderr: ShellStreamSnapshot
  }
  events_truncated: boolean
  dropped_event_count: number
}

export interface ChatCommandStartEvent {
  event: 'command_start'
  data: {
    session_id: string
    tool_call_id: string
    tool_name: string
    command: string
    cwd: string
    is_background: boolean
  }
}

export interface ChatCommandOutputEvent {
  event: 'command_output'
  data: {
    session_id: string
    tool_call_id: string
    tool_name: string
    stream: 'stdout' | 'stderr'
    content: string
    is_background: boolean
  }
}

export interface ChatCommandEndEvent {
  event: 'command_end'
  data: {
    session_id: string
    tool_call_id: string
    tool_name: string
    command: string
    cwd: string
    exit_code: number
    timed_out: boolean
    is_background: boolean
  }
}

export interface ChatBackgroundCommandStartedEvent {
  event: 'background_command_started'
  data: {
    session_id: string
    tool_call_id: string
    tool_name: string
    background_session_id: string
    command: string
    cwd: string
    state: string
  }
}

export interface ChatBackgroundCommandOutputEvent {
  event: 'background_command_output'
  data: {
    session_id: string
    background_session_id: string
    command: string
    cwd: string
    stream: 'stdout' | 'stderr'
    content: string
  }
}

export interface ChatBackgroundCommandEndEvent {
  event: 'background_command_end'
  data: {
    session_id: string
    background_session_id: string
    command: string
    cwd: string
    state: string
    exit_code: number | null
  }
}

export interface ChatBackgroundFollowupQueuedEvent {
  event: 'background_followup_queued'
  data: {
    session_id: string
    tool_call_id: string
    background_session_id: string
    queue_id: string
    prompt: string
  }
}

export interface ChatBackgroundFollowupStartedEvent {
  event: 'background_followup_started'
  data: {
    session_id: string
    background_session_id: string
    queue_id: string
    prompt: string
  }
}

export interface ChatBackgroundFollowupFinishedEvent {
  event: 'background_followup_finished'
  data: {
    session_id: string
    background_session_id: string
    queue_id: string
    finish_reason: string
  }
}

export interface ChatReasoningEvent {
  event: 'reasoning'
  data: {
    session_id: string
    content: string
    iteration: number
  }
}

export interface ChatAssistantEvent {
  event: 'assistant_message'
  data: {
    session_id: string
    content: string
    iteration: number
  }
}

export interface ChatErrorEvent {
  event: 'error'
  data: {
    session_id: string
    message: string
    iteration?: number
  }
}

export interface ChatStreamDoneEvent {
  event: 'done'
  data: {
    session_id: string
    finish_reason: string
  }
}

export type ChatStreamEvent =
  | ChatRunStartedEvent
  | ChatToolStartEvent
  | ChatToolEndEvent
  | ChatCommandStartEvent
  | ChatCommandOutputEvent
  | ChatCommandEndEvent
  | ChatBackgroundCommandStartedEvent
  | ChatBackgroundCommandOutputEvent
  | ChatBackgroundCommandEndEvent
  | ChatBackgroundFollowupQueuedEvent
  | ChatBackgroundFollowupStartedEvent
  | ChatBackgroundFollowupFinishedEvent
  | ChatReasoningEvent
  | ChatAssistantEvent
  | ChatErrorEvent
  | ChatStreamDoneEvent

export interface UiChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
}

export interface ChatActivity {
  id: string
  kind: 'status' | 'reasoning' | 'tool' | 'command' | 'background'
  title: string
  detail: string
  state: 'running' | 'done' | 'error' | 'info' | 'queued'
  command: string
  cwd: string
  stdout: string
  stderr: string
  meta: string[]
  shell: ShellActivityState | null
}

export interface EditableMcpServer {
  id: string
  name: string
  type: 'stdio' | 'http' | 'sse'
  enabled: boolean
  status: string
  command: string
  url: string
  argsText: string
  envText: string
  headersText: string
}

export interface EditableAllowedRoot {
  id: string
  path: string
}
