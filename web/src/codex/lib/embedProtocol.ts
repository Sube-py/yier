import type {
  CodexPromptSubmission,
  CodexThreadGoalStatus,
  CodexTurnState,
  CodexWorkMode,
  JsonRecord,
} from '../types'

export const embedCommandTypes = [
  'yier:codex-start',
  'yier:codex-resume',
  'yier:codex-send-prompt',
  'yier:codex-steer-prompt',
  'yier:codex-enqueue-followup',
  'yier:codex-remove-followup',
  'yier:codex-interrupt-turn',
  'yier:codex-compact-thread',
  'yier:codex-set-mode',
  'yier:codex-set-goal',
  'yier:codex-update-goal-status',
  'yier:codex-clear-goal',
  'yier:codex-submit-user-input',
  'yier:codex-rename-thread',
  'yier:codex-archive-thread',
  'yier:codex-fork-thread',
] as const

export type EmbedCommandType = (typeof embedCommandTypes)[number]

export type EmbedMessageType =
  | 'yier:codex-ready'
  | 'yier:codex-thread-created'
  | 'yier:codex-thread-resumed'
  | 'yier:codex-prompt-sent'
  | 'yier:codex-command-result'
  | 'yier:codex-status'
  | 'yier:codex-turn-state'
  | 'yier:codex-goal-state'
  | 'yier:codex-mode-changed'
  | 'yier:codex-user-input-request'
  | 'yier:codex-followups-changed'
  | 'yier:codex-error'

export type EmbedWorkStatus =
  | 'idle'
  | 'planning'
  | 'running'
  | 'awaiting_approval'
  | 'done'
  | 'failed'

export interface EmbedCommand extends JsonRecord {
  type: EmbedCommandType
  commandId?: unknown
  cwd?: unknown
  threadId?: unknown
  thread_id?: unknown
  mode?: unknown
  prompt?: unknown
  goal?: unknown
  objective?: unknown
  tokenBudget?: unknown
  token_budget?: unknown
  status?: unknown
  requestId?: unknown
  request_id?: unknown
  response?: unknown
  messageId?: unknown
  message_id?: unknown
  name?: unknown
  model?: unknown
  reasoningEffort?: unknown
  reasoning_effort?: unknown
  attachments?: unknown
  approvalPolicy?: unknown
  approval_policy?: unknown
  approvalsReviewer?: unknown
  approvals_reviewer?: unknown
  sandboxPolicy?: unknown
  sandbox_policy?: unknown
}

export interface EmbedTurnState extends JsonRecord {
  turnId: string
  status: string
  turnStartedAtMs: number | null
  firstTurnWorkItemStartedAtMs: number | null
  finalAssistantStartedAtMs: number | null
  durationMs: number | null
  error: unknown
}

const commandTypeSet = new Set<string>(embedCommandTypes)
const goalStatusSet = new Set<CodexThreadGoalStatus>([
  'active',
  'paused',
  'blocked',
  'usageLimited',
  'budgetLimited',
  'complete',
])

export function parseEmbedCommand(value: unknown): EmbedCommand | null {
  if (!isRecord(value) || typeof value.type !== 'string' || !commandTypeSet.has(value.type)) {
    return null
  }
  return value as EmbedCommand
}

export function cloneEmbedMessage(type: EmbedMessageType, payload: JsonRecord): JsonRecord {
  return JSON.parse(JSON.stringify({ type, ...payload })) as JsonRecord
}

export function embedText(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value.trim()
    }
  }
  return ''
}

export function embedPositiveInteger(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
      return Math.floor(value)
    }
  }
  return null
}

export function embedRecord(value: unknown): JsonRecord | null {
  return isRecord(value) ? value : null
}

export function normalizeEmbedMode(value: unknown): { mode: CodexWorkMode | null; error: string } {
  const mode = embedText(value).toLowerCase()
  if (!mode) {
    return { mode: null, error: '' }
  }
  if (mode === 'build' || mode === 'plan') {
    return { mode, error: '' }
  }
  return { mode: null, error: 'mode must be build or plan.' }
}

export function normalizeGoalStatus(value: unknown): {
  status: CodexThreadGoalStatus | null
  error: string
} {
  const status = embedText(value) as CodexThreadGoalStatus
  if (!status) {
    return { status: null, error: 'goal status is required.' }
  }
  if (goalStatusSet.has(status)) {
    return { status, error: '' }
  }
  return { status: null, error: `unsupported goal status: ${status}.` }
}

export function promptSubmissionFromCommand(command: EmbedCommand): CodexPromptSubmission {
  const attachments = Array.isArray(command.attachments) ? command.attachments.filter(isRecord) : []
  return {
    prompt: embedText(command.prompt),
    model: nullableText(command.model),
    reasoningEffort: nullableText(command.reasoningEffort, command.reasoning_effort),
    attachments,
    approvalPolicy: nullableText(command.approvalPolicy, command.approval_policy),
    approvalsReviewer: nullableText(command.approvalsReviewer, command.approvals_reviewer),
    sandboxPolicy: embedRecord(command.sandboxPolicy ?? command.sandbox_policy),
  }
}

export function latestTurnState(turns: CodexTurnState[] | undefined): EmbedTurnState | null {
  const turn = Array.isArray(turns) && turns.length ? turns[turns.length - 1] : null
  if (!turn) {
    return null
  }
  return {
    turnId: embedText(turn.turnId, turn.id),
    status: embedText(turn.status) || 'idle',
    turnStartedAtMs: finiteNumber(turn.turnStartedAtMs),
    firstTurnWorkItemStartedAtMs: finiteNumber(turn.firstTurnWorkItemStartedAtMs),
    finalAssistantStartedAtMs: finiteNumber(turn.finalAssistantStartedAtMs),
    durationMs: finiteNumber(turn.durationMs),
    error: turn.error ?? null,
  }
}

function nullableText(...values: unknown[]) {
  return embedText(...values) || null
}

function finiteNumber(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
