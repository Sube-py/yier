export type LlmProvider = '' | 'zai' | 'zai-coding-plan'
export type BackendId = 'yier' | 'codex'
export type WorkspaceSurface = 'yier' | 'codex' | 'claude'
export type CodexWorkMode = 'plan' | 'build'
export type ApprovalDecision = 'accept' | 'accept_for_session' | 'decline' | 'cancel'

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
  backends: Record<BackendId, { ready: boolean; detail?: string | null }>
  allowed_roots: string[]
}

export interface ConfigResponse {
  llm: {
    provider: LlmProvider
    base_url: string
    model: string
    has_api_key: boolean
  }
  backends: Array<{
    id: BackendId
    label: string
  }>
  session_defaults: {
    default_backend_id: BackendId
    default_project_path: string
    channel_backend_id: BackendId
    channel_project_path: string
    channel_auto_approve_codex_requests: boolean
    workspace_surface?: WorkspaceSurface
  }
  codex: {
    launcher_command: string
    model: string
    sandbox: 'read-only' | 'workspace-write' | 'danger-full-access'
    approval_policy: 'untrusted' | 'on-failure' | 'on-request' | 'never'
    approvals_reviewer: 'user' | 'guardian_subagent'
    personality: 'none' | 'friendly' | 'pragmatic'
    reasoning_effort: 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh'
    service_tier: '' | 'fast' | 'flex'
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

export interface ChannelMeta {
  platform: string
  account_id: string
  peer_id: string
}

export interface StoredMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string | null
  reasoning_content?: string | null
  tool_call_id?: string | null
  source: 'chat' | 'channel'
  channel_meta?: ChannelMeta | null
}

export interface StoredActivityEvent {
  event: ChatStreamEvent['event']
  data: Record<string, unknown>
}

export interface SessionTranscriptResponse {
  session_id: string
  source: 'chat' | 'channel'
  backend_id: BackendId
  project_path: string
  channel_meta?: ChannelMeta | null
  codex_work_mode?: CodexWorkMode | null
  backend_runtime?: BackendRuntime | null
  pending_approvals: PendingApproval[]
  messages: StoredMessage[]
  activity_events: StoredActivityEvent[]
}

export interface SessionSummary {
  session_id: string
  title: string
  preview: string
  updated_at: number
  message_count: number
  source: 'chat' | 'channel'
  backend_id: BackendId
  project_path: string
  channel_meta?: ChannelMeta | null
  codex_work_mode?: CodexWorkMode | null
}

export interface SessionListResponse {
  sessions: SessionSummary[]
}

export interface DeleteSessionResponse {
  session_id: string
  deleted: boolean
}

export interface ChannelPlatformSummary {
  name: string
  label: string
  implemented: boolean
  account_count: number
  running_count: number
}

export interface ChannelAccountSummary {
  platform: string
  account_id: string
  configured: boolean
  enabled: boolean
  running: boolean
  name?: string | null
  last_inbound_at?: number | null
  last_outbound_at?: number | null
  last_error?: string | null
  login_status?: string | null
}

export interface ChannelWorkspaceResponse {
  platforms: ChannelPlatformSummary[]
  accounts: ChannelAccountSummary[]
}

export interface ChannelPlatformsResponse {
  platforms: Array<{
    name: string
    label: string
    implemented: boolean
  }>
}

export interface ChannelConfigResponse {
  enabled_platforms: string[]
  weixin: Record<string, unknown>
}

export interface SaveChannelConfigRequest {
  enabled_platforms: string[]
  weixin: Record<string, unknown>
}

export interface ChannelAccountsResponse {
  accounts: ChannelAccountSummary[]
}

export interface ChannelLoginRequest {
  account_id?: string | null
}

export interface ChannelAccountActionResponse {
  account: ChannelAccountSummary
}

export interface ChatStreamRequest {
  session_id: string
  message: string
}

export interface CreateSessionRequest {
  backend_id?: BackendId | null
  project_path?: string | null
}

export interface SelectDirectoryRequest {
  initial_path?: string | null
}

export interface SelectDirectoryResponse {
  selected: boolean
  project_path: string
}

export interface SaveAppSettingsRequest {
  session_defaults: ConfigResponse['session_defaults']
  codex: ConfigResponse['codex']
}

export interface UpdateCodexSessionModeRequest {
  codex_work_mode: CodexWorkMode
}

export interface OpenCodexSessionRequest {
  thread_id: string
}

export interface OpenCodexSessionResponse {
  session_id: string
}

export interface CodexNativeSessionSummary {
  thread_id: string
  title: string
  preview: string
  updated_at: number
  started_at: number
  status: string
  cwd: string
  project: string
  project_path: string
  source: string
}

export interface CodexProjectGroup {
  project: string
  project_path: string
  session_count: number
  sessions: CodexNativeSessionSummary[]
}

export interface CodexPairingExtensionSummary {
  id: string
  app_name: string
  workspace_name: string
  extension_name: string
  extension_version: string
  bundle_id: string
  marketplace_id: string
  capability_names: string[]
  capability_count: number
  socket_path: string
  is_online: boolean
  needs_reload: boolean
  last_seen_at: number
}

export interface CodexWorkspaceResponse {
  projects: CodexProjectGroup[]
  paired_editors: CodexPairingExtensionSummary[]
}

export interface ApprovalOption {
  value: ApprovalDecision
  label: string
}

export interface PendingApproval {
  request_id: string
  method: string
  kind: string
  title: string
  detail: string
  options: ApprovalOption[]
  payload: Record<string, unknown>
}

export interface BackendRuntime {
  backend_id: BackendId
  label: string
  ready: boolean
  status: string
  thread_id?: string | null
  active_flags: string[]
  detail?: string | null
  pending_approval_count: number
}

export interface ApprovalResponseRequest {
  request_id: string
  decision: ApprovalDecision
  content?: Record<string, unknown> | null
}

export type ApprovalFormMode = 'none' | 'structured' | 'json'
export type ApprovalFormFieldKind = 'text' | 'number' | 'boolean' | 'select' | 'multiselect'

export interface ApprovalFormOption {
  label: string
  value: string
}

export interface ApprovalFormFieldState {
  id: string
  label: string
  prompt: string
  kind: ApprovalFormFieldKind
  required: boolean
  value: string | boolean | string[] | null
  options?: ApprovalFormOption[]
  min?: number | null
  max?: number | null
  integer?: boolean
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
    raw?: ToolRawPayload | Record<string, unknown>
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

export interface BackgroundCommandSessionSummary {
  session_id: string
  command: string
  cwd: string
  state: string
  exit_code: number | null
  runtime_seconds: number
}

export interface BackgroundCommandListRawPayload {
  kind: 'background_command'
  sessions: BackgroundCommandSessionSummary[]
}

export interface FileReadLineRecord {
  number: number
  text: string
}

export interface FileReadRawPayload {
  kind: 'file_read'
  path: string
  start_line: number
  end_line: number
  max_chars: number
  truncated: boolean
  lines: FileReadLineRecord[]
}

export interface FileListEntry {
  path: string
  type: 'directory' | 'file'
}

export interface FileListRawPayload {
  kind: 'file_list'
  path: string
  recursive: boolean
  include_hidden: boolean
  max_entries: number
  entries: FileListEntry[]
}

export interface FileSearchMatch {
  path: string
  line_number: number
  text: string
}

export interface FileSearchRawPayload {
  kind: 'file_search'
  path: string
  pattern: string
  regex: boolean
  case_sensitive: boolean
  include_hidden: boolean
  matches: FileSearchMatch[]
}

export interface FileWriteRawPayload {
  kind: 'file_write'
  path: string
  append: boolean
  create_directories: boolean
  chars_written: number
}

export interface FileReplaceRawPayload {
  kind: 'file_replace'
  path: string
  replaced_count: number
  replace_all: boolean
  old_text: string
  new_text: string
}

export interface SkillLoadRawPayload {
  kind: 'skill_load'
  name: string
  description: string
  location: string
  directory: string
  sampled_files: string[]
  sampled_file_locations: string[]
}

export interface TodoStatusCounts {
  pending: number
  in_progress: number
  completed: number
}

export interface TodoRecord {
  id?: string | null
  content: string
  activeForm: string
  status: 'pending' | 'in_progress' | 'completed'
}

export interface TodoListRawPayload {
  kind: 'todo_list'
  session_id: string
  total: number
  status_counts: TodoStatusCounts
  todos: TodoRecord[]
}

export interface TodoUpdateRawPayload {
  kind: 'todo_update'
  session_id: string
  total: number
  status_counts: TodoStatusCounts
  todos: TodoRecord[]
}

export interface McpContentItem {
  index: number
  kind: string
  text?: string | null
  mime_type?: string | null
  uri?: string | null
  data?: string | null
}

export interface McpToolResultRawPayload {
  kind: 'mcp_tool_result'
  tool_name: string
  arguments: Record<string, unknown>
  is_error: boolean
  contents: McpContentItem[]
}

export interface SubagentFinalMessage {
  role?: string
  content?: string | null
  reasoning_content?: string | null
}

export interface SubagentResultRawPayload {
  kind: 'subagent_result'
  sub_session_id: string
  finish_reason: string
  message_count: number
  tool_summary: string
  final_message: SubagentFinalMessage
}

export type ToolRawPayload =
  | ShellRawPayload
  | BackgroundCommandListRawPayload
  | FileReadRawPayload
  | FileListRawPayload
  | FileSearchRawPayload
  | FileWriteRawPayload
  | FileReplaceRawPayload
  | SkillLoadRawPayload
  | TodoListRawPayload
  | TodoUpdateRawPayload
  | McpToolResultRawPayload
  | SubagentResultRawPayload

export type ToolDigestRawPayload = Exclude<ToolRawPayload, ShellRawPayload>

export interface ToolActivityState {
  raw: ToolDigestRawPayload | null
  metadata: Record<string, unknown>
  arguments: Record<string, unknown>
  result: string
  is_error: boolean
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
    item_id?: string
  }
}

export interface ChatPlanEvent {
  event: 'plan'
  data: {
    session_id: string
    content: string
    iteration: number
    item_id?: string
  }
}

export interface ChatAssistantEvent {
  event: 'assistant_message'
  data: {
    session_id: string
    content: string
    iteration: number
    item_id?: string
  }
}

export interface ChatAssistantDeltaEvent {
  event: 'assistant_delta'
  data: {
    session_id: string
    item_id: string
    delta: string
  }
}

export interface ChatApprovalRequestedEvent {
  event: 'approval_requested'
  data: {
    session_id: string
    request_id: string
    method: string
    kind: string
    title: string
    detail: string
    options: ApprovalOption[]
    payload: Record<string, unknown>
    item_id?: string | null
  }
}

export interface ChatApprovalResolvedEvent {
  event: 'approval_resolved'
  data: {
    session_id: string
    request_id: string
    decision: string
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

export interface ChatTurnCompletedEvent {
  event: 'turn_completed'
  data: {
    session_id: string
    turn_id?: string
    status: string
  }
}

export interface ChatTurnAbortedEvent {
  event: 'turn_aborted'
  data: {
    session_id: string
    turn_id?: string
    status: string
    reason?: string | null
  }
}

export interface ChatStreamErrorEvent {
  event: 'stream_error'
  data: {
    session_id: string
    message: string
    thread_id?: string
    turn_id?: string
    code?: string | null
    will_retry?: boolean
  }
}

export interface ChatStreamDoneEvent {
  event: 'done'
  data: {
    session_id: string
    finish_reason: string
  }
}

export interface ChannelAccountStateEvent {
  event: 'channel_account_state'
  data: ChannelAccountSummary
}

export interface ChannelInboundMessageEvent {
  event: 'channel_inbound_message'
  data: {
    platform: string
    account_id: string
    peer_id: string
    session_id: string
    content: string
    timestamp_ms: number
  }
}

export interface ChannelOutboundMessageEvent {
  event: 'channel_outbound_message'
  data: {
    platform: string
    account_id: string
    peer_id: string
    content: string
    message_id: string
    timestamp_ms: number
  }
}

export interface ChannelErrorEvent {
  event: 'channel_error'
  data: {
    platform: string
    account_id: string
    message: string
  }
}

export interface ChannelLoginQrEvent {
  event: 'channel_login_qr'
  data: {
    platform: string
    account_id: string
    qrcode_url?: string
    status: string
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
  | ChatPlanEvent
  | ChatAssistantDeltaEvent
  | ChatAssistantEvent
  | ChatApprovalRequestedEvent
  | ChatApprovalResolvedEvent
  | ChatErrorEvent
  | ChatTurnCompletedEvent
  | ChatTurnAbortedEvent
  | ChatStreamErrorEvent
  | ChatStreamDoneEvent
  | ChannelAccountStateEvent
  | ChannelInboundMessageEvent
  | ChannelOutboundMessageEvent
  | ChannelErrorEvent
  | ChannelLoginQrEvent

export interface UiChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  source: 'chat' | 'channel'
  channelMeta?: ChannelMeta | null
  draftId?: string | null
}

export interface ApprovalActivityState {
  requestId: string
  method: string
  kind: string
  options: ApprovalOption[]
  payload: Record<string, unknown>
  formMode: ApprovalFormMode
  formFields: ApprovalFormFieldState[]
  responseDraft: string
  validationError?: string | null
  submittedDecision?: ApprovalDecision | null
}

export interface ChatActivity {
  id: string
  kind: 'status' | 'reasoning' | 'plan' | 'tool' | 'command' | 'background' | 'approval'
  title: string
  detail: string
  state: 'running' | 'done' | 'error' | 'info' | 'queued'
  command: string
  cwd: string
  stdout: string
  stderr: string
  meta: string[]
  shell: ShellActivityState | null
  tool: ToolActivityState | null
  approval?: ApprovalActivityState | null
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
