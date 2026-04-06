import type {
  ApprovalDecision,
  ApprovalFormFieldState,
  ChatActivity,
  CodexTurnTiming,
  FileChangeRecord,
} from '../../types/api'
import type { ActivityDisplayItem, TurnGroupEntry } from './types'

const HIDDEN_TOOL_TITLES = new Set([
  'start_background_command',
  'start_codex_background_session',
  'list_background_commands',
  'read_background_command',
  'resume_codex_background_session',
  'wait_background_command',
  'stop_background_command',
  'send_background_command_input',
  'start background command',
  'start codex background session',
  'list background commands',
  'read background command',
  'resume codex background session',
  'wait background command',
  'stop background command',
  'send background command input',
])

const CODEX_BACKGROUND_TOOL_NAMES = new Set([
  'start_codex_background_session',
  'resume_codex_background_session',
])

export function runtimeStatusLabel(status: string | null | undefined) {
  const normalized = (status ?? '').trim()
  if (!normalized || normalized === 'idle') {
    return 'Ready'
  }
  if (normalized === 'active') {
    return 'Working'
  }
  if (normalized === 'completed') {
    return 'Completed'
  }
  if (normalized === 'interrupted') {
    return 'Aborted'
  }
  if (normalized === 'error' || normalized === 'failed') {
    return 'Error'
  }

  return normalized
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(' ')
}

export function shellCommand(activity: ChatActivity) {
  return activity.shell && typeof activity.shell.request.command === 'string'
    ? activity.shell.request.command
    : activity.command
}

export function shellCwd(activity: ChatActivity) {
  return activity.shell && typeof activity.shell.request.cwd === 'string'
    ? activity.shell.request.cwd
    : activity.cwd
}

export function isShellActivity(activity: ChatActivity) {
  return Boolean(activity.shell)
}

export function hasShellTranscript(activity: ChatActivity) {
  return Boolean(activity.shell?.events.length)
}

export function shellOutputTranscript(activity: ChatActivity) {
  if (!activity.shell?.events.length) {
    return ''
  }

  const lines: string[] = []
  for (const event of activity.shell.events) {
    if (event.type === 'started' || event.type === 'state_changed' || event.type === 'exit') {
      continue
    }
    if (!event.text) {
      continue
    }

    if (event.type === 'stdin') {
      lines.push(`> ${event.text.replace(/\n$/, '')}`)
      continue
    }

    lines.push(event.text.replace(/\n$/, ''))
  }

  return lines.filter((line) => line.length > 0).join('\n')
}

export function shellRuntime(activity: ChatActivity) {
  if (!activity.shell?.process) {
    return ''
  }
  return `${activity.shell.process.runtime_seconds}s`
}

