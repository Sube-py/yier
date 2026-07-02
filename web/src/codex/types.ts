export type JsonRecord = Record<string, unknown>

export type CodexWorkMode = 'build' | 'plan'

export interface CodexPromptSubmission {
  prompt: string
  model?: string | null
  reasoningEffort?: string | null
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

export interface CodexWorkspaceResponse {
  projects: CodexProjectGroup[]
  paired_editors?: JsonRecord[]
  remote_connections?: CodexRemoteConnection[]
  active_remote_connection_id?: string
  remote_connection_statuses?: Record<string, CodexRemoteConnectionStatus>
}

export interface CodexRemoteConnection {
  id: string
  display_name: string
  ssh_host: string
  ssh_port?: number | null
  ssh_alias: string
  identity_file: string
  remote_path: string
  auto_connect: boolean
}

export type CodexRemoteConnectionRuntimeStatus =
  | 'connected'
  | 'connecting'
  | 'disconnected'
  | 'error'

export interface CodexRemoteConnectionStatus {
  status: CodexRemoteConnectionRuntimeStatus
  detail?: string
}

export interface CodexRemoteConnectionPayload {
  display_name: string
  ssh_host: string
  ssh_port?: number | null
  ssh_alias: string
  identity_file: string
  remote_path: string
  auto_connect: boolean
}

export interface CodexRemoteConnectionsResponse {
  connections: CodexRemoteConnection[]
  active_connection_id: string
  statuses?: Record<string, CodexRemoteConnectionStatus>
}

export interface CodexRemoteConnectionResponse {
  connection: CodexRemoteConnection
}

export interface CodexRemoteConnectionTestResponse {
  ok: boolean
  detail: string
}

export interface CodexRemoteConnectionChatGptLoginResponse {
  ok: boolean
  auth_url: string
  login_id: string
  detail: string
}

export type CodexFilesystemEntryKind = 'directory' | 'file' | 'other'

export interface CodexFilesystemEntry {
  name: string
  path: string
  kind: CodexFilesystemEntryKind
  extension: string
  readable: boolean
}

export interface CodexFilesystemResponse {
  path: string
  parent_path?: string | null
  roots: CodexFilesystemEntry[]
  entries: CodexFilesystemEntry[]
}

export interface CodexCollaborationMode extends JsonRecord {
  mode?: string
  settings?: {
    model?: string | null
    reasoning_effort?: string | null
    developer_instructions?: string | null
  } | null
}

export interface CodexTurnState extends JsonRecord {
  turnId?: string | null
  status?: string
  items?: JsonRecord[]
  turnStartedAtMs?: number | null
  finalAssistantStartedAtMs?: number | null
  durationMs?: number | null
  error?: unknown
}

export interface CodexRequestOption {
  label: string
  description?: string
}

export interface CodexRequestQuestion extends JsonRecord {
  id: string
  header?: string
  question?: string
  isOther?: boolean
  isSecret?: boolean
  options?: CodexRequestOption[]
}

export interface CodexPendingRequest extends JsonRecord {
  id: string
  method: string
  params?: {
    threadId?: string
    turnId?: string
    itemId?: string
    questions?: CodexRequestQuestion[]
    [key: string]: unknown
  }
}

export interface CodexQueuedFollowup extends JsonRecord {
  id?: string
  text?: string
  prompt?: string
  createdAt?: number
}

export type CodexThreadGoalStatus =
  | 'active'
  | 'paused'
  | 'blocked'
  | 'usageLimited'
  | 'budgetLimited'
  | 'complete'

export interface CodexThreadGoal extends JsonRecord {
  threadId?: string
  thread_id?: string
  objective: string
  status: CodexThreadGoalStatus | string
  tokenBudget?: number | null
  tokensUsed?: number
  timeUsedSeconds?: number
  createdAt?: number
  updatedAt?: number
}

export interface CodexConversationState extends JsonRecord {
  id?: string
  hostId?: string
  turns?: CodexTurnState[]
  requests?: CodexPendingRequest[]
  createdAt?: number
  updatedAt?: number
  title?: string | null
  latestModel?: string | null
  latestReasoningEffort?: string | null
  latestCollaborationMode?: CodexCollaborationMode | null
  threadRuntimeStatus?: JsonRecord | string | null
  threadGoal?: CodexThreadGoal | null
  completedThreadGoal?: CodexThreadGoal | null
  threadGoalResumeConfirmation?: JsonRecord | null
  cwd?: string | null
  source?: string | null
  archived?: boolean
  queuedFollowups?: CodexQueuedFollowup[]
}

export interface CodexThreadStatePayload {
  thread_id: string
  state: CodexConversationState | null
  stream_role?: JsonRecord | null
  queued_followups?: CodexQueuedFollowup[]
}

export interface CodexThreadCreateResponse {
  thread_id: string
  state?: CodexConversationState | null
}

export interface CodexThreadForkResponse {
  thread_id: string
  state?: CodexConversationState | null
}

export type CodexClientCommand =
  | 'list_threads'
  | 'subscribe_thread'
  | 'unsubscribe_thread'
  | 'start_thread'
  | 'send_prompt'
  | 'steer_prompt'
  | 'enqueue_followup'
  | 'remove_followup'
  | 'interrupt_turn'
  | 'compact_thread'
  | 'set_thread_goal'
  | 'get_thread_goal'
  | 'clear_thread_goal'
  | 'set_collaboration_mode'
  | 'submit_user_input_response'
  | 'rename_thread'
  | 'archive_thread'
  | 'fork_thread'
  | 'unarchive_thread'
  | 'activate_remote_connection'

export type CodexServerEventType =
  | 'connection_ready'
  | 'workspace'
  | 'thread_snapshot'
  | 'thread_state'
  | 'thread_archived'
  | 'thread_unarchived'

export interface CodexAckEnvelope<TPayload = unknown> {
  id: string
  type: 'ack'
  ok: true
  payload: TPayload
}

export interface CodexErrorEnvelope {
  id?: string
  type: 'error'
  code: string
  message: string
}

export interface CodexServerEvent<TPayload = unknown> {
  id?: string
  type: CodexServerEventType
  payload: TPayload
}

export type CodexSocketMessage =
  | CodexAckEnvelope
  | CodexErrorEnvelope
  | CodexServerEvent

export type CodexSocketStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error'