export function activityUsesMarkdown(activity: ChatActivity) {
  return activity.kind === 'reasoning' || activity.kind === 'plan'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function codexBackgroundToolName(activity: ChatActivity) {
  const toolName =
    activity.shell && typeof activity.shell.request.background_tool_name === 'string'
      ? activity.shell.request.background_tool_name
      : null
  if (toolName && CODEX_BACKGROUND_TOOL_NAMES.has(toolName)) {
    return toolName
  }
  return null
}

function isCodexBackgroundActivity(activity: ChatActivity) {
  return Boolean(codexBackgroundToolName(activity))
}

function codexBackgroundSummary(activity: ChatActivity) {
  const toolName = codexBackgroundToolName(activity)
  const sessionId = activity.shell?.process?.session_id || activity.shell?.session_id || ''
  const action = toolName === 'resume_codex_background_session' ? 'Resume' : 'Start'
  const detail = activity.detail.trim()
  return {
    verb: sessionId ? `${action} ${sessionId}` : action,
    text: detail || (activity.state === 'error' ? '失败了' : '思考中'),
    verbClass: activityToneClass(activity),
  }
}

export function isFileChangeRecord(value: unknown): value is FileChangeRecord {
  if (!isRecord(value) || typeof value.path !== 'string' || typeof value.diff !== 'string') {
    return false
  }
  if (!isRecord(value.kind) || typeof value.kind.type !== 'string') {
    return false
  }
  return value.kind.move_path === null || typeof value.kind.move_path === 'string'
}

export function fileChangeRecords(activity: ChatActivity): FileChangeRecord[] {
  const tool = activity.tool
  if (!tool || tool.tool_name !== 'file_change') {
    return []
  }

  const value = tool.metadata.changes
  if (!Array.isArray(value)) {
    return []
  }

  return value.filter(isFileChangeRecord)
}

export function fileChangeKindLabel(change: FileChangeRecord) {
  switch (change.kind.type) {
    case 'create':
      return 'Created'
    case 'delete':
      return 'Deleted'
    case 'move':
      return 'Moved'
    case 'update':
      return 'Updated'
    default:
      return 'Changed'
  }
}

export function fileChangeVerb(change: FileChangeRecord) {
  switch (change.kind.type) {
    case 'create':
      return 'Created'
    case 'delete':
      return 'Deleted'
    case 'move':
      return 'Moved'
    case 'update':
      return 'Edited'
    default:
      return 'Changed'
  }
}

export function fileChangeMetaLabel(change: FileChangeRecord) {
  if (change.kind.type === 'move' && change.kind.move_path) {
    return change.kind.move_path
  }
  return ''
}

export function fileBasename(path: string) {
  const normalized = path.trim()
  if (!normalized) {
    return 'Untitled'
  }

  const segments = normalized.split(/[\\/]/).filter(Boolean)
  return segments[segments.length - 1] ?? normalized
}

export function fileChangeStats(diff: string) {
  let additions = 0
  let removals = 0

  for (const line of diff.split('\n')) {
    if (!line) {
      continue
    }
    if (line.startsWith('+++') || line.startsWith('---')) {
      continue
    }
    if (line.startsWith('+')) {
      additions += 1
      continue
    }
    if (line.startsWith('-')) {
      removals += 1
    }
  }

  return { additions, removals }
}

export function fileChangeSummary(change: FileChangeRecord) {
  const stats = fileChangeStats(change.diff)
  return {
    label: fileBasename(change.path),
    additions: stats.additions,
    removals: stats.removals,
  }
}

export function fileChangeVerbClass(change: FileChangeRecord) {
  switch (change.kind.type) {
    case 'create':
    case 'update':
      return 'text-[#4b8b58]'
    case 'delete':
      return 'text-[#b85d48]'
    case 'move':
      return 'text-[color:var(--app-accent)]'
    default:
      return 'text-[color:var(--app-accent-deep)]'
  }
}

function activityToneClass(activity: ChatActivity) {
  if (activity.state === 'error') {
    return 'text-[#b85d48]'
  }
  if (activity.state === 'running' || activity.state === 'queued') {
    return 'text-[color:var(--app-accent)]'
  }
  if (activity.state === 'done') {
    return 'text-[#4b8b58]'
  }
  return 'text-[color:var(--app-text-soft)]'
}

function splitSummary(summary: string, fallbackVerb: string, activity: ChatActivity) {
  const trimmed = summary.trim()
  if (!trimmed) {
    return {
      verb: fallbackVerb,
      text: activity.title.trim() || 'Activity',
      verbClass: activityToneClass(activity),
    }
  }

  const match = trimmed.match(/^([A-Za-z][A-Za-z-]*)\s+(.+)$/)
  if (!match) {
    return {
      verb: fallbackVerb,
      text: trimmed,
      verbClass: activityToneClass(activity),
    }
  }

  return {
    verb: match[1] ?? fallbackVerb,
    text: match[2] ?? trimmed,
    verbClass: activityToneClass(activity),
  }
}

export function activitySummaryParts(display: ActivityDisplayItem, isSending = false) {
  const { activity, change } = display

  if (change) {
    const { additions, removals } = fileChangeStats(change.diff)
    const hasStats = change.diff.trim().length > 0
    return {
      verb: fileChangeVerb(change),
      text: `${fileBasename(change.path)}${hasStats ? ` +${additions} -${removals}` : ''}`,
      verbClass: fileChangeVerbClass(change),
    }
  }

  if (isShellActivity(activity)) {
    if (isCodexBackgroundActivity(activity)) {
      return codexBackgroundSummary(activity)
    }

    const command = shellCommand(activity) || activity.title || 'command'
    const isBackground = activity.shell?.kind === 'background_command' || activity.kind === 'background'

    if (activity.state === 'running') {
      return {
        verb: isBackground ? 'Running background terminal' : 'Running command',
        text: command,
        verbClass: 'text-[color:var(--app-accent)]',
      }
    }

    if (activity.state === 'error') {
      return {
        verb: isBackground ? 'Background terminal failed with' : 'Command failed',
        text: command,
        verbClass: 'text-[#b85d48]',
      }
    }

    if (isBackground && !isSending) {
      return {
        verb: 'Background terminal finished with',
        text: command,
        verbClass: 'text-[#7a6b4e]',
      }
    }

    return {
      verb: 'Ran',
      text: command,
      verbClass: 'text-[#4b8b58]',
    }
  }

  if (activity.kind === 'approval') {
    return {
      verb: activity.state === 'done' ? 'Resolved' : 'Approval requested',
      text: approvalMessage(activity) || activity.title || 'Approval',
      verbClass: activityToneClass(activity),
    }
  }

  if (activity.kind === 'reasoning' || activity.kind === 'plan') {
    return {
      verb: activity.kind === 'reasoning' ? 'Reasoning' : 'Plan',
      text: activity.title || 'Details',
      verbClass: activityToneClass(activity),
    }
  }

  return splitSummary(activity.detail || activity.title, 'Updated', activity)
}

export function activitySummaryText(display: ActivityDisplayItem, isSending = false) {
  const summary = activitySummaryParts(display, isSending)
  return `${summary.verb} ${summary.text}`.trim()
}

export function isHiddenActivity(activity: ChatActivity, showReasoningCards = false) {
  if (activity.kind === 'approval') {
    return false
  }

  if (activity.kind === 'reasoning' && !showReasoningCards) {
    return true
  }

  if (activity.kind === 'status' && activity.title === 'Thinking') {
    return true
  }

  if (activity.kind === 'tool' && HIDDEN_TOOL_TITLES.has(activity.title)) {
    return true
  }

  return false
}

export function isApprovalActivity(activity: ChatActivity) {
  return activity.kind === 'approval' && Boolean(activity.approval)
}

function approvalRequest(activity: ChatActivity) {
  const request = activity.approval?.payload.request
  if (!request || typeof request !== 'object' || Array.isArray(request)) {
    return null
  }
  return request as Record<string, unknown>
}

export function approvalUsesStructuredForm(activity: ChatActivity) {
  return activity.approval?.formMode === 'structured' && activity.approval.formFields.length > 0
}

export function approvalUsesJsonFallback(activity: ChatActivity) {
  return activity.approval?.formMode === 'json'
}

export function approvalHasUrl(activity: ChatActivity) {
  const url = approvalRequest(activity)?.url
  return typeof url === 'string' && url.length > 0
}

export function approvalUrl(activity: ChatActivity) {
  const url = approvalRequest(activity)?.url
  return typeof url === 'string' ? url : ''
}

export function approvalMessage(activity: ChatActivity) {
  const message = approvalRequest(activity)?.message
  return typeof message === 'string' ? message : ''
}

export function approvalSchemaPreview(activity: ChatActivity) {
  const schema = approvalRequest(activity)?.requestedSchema
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) {
    return ''
  }
  return JSON.stringify(schema, null, 2)
}

export function approvalFieldPrompt(field: ApprovalFormFieldState) {
  return field.prompt && field.prompt !== field.label ? field.prompt : ''
}

export function approvalFieldValue(field: ApprovalFormFieldState) {
  if (field.kind === 'multiselect') {
    return Array.isArray(field.value) ? field.value : []
  }
  if (field.kind === 'boolean') {
    return typeof field.value === 'boolean' ? String(field.value) : ''
  }
  return typeof field.value === 'string' ? field.value : ''
}

export function updateApprovalFieldValue(field: ApprovalFormFieldState, value: string) {
  if (field.kind === 'boolean') {
    field.value = value === 'true' ? true : value === 'false' ? false : null
    return
  }
  field.value = value
}

export function updateApprovalMultiSelect(field: ApprovalFormFieldState, select: HTMLSelectElement) {
  field.value = Array.from(select.selectedOptions).map((option) => option.value)
}

export function clearApprovalValidation(activity: ChatActivity) {
  if (!activity.approval) {
    return
  }
  activity.approval.validationError = null
}

function approvalFieldContent(field: ApprovalFormFieldState) {
  if (field.kind === 'text') {
    const value = typeof field.value === 'string' ? field.value.trim() : ''
    if (!value) {
      return field.required ? { ok: false as const, error: `${field.label} is required.` } : { ok: true as const, value: undefined }
    }
    return { ok: true as const, value }
  }

  if (field.kind === 'number') {
    const value = typeof field.value === 'string' ? field.value.trim() : ''
    if (!value) {
      return field.required ? { ok: false as const, error: `${field.label} is required.` } : { ok: true as const, value: undefined }
    }

    const parsed = Number(value)
    if (!Number.isFinite(parsed)) {
      return { ok: false as const, error: `${field.label} must be a valid number.` }
    }
    if (field.integer && !Number.isInteger(parsed)) {
      return { ok: false as const, error: `${field.label} must be an integer.` }
    }
    if (typeof field.min === 'number' && parsed < field.min) {
      return { ok: false as const, error: `${field.label} must be at least ${field.min}.` }
    }
    if (typeof field.max === 'number' && parsed > field.max) {
      return { ok: false as const, error: `${field.label} must be at most ${field.max}.` }
    }
    return { ok: true as const, value: parsed }
  }

  if (field.kind === 'boolean') {
    if (typeof field.value !== 'boolean') {
      return field.required ? { ok: false as const, error: `${field.label} is required.` } : { ok: true as const, value: undefined }
    }
    return { ok: true as const, value: field.value }
  }

  if (field.kind === 'select') {
    const value = typeof field.value === 'string' ? field.value : ''
    if (!value) {
      return field.required ? { ok: false as const, error: `${field.label} is required.` } : { ok: true as const, value: undefined }
    }
    return { ok: true as const, value }
  }

  if (field.kind === 'multiselect') {
    const value = Array.isArray(field.value) ? field.value.filter((item) => typeof item === 'string') : []
    if (!value.length) {
      return field.required ? { ok: false as const, error: `${field.label} is required.` } : { ok: true as const, value: undefined }
    }
    return { ok: true as const, value }
  }

  return { ok: true as const, value: undefined }
}

export function approvalContentText(activity: ChatActivity) {
  if (!activity.approval) {
    return ''
  }

  if (!approvalUsesStructuredForm(activity)) {
    return activity.approval.responseDraft
  }

  const content: Record<string, unknown> = {}
  for (const field of activity.approval.formFields) {
    const result = approvalFieldContent(field)
    if (!result.ok) {
      activity.approval.validationError = result.error
      return ''
    }
    if (result.value !== undefined) {
      content[field.id] = result.value
    }
  }

  activity.approval.validationError = null
  return JSON.stringify(content)
}

export function approvalSubmitPayload(activity: ChatActivity, decision: ApprovalDecision) {
  if (!activity.approval) {
    return null
  }

  const contentText = approvalContentText(activity)
  if (approvalUsesStructuredForm(activity) && activity.approval.validationError) {
    return null
  }

  return {
    requestId: activity.approval.requestId,
    decision,
    contentText,
  }
}

export function hasActivityDetails(display: ActivityDisplayItem) {
  const { activity, change } = display
  if (change) {
    return Boolean(change.diff || fileChangeMetaLabel(change))
  }
  if (isShellActivity(activity)) {
    return Boolean(
      shellCommand(activity) ||
      hasShellTranscript(activity) ||
      activity.stdout ||
      activity.stderr ||
      activity.meta.length,
    )
  }
  if (activityUsesMarkdown(activity) || isApprovalActivity(activity)) {
    return Boolean(activity.detail || activity.approval)
  }
  return Boolean(
    activity.detail.trim() ||
    activity.command ||
    activity.cwd ||
    activity.stdout ||
    activity.stderr ||
    activity.meta.length,
  )
}

export function genericActivityDetail(activity: ChatActivity) {
  const detail = activity.detail.trim()
  if (!detail || activityUsesMarkdown(activity) || isApprovalActivity(activity) || isShellActivity(activity)) {
    return ''
  }

  if (detail === activity.title.trim()) {
    return ''
  }

  return detail
}

export function formatDurationLabel(totalSeconds: number) {
  const roundedSeconds = Math.max(0, Math.round(totalSeconds))
  const minutes = Math.floor(roundedSeconds / 60)
  const seconds = roundedSeconds % 60

  if (minutes <= 0) {
    return `${seconds}s`
  }

  return `${minutes}m ${seconds}s`
}

export function turnGroupDurationSeconds(group: TurnGroupEntry, turnTimings?: CodexTurnTiming[]) {
  const turnTiming = turnTimings?.[group.turnIndex]
  const turnStartedAtMs = turnTiming?.turn_started_at_ms
  const finalAssistantStartedAtMs = turnTiming?.final_assistant_started_at_ms

  if (
    typeof turnStartedAtMs === 'number' &&
    Number.isFinite(turnStartedAtMs) &&
    typeof finalAssistantStartedAtMs === 'number' &&
    Number.isFinite(finalAssistantStartedAtMs) &&
    finalAssistantStartedAtMs >= turnStartedAtMs
  ) {
    return (finalAssistantStartedAtMs - turnStartedAtMs) / 1000
  }

  let startedAt: number | null = null
  let finishedAt: number | null = null
  let maxRuntimeSeconds = 0

  for (const item of group.items) {
    if (item.type !== 'activity') {
      continue
    }

    const process = item.display.activity.shell?.process
    if (!process) {
      continue
    }

    if (Number.isFinite(process.runtime_seconds)) {
      maxRuntimeSeconds = Math.max(maxRuntimeSeconds, process.runtime_seconds)
    }

    if (Number.isFinite(process.started_at)) {
      startedAt = startedAt === null ? process.started_at : Math.min(startedAt, process.started_at)
    }

    const candidateFinishedAt =
      Number.isFinite(process.finished_at)
        ? process.finished_at
        : Number.isFinite(process.started_at) && Number.isFinite(process.runtime_seconds)
          ? process.started_at + process.runtime_seconds
          : null

    if (candidateFinishedAt !== null) {
      finishedAt = finishedAt === null ? candidateFinishedAt : Math.max(finishedAt, candidateFinishedAt)
    }
  }

  if (startedAt !== null && finishedAt !== null && finishedAt >= startedAt) {
    return Math.max(0, finishedAt - startedAt)
  }

  return maxRuntimeSeconds > 0 ? maxRuntimeSeconds : null
}

export function turnGroupSummary(group: TurnGroupEntry, turnTimings?: CodexTurnTiming[]) {
  const durationSeconds = turnGroupDurationSeconds(group, turnTimings)
  if (durationSeconds !== null && durationSeconds > 0) {
    return `Worked for ${formatDurationLabel(durationSeconds)}`
  }

  const count = group.items.length
  const noun = count === 1 ? 'update' : 'updates'
  return `Worked through ${count} ${noun}`
}
