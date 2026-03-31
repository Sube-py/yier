<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/common'
import Button from 'primevue/button'
import Fieldset from 'primevue/fieldset'
import ProgressSpinner from 'primevue/progressspinner'
import ScrollPanel from 'primevue/scrollpanel'
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'

import HighlightedCodeBlock from './HighlightedCodeBlock.vue'
import { resolveHighlightLanguage } from '../lib/codeHighlight'

import type {
  ApprovalDecision,
  ApprovalFormFieldState,
  BackendRuntime,
  ChatActivity,
  CodexTurnTiming,
  FileChangeRecord,
  UiChatMessage,
} from '../types/api'

interface MessageFeedItem {
  type: 'message'
  key: string
  sortOrder: number
  message: UiChatMessage
}

interface ActivityDisplayItem {
  key: string
  sortOrder: number
  activity: ChatActivity
  change?: FileChangeRecord
}

interface ActivityFeedItem {
  type: 'activity'
  key: string
  sortOrder: number
  display: ActivityDisplayItem
}

interface TurnGroupEntry {
  type: 'turn-group'
  key: string
  sortOrder: number
  turnIndex: number
  items: Array<ActivityFeedItem | MessageFeedItem>
}

interface ActivitySummaryParts {
  verb: string
  text: string
  verbClass: string
}

type FeedEntry = MessageFeedItem | ActivityFeedItem
type RenderEntry = FeedEntry | TurnGroupEntry

const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
})

const markdownCopyResetTimers = new WeakMap<HTMLButtonElement, number>()
const markdownCopyButtonLabel = 'Copy code block'
const markdownCopiedButtonLabel = 'Copied'
const markdownCopyButtonIcon = '<i class="pi pi-copy" aria-hidden="true"></i>'
const markdownCopiedButtonIcon = '<i class="pi pi-check" aria-hidden="true"></i>'

function highlightMarkdownCode(content: string, language = '') {
  const { requestedLanguage, highlightLanguage } = resolveHighlightLanguage(language)
  const escapedContent = markdown.utils.escapeHtml(content)

  if (!highlightLanguage || !hljs.getLanguage(highlightLanguage)) {
    return {
      classNames: requestedLanguage ? ['hljs', `language-${requestedLanguage}`] : [],
      content: escapedContent,
    }
  }

  return {
    classNames: ['hljs', `language-${requestedLanguage || highlightLanguage}`],
    content: hljs.highlight(content, { language: highlightLanguage, ignoreIllegals: true }).value,
  }
}

function renderMarkdownCodeBlock(
  content: string,
  languageClass = '',
  languageLabel = '',
) {
  const escapedLabel = languageLabel ? markdown.utils.escapeHtml(languageLabel) : ''
  return `
    <div class="markdown-code-block">
      <div class="markdown-code-toolbar">
        ${escapedLabel ? `<span class="markdown-code-language">${escapedLabel}</span>` : '<span></span>'}
        <button
          type="button"
          class="markdown-code-copy"
          data-copy-markdown-code
          aria-label="${markdownCopyButtonLabel}"
          title="${markdownCopyButtonLabel}"
        >
          ${markdownCopyButtonIcon}
        </button>
      </div>
      <pre><code${languageClass}>${content}</code></pre>
    </div>
  `
}

markdown.renderer.rules.fence = (tokens, idx) => {
  const token = tokens[idx]
  if (!token) {
    return ''
  }
  const info = token.info ? markdown.utils.unescapeAll(token.info).trim() : ''
  const language = info ? info.split(/\s+/g)[0] ?? '' : ''
  const highlightedCode = highlightMarkdownCode(token.content, language)
  const languageClasses = highlightedCode.classNames
  const languageClass = languageClasses.length
    ? ` class="${markdown.utils.escapeHtml(languageClasses.join(' '))}"`
    : ''
  return renderMarkdownCodeBlock(
    highlightedCode.content,
    languageClass,
    language,
  )
}

markdown.renderer.rules.code_block = (tokens, idx) => {
  const token = tokens[idx]
  return renderMarkdownCodeBlock(highlightMarkdownCode(token?.content ?? '').content, ' class="hljs"')
}

const props = defineProps<{
  messages: UiChatMessage[]
  activities: ChatActivity[]
  turnTimings?: CodexTurnTiming[]
  isSending: boolean
  sessionLabel: string
  sessionRuntime: BackendRuntime | null
  projectPath: string
  assistantLabel?: string
  bottomInset?: number
  compactHeader?: boolean
  showReasoningCards?: boolean
}>()

const emit = defineEmits<{
  approvalAction: [requestId: string, decision: ApprovalDecision, contentText: string]
}>()

const HIDDEN_TOOL_TITLES = new Set([
  'start_background_command',
  'list_background_commands',
  'read_background_command',
  'wait_background_command',
  'stop_background_command',
  'send_background_command_input',
  'start background command',
  'list background commands',
  'read background command',
  'wait background command',
  'stop background command',
  'send background command input',
])

const timelineBody = ref<HTMLElement | null>(null)
const shouldStickToBottom = ref(true)
const bottomThreshold = 72
const timelineScrollPt = computed(() => ({
  contentContainer: {
    class: 'h-full w-full',
  },
  content: {
    class: 'flex min-h-0 flex-col gap-4 pr-[0.35rem]',
    style: {
      paddingBottom: `${Math.max(props.bottomInset ?? 0, 0)}px`,
    },
    ref: (element: HTMLElement | null) => {
      timelineBody.value = element
    },
    onScroll: onTimelineScroll,
  },
}))

function runtimeStatusLabel(status: string | null | undefined) {
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

function messageFieldsetPt() {
  return {
    root: {
      class:
        'message-fieldset message-fieldset-user inline-block min-w-0 max-w-[min(40rem,78%)] max-[1023px]:max-w-[min(34rem,86%)] max-sm:max-w-[88%]',
    },
    legend: {
      class: 'message-fieldset-legend',
    },
    legendLabel: {
      class: 'message-fieldset-legend-label',
    },
    contentContainer: {
      class: 'message-fieldset-content-container',
    },
    contentWrapper: {
      class: 'message-fieldset-content-wrapper',
    },
    content: {
      class: 'message-fieldset-content',
    },
  }
}

function renderAssistantMessage(content: string) {
  return markdown.render(content)
}

function renderActivityMarkdown(content: string) {
  return markdown.render(content)
}

function shellCommand(activity: ChatActivity) {
  return activity.shell && typeof activity.shell.request.command === 'string'
    ? activity.shell.request.command
    : activity.command
}

function shellCwd(activity: ChatActivity) {
  return activity.shell && typeof activity.shell.request.cwd === 'string'
    ? activity.shell.request.cwd
    : activity.cwd
}

function isShellActivity(activity: ChatActivity) {
  return Boolean(activity.shell)
}

function hasShellTranscript(activity: ChatActivity) {
  return Boolean(activity.shell?.events.length)
}

function shellOutputTranscript(activity: ChatActivity) {
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

function shellRuntime(activity: ChatActivity) {
  if (!activity.shell?.process) {
    return ''
  }
  return `${activity.shell.process.runtime_seconds}s`
}

async function onMarkdownClick(event: MouseEvent) {
  const target = event.target
  if (!(target instanceof Element)) {
    return
  }

  const button = target.closest('[data-copy-markdown-code]')
  if (!(button instanceof HTMLButtonElement)) {
    return
  }

  const block = button.closest('.markdown-code-block')
  const codeElement = block?.querySelector('code')
  if (!(codeElement instanceof HTMLElement)) {
    return
  }

  await navigator.clipboard.writeText(codeElement.innerText)
  button.dataset.state = 'copied'
  button.innerHTML = markdownCopiedButtonIcon
  button.setAttribute('aria-label', markdownCopiedButtonLabel)
  button.setAttribute('title', markdownCopiedButtonLabel)

  const existingTimer = markdownCopyResetTimers.get(button)
  if (existingTimer) {
    window.clearTimeout(existingTimer)
  }

  const resetTimer = window.setTimeout(() => {
    delete button.dataset.state
    button.innerHTML = markdownCopyButtonIcon
    button.setAttribute('aria-label', markdownCopyButtonLabel)
    button.setAttribute('title', markdownCopyButtonLabel)
    markdownCopyResetTimers.delete(button)
  }, 1600)

  markdownCopyResetTimers.set(button, resetTimer)
}

function activityUsesMarkdown(activity: ChatActivity) {
  return activity.kind === 'reasoning' || activity.kind === 'plan'
}

function fileChangeRecords(activity: ChatActivity): FileChangeRecord[] {
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

function isFileChangeRecord(value: unknown): value is FileChangeRecord {
  if (!isRecord(value) || typeof value.path !== 'string' || typeof value.diff !== 'string') {
    return false
  }
  if (!isRecord(value.kind) || typeof value.kind.type !== 'string') {
    return false
  }
  return value.kind.move_path === null || typeof value.kind.move_path === 'string'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function fileChangeKindLabel(change: FileChangeRecord) {
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

function fileChangeVerb(change: FileChangeRecord) {
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

function fileChangeMetaLabel(change: FileChangeRecord) {
  if (change.kind.type === 'move' && change.kind.move_path) {
    return change.kind.move_path
  }
  return ''
}

function fileBasename(path: string) {
  const normalized = path.trim()
  if (!normalized) {
    return 'Untitled'
  }

  const segments = normalized.split(/[\\/]/).filter(Boolean)
  return segments[segments.length - 1] ?? normalized
}

function fileChangeStats(diff: string) {
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

function splitSummary(summary: string, fallbackVerb: string, activity: ChatActivity): ActivitySummaryParts {
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

function activitySummaryParts(display: ActivityDisplayItem): ActivitySummaryParts {
  const { activity, change } = display

  if (change) {
    const { additions, removals } = fileChangeStats(change.diff)
    const hasStats = change.diff.trim().length > 0
    return {
      verb: fileChangeVerb(change),
      text: `${fileBasename(change.path)}${hasStats ? ` +${additions} -${removals}` : ''}`,
      verbClass: 'text-[color:var(--app-accent-deep)]',
    }
  }

  if (isShellActivity(activity)) {
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

    if (isBackground && !props.isSending) {
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

function activitySummaryText(display: ActivityDisplayItem) {
  const summary = activitySummaryParts(display)
  return `${summary.verb} ${summary.text}`.trim()
}

function isHiddenActivity(activity: ChatActivity) {
  if (activity.kind === 'approval') {
    return false
  }

  if (activity.kind === 'reasoning' && !props.showReasoningCards) {
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

function isApprovalActivity(activity: ChatActivity) {
  return activity.kind === 'approval' && Boolean(activity.approval)
}

function approvalRequest(activity: ChatActivity) {
  const request = activity.approval?.payload.request
  if (!request || typeof request !== 'object' || Array.isArray(request)) {
    return null
  }
  return request as Record<string, unknown>
}

function approvalUsesStructuredForm(activity: ChatActivity) {
  return activity.approval?.formMode === 'structured' && activity.approval.formFields.length > 0
}

function approvalUsesJsonFallback(activity: ChatActivity) {
  return activity.approval?.formMode === 'json'
}

function approvalHasUrl(activity: ChatActivity) {
  const url = approvalRequest(activity)?.url
  return typeof url === 'string' && url.length > 0
}

function approvalUrl(activity: ChatActivity) {
  const url = approvalRequest(activity)?.url
  return typeof url === 'string' ? url : ''
}

function approvalMessage(activity: ChatActivity) {
  const message = approvalRequest(activity)?.message
  return typeof message === 'string' ? message : ''
}

function approvalSchemaPreview(activity: ChatActivity) {
  const schema = approvalRequest(activity)?.requestedSchema
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) {
    return ''
  }
  return JSON.stringify(schema, null, 2)
}

function approvalFieldPrompt(field: ApprovalFormFieldState) {
  return field.prompt && field.prompt !== field.label ? field.prompt : ''
}

function approvalFieldValue(field: ApprovalFormFieldState) {
  if (field.kind === 'multiselect') {
    return Array.isArray(field.value) ? field.value : []
  }
  if (field.kind === 'boolean') {
    return typeof field.value === 'boolean' ? String(field.value) : ''
  }
  return typeof field.value === 'string' ? field.value : ''
}

function updateApprovalFieldValue(field: ApprovalFormFieldState, value: string) {
  if (field.kind === 'boolean') {
    field.value = value === 'true' ? true : value === 'false' ? false : null
    return
  }
  field.value = value
}

function onApprovalInput(field: ApprovalFormFieldState, event: Event, activity: ChatActivity) {
  const target = event.target
  if (!(target instanceof HTMLInputElement)) {
    return
  }
  updateApprovalFieldValue(field, target.value)
  clearApprovalValidation(activity)
}

function onApprovalSelect(field: ApprovalFormFieldState, event: Event, activity: ChatActivity) {
  const target = event.target
  if (!(target instanceof HTMLSelectElement)) {
    return
  }
  updateApprovalFieldValue(field, target.value)
  clearApprovalValidation(activity)
}

function updateApprovalMultiSelect(field: ApprovalFormFieldState, event: Event) {
  const target = event.target
  if (!(target instanceof HTMLSelectElement)) {
    return
  }
  field.value = Array.from(target.selectedOptions).map((option) => option.value)
}

function onApprovalMultiSelect(field: ApprovalFormFieldState, event: Event, activity: ChatActivity) {
  updateApprovalMultiSelect(field, event)
  clearApprovalValidation(activity)
}

function clearApprovalValidation(activity: ChatActivity) {
  if (!activity.approval) {
    return
  }
  activity.approval.validationError = null
}

function approvalContentText(activity: ChatActivity) {
  if (!activity.approval) {
    return ''
  }

  if (!approvalUsesStructuredForm(activity)) {
    return activity.approval.responseDraft
  }

  const content: Record<string, unknown> = {}
  for (const field of activity.approval.formFields) {
    const result = approvalFieldContent(field)
    if (typeof result === 'string') {
      activity.approval.validationError = result
      return ''
    }
    if (result !== undefined) {
      content[field.id] = result
    }
  }

  activity.approval.validationError = null
  return JSON.stringify(content)
}

function approvalFieldContent(field: ApprovalFormFieldState): string | unknown | undefined {
  if (field.kind === 'text') {
    const value = typeof field.value === 'string' ? field.value.trim() : ''
    if (!value) {
      return field.required ? `${field.label} is required.` : undefined
    }
    return value
  }

  if (field.kind === 'number') {
    const value = typeof field.value === 'string' ? field.value.trim() : ''
    if (!value) {
      return field.required ? `${field.label} is required.` : undefined
    }

    const parsed = Number(value)
    if (!Number.isFinite(parsed)) {
      return `${field.label} must be a valid number.`
    }
    if (field.integer && !Number.isInteger(parsed)) {
      return `${field.label} must be an integer.`
    }
    if (typeof field.min === 'number' && parsed < field.min) {
      return `${field.label} must be at least ${field.min}.`
    }
    if (typeof field.max === 'number' && parsed > field.max) {
      return `${field.label} must be at most ${field.max}.`
    }
    return parsed
  }

  if (field.kind === 'boolean') {
    if (typeof field.value !== 'boolean') {
      return field.required ? `${field.label} is required.` : undefined
    }
    return field.value
  }

  if (field.kind === 'select') {
    const value = typeof field.value === 'string' ? field.value : ''
    if (!value) {
      return field.required ? `${field.label} is required.` : undefined
    }
    return value
  }

  if (field.kind === 'multiselect') {
    const value = Array.isArray(field.value) ? field.value.filter((item) => typeof item === 'string') : []
    if (!value.length) {
      return field.required ? `${field.label} is required.` : undefined
    }
    return value
  }

  return undefined
}

function submitApproval(activity: ChatActivity, decision: ApprovalDecision) {
  if (!activity.approval) {
    return
  }

  const contentText = approvalContentText(activity)
  if (approvalUsesStructuredForm(activity) && activity.approval.validationError) {
    return
  }
  emit('approvalAction', activity.approval.requestId, decision, contentText)
}

const visibleActivities = computed(() =>
  props.activities.filter((activity) => !isHiddenActivity(activity)),
)

const activityOpenOverrides = ref<Record<string, boolean>>({})
const turnGroupOpenOverrides = ref<Record<string, boolean>>({})

const feedEntries = computed<FeedEntry[]>(() => {
  const explicitSequences = [...props.messages, ...visibleActivities.value]
    .map((item) => item.sequence)
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value))

  if (!explicitSequences.length) {
    let sortOrder = 0
    const lastMessage = props.messages[props.messages.length - 1]
    const trailingAssistantMessage =
      visibleActivities.value.length && lastMessage?.role === 'assistant' ? lastMessage : null
    const leadingMessages = trailingAssistantMessage
      ? props.messages.slice(0, -1)
      : props.messages

    const legacyMessages: MessageFeedItem[] = leadingMessages.map((message) => {
      sortOrder += 1000
      return {
        type: 'message',
        key: `message:${message.id}`,
        sortOrder,
        message,
      }
    })

    const legacyActivities: ActivityFeedItem[] = visibleActivities.value.flatMap((activity) => {
      const changes = fileChangeRecords(activity)

      if (changes.length) {
        return changes.map((change, index) => {
          const key = `activity:${activity.id}:change:${index}`
          sortOrder += 1
          return {
            type: 'activity',
            key,
            sortOrder,
            display: {
              key,
              sortOrder,
              activity,
              change,
            },
          }
        })
      }

      sortOrder += 1
      const key = `activity:${activity.id}`
      return [
        {
          type: 'activity',
          key,
          sortOrder,
          display: {
            key,
            sortOrder,
            activity,
          },
        },
      ]
    })

    const trailingMessages: MessageFeedItem[] = trailingAssistantMessage
      ? [
          {
            type: 'message',
            key: `message:${trailingAssistantMessage.id}`,
            sortOrder: sortOrder + 1000,
            message: trailingAssistantMessage,
          },
        ]
      : []

    return [...legacyMessages, ...legacyActivities, ...trailingMessages]
  }

  let fallbackSequence = explicitSequences.length ? Math.max(...explicitSequences) + 1 : 0

  const messages: MessageFeedItem[] = props.messages.map((message) => {
    const sequence =
      typeof message.sequence === 'number' && Number.isFinite(message.sequence)
        ? message.sequence
        : fallbackSequence++

    return {
      type: 'message',
      key: `message:${message.id}`,
      sortOrder: sequence * 1000 + 500,
      message,
    }
  })

  const activities: ActivityFeedItem[] = visibleActivities.value.flatMap((activity) => {
    const sequence =
      typeof activity.sequence === 'number' && Number.isFinite(activity.sequence)
        ? activity.sequence
        : fallbackSequence++
    const baseSortOrder = sequence * 1000
    const changes = fileChangeRecords(activity)

    if (changes.length) {
      return changes.map((change, index) => {
        const key = `activity:${activity.id}:change:${index}`
        const sortOrder = baseSortOrder + index + 1
        return {
          type: 'activity',
          key,
          sortOrder,
          display: {
            key,
            sortOrder,
            activity,
            change,
          },
        }
      })
    }

    const key = `activity:${activity.id}`
    return [
      {
        type: 'activity',
        key,
        sortOrder: baseSortOrder + 1,
        display: {
          key,
          sortOrder: baseSortOrder + 1,
          activity,
        },
      },
    ]
  })

  return [...activities, ...messages].sort((left, right) => {
    if (left.sortOrder !== right.sortOrder) {
      return left.sortOrder - right.sortOrder
    }
    if (left.type === right.type) {
      return left.key.localeCompare(right.key)
    }
    return left.type === 'activity' ? -1 : 1
  })
})

const latestAssistantSortOrder = computed(() => {
  const assistantOrders = feedEntries.value
    .filter((entry): entry is MessageFeedItem => entry.type === 'message' && entry.message.role === 'assistant')
    .map((entry) => entry.sortOrder)

  return assistantOrders.length ? Math.max(...assistantOrders) : Number.NEGATIVE_INFINITY
})

const latestCurrentTurnActivityKey = computed(() => {
  const activityEntries = feedEntries.value.filter(
    (entry): entry is ActivityFeedItem =>
      entry.type === 'activity' &&
      isCurrentTurnActivity(entry.display) &&
      hasActivityDetails(entry.display),
  )

  return activityEntries.length ? activityEntries[activityEntries.length - 1]?.display.key ?? null : null
})

const renderEntries = computed<RenderEntry[]>(() => {
  const entries: RenderEntry[] = []
  let activeUserMessage: MessageFeedItem | null = null
  let pendingTurnItems: Array<ActivityFeedItem | MessageFeedItem> = []
  let activeTurnIndex = -1

  function flushTurn() {
    if (!activeUserMessage) {
      if (pendingTurnItems.length) {
        entries.push(...pendingTurnItems)
      }

      pendingTurnItems = []
      return
    }

    entries.push(activeUserMessage)

    const assistantIndexes = pendingTurnItems.flatMap((entry, index) =>
      entry.type === 'message' && entry.message.role === 'assistant' ? [index] : [],
    )

    if (!assistantIndexes.length) {
      entries.push(...pendingTurnItems)
      activeUserMessage = null
      pendingTurnItems = []
      return
    }

    const finalAssistantIndex = assistantIndexes[assistantIndexes.length - 1] ?? -1
    const groupedItems = pendingTurnItems.slice(0, finalAssistantIndex)
    const finalAssistant = pendingTurnItems[finalAssistantIndex]
    const trailingItems = pendingTurnItems.slice(finalAssistantIndex + 1)

    if (groupedItems.length) {
      const firstSortOrder = groupedItems[0]?.sortOrder ?? 0
      const lastSortOrder = groupedItems[groupedItems.length - 1]?.sortOrder ?? firstSortOrder
      entries.push({
        type: 'turn-group',
        key: `turn-group:${firstSortOrder}:${lastSortOrder}`,
        sortOrder: firstSortOrder,
        turnIndex: activeTurnIndex,
        items: groupedItems,
      })
    }

    if (finalAssistant) {
      entries.push(finalAssistant)
    }
    if (trailingItems.length) {
      entries.push(...trailingItems)
    }

    activeUserMessage = null
    pendingTurnItems = []
  }

  for (const entry of feedEntries.value) {
    if (entry.type === 'message' && entry.message.role === 'user') {
      flushTurn()
      activeTurnIndex += 1
      activeUserMessage = entry
      continue
    }

    if (!activeUserMessage) {
      entries.push(entry)
      continue
    }

    pendingTurnItems.push(entry)
  }

  if (activeUserMessage) {
    flushTurn()
  }

  return entries
})

function isCurrentTurnActivity(display: ActivityDisplayItem) {
  if (!Number.isFinite(latestAssistantSortOrder.value)) {
    return true
  }
  return display.sortOrder > latestAssistantSortOrder.value
}

function shouldDefaultOpenActivity(display: ActivityDisplayItem) {
  if (isApprovalActivity(display.activity)) {
    return latestCurrentTurnActivityKey.value === display.key &&
      (display.activity.state === 'queued' || display.activity.state === 'running')
  }

  return props.isSending &&
    isCurrentTurnActivity(display) &&
    latestCurrentTurnActivityKey.value === display.key
}

function isActivityOpen(display: ActivityDisplayItem) {
  const override = activityOpenOverrides.value[display.key]
  if (typeof override === 'boolean') {
    return override
  }
  return shouldDefaultOpenActivity(display)
}

function onActivityToggle(display: ActivityDisplayItem, event: Event) {
  const target = event.target
  if (!(target instanceof HTMLDetailsElement)) {
    return
  }

  activityOpenOverrides.value = {
    ...activityOpenOverrides.value,
    [display.key]: target.open,
  }
}

function isTurnGroupOpen(group: TurnGroupEntry) {
  const override = turnGroupOpenOverrides.value[group.key]
  return typeof override === 'boolean' ? override : false
}

function onTurnGroupToggle(group: TurnGroupEntry, event: Event) {
  const target = event.target
  if (!(target instanceof HTMLDetailsElement)) {
    return
  }

  turnGroupOpenOverrides.value = {
    ...turnGroupOpenOverrides.value,
    [group.key]: target.open,
  }
}

function turnGroupDurationSeconds(group: TurnGroupEntry) {
  const turnTiming = props.turnTimings?.[group.turnIndex]
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

function formatDurationLabel(totalSeconds: number) {
  const roundedSeconds = Math.max(0, Math.round(totalSeconds))
  const minutes = Math.floor(roundedSeconds / 60)
  const seconds = roundedSeconds % 60

  if (minutes <= 0) {
    return `${seconds}s`
  }

  return `${minutes}m ${seconds}s`
}

function turnGroupSummary(group: TurnGroupEntry) {
  const durationSeconds = turnGroupDurationSeconds(group)
  if (durationSeconds !== null) {
    return `Worked for ${formatDurationLabel(durationSeconds)}`
  }

  const count = group.items.length
  const noun = count === 1 ? 'update' : 'updates'
  return `Worked through ${count} ${noun}`
}

function shouldShowFinalMessageSeparator(entry: MessageFeedItem, index: number) {
  if (entry.message.role !== 'assistant') {
    return false
  }

  const previousEntry = renderEntries.value[index - 1]
  return previousEntry?.type === 'turn-group' ? isTurnGroupOpen(previousEntry) : false
}

function hasActivityDetails(display: ActivityDisplayItem) {
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

function genericActivityDetail(activity: ChatActivity) {
  const detail = activity.detail.trim()
  if (!detail || activityUsesMarkdown(activity) || isApprovalActivity(activity) || isShellActivity(activity)) {
    return ''
  }

  if (detail === activity.title.trim()) {
    return ''
  }

  return detail
}

watch(
  latestAssistantSortOrder,
  (nextValue, previousValue) => {
    if (Number.isFinite(previousValue) && nextValue > previousValue) {
      activityOpenOverrides.value = {}
    }
  },
  { flush: 'post' },
)

watch(
  latestCurrentTurnActivityKey,
  (nextValue, previousValue) => {
    if (previousValue && nextValue && previousValue !== nextValue) {
      activityOpenOverrides.value = {}
    }
  },
  { flush: 'post' },
)

function isNearBottom(element: HTMLElement) {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= bottomThreshold
}

function onTimelineScroll() {
  if (!timelineBody.value) {
    return
  }
  shouldStickToBottom.value = isNearBottom(timelineBody.value)
}

async function scrollToBottomIfNeeded() {
  await nextTick()
  if (!timelineBody.value || !shouldStickToBottom.value) {
    return
  }
  timelineBody.value.scrollTop = timelineBody.value.scrollHeight
}

onMounted(async () => {
  await scrollToBottomIfNeeded()
})

watch(
  () => props.activities,
  async () => {
    await scrollToBottomIfNeeded()
  },
  { deep: true, flush: 'post' },
)

watch(
  () => [props.messages.length, props.isSending],
  async () => {
    await scrollToBottomIfNeeded()
  },
  { flush: 'post' },
)

watch(
  () => props.bottomInset,
  async () => {
    await scrollToBottomIfNeeded()
  },
  { flush: 'post' },
)
</script>

<template>
  <section
    class="flex h-full min-h-0 flex-1 flex-col gap-4 overflow-hidden rounded-3xl border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-[1.1rem] shadow-[var(--app-shadow)] backdrop-blur-[14px] max-[1023px]:rounded-[1.35rem] max-[1023px]:p-4 max-sm:gap-3 max-sm:p-3"
  >
    <div
      v-if="compactHeader"
      class="flex items-center justify-between gap-3 rounded-[1rem] border border-[rgba(34,66,72,0.08)] bg-[rgba(255,250,242,0.7)] px-3 py-2.5"
    >
      <div class="min-w-0">
        <p class="eyebrow">Working Directory</p>
        <p
          class="m-0 truncate font-mono text-[0.8rem] text-[color:var(--app-text-soft)]"
          :title="projectPath"
        >
          {{ projectPath || 'No project path' }}
        </p>
      </div>
      <div
        v-if="isSending"
        class="inline-flex shrink-0 items-center gap-2 text-[0.82rem] text-[color:var(--app-text-soft)]"
      >
        <ProgressSpinner stroke-width="4" class="h-[1rem] w-[1rem]" />
        <span>Working</span>
      </div>
    </div>
    <div v-else class="flex items-start justify-between gap-3 max-[1023px]:flex-col max-[1023px]:items-stretch">
      <div>
        <p class="eyebrow">Current session</p>
        <div class="flex items-center justify-start gap-3 max-sm:flex-wrap">
          <h3 class="m-0 font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-2xl font-semibold">
            Session {{ sessionLabel }}
          </h3>
          <Tag
            :value="messages.length ? `${messages.length} msgs` : 'New'"
            rounded
            severity="secondary"
          />
        </div>
        <p v-if="sessionRuntime" class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]">
          {{ sessionRuntime.label }} · {{ runtimeStatusLabel(sessionRuntime.status) }}
          <span v-if="sessionRuntime.thread_id"> · {{ sessionRuntime.thread_id }}</span>
        </p>
        <p v-if="sessionRuntime?.detail" class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]">
          {{ sessionRuntime.detail }}
        </p>
        <p v-if="projectPath" class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]">
          {{ projectPath }}
        </p>
      </div>
      <div
        v-if="isSending"
        class="inline-flex items-center gap-2.5 self-start text-[color:var(--app-text-soft)]"
      >
        <ProgressSpinner stroke-width="4" class="h-[1.1rem] w-[1.1rem]" />
        <span>Working…</span>
      </div>
    </div>

    <div v-if="!renderEntries.length" class="grid place-items-center gap-2 p-10 text-center">
      <p class="eyebrow">Ready</p>
      <h4 class="m-0 font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-xl font-semibold">
        Start with a local task.
      </h4>
      <p class="m-0 max-w-[30rem] text-[color:var(--app-text-soft)]">
        Try asking yier to inspect this repo, summarize a file, or edit code inside the allowed
        roots.
      </p>
    </div>

    <ScrollPanel v-else class="min-h-0 flex-1" :pt="timelineScrollPt">
      <div class="grid min-w-0 grid-cols-1 gap-3">
        <template v-for="(entry, entryIndex) in renderEntries" :key="entry.key">
          <div
            v-if="entry.type === 'message'"
            class="flex min-w-0"
            :class="entry.message.role === 'user' ? 'justify-end' : 'justify-start'"
          >
            <Fieldset v-if="entry.message.role === 'user'" legend="You" :pt="messageFieldsetPt()">
              <p class="m-0 whitespace-pre-wrap leading-[1.65]">
                {{ entry.message.content }}
              </p>
            </Fieldset>
            <div
              v-else
              class="min-w-0 max-w-full flex-1 px-1 max-sm:px-0"
            >
              <div
                v-if="shouldShowFinalMessageSeparator(entry, entryIndex)"
                class="mb-3 flex items-center gap-3 text-[0.72rem] font-semibold uppercase tracking-[0.12em] text-[color:var(--app-text-soft)]"
              >
                <span class="h-px flex-1 bg-[rgba(34,66,72,0.12)]"></span>
                <span>Final message</span>
                <span class="h-px flex-1 bg-[rgba(34,66,72,0.12)]"></span>
              </div>
              <div
                class="markdown-prose"
                @click="onMarkdownClick"
                v-html="renderAssistantMessage(entry.message.content)"
              ></div>
            </div>
          </div>

          <details
            v-else-if="entry.type === 'turn-group'"
            class="overflow-hidden"
            :open="isTurnGroupOpen(entry)"
            @toggle="onTurnGroupToggle(entry, $event)"
          >
            <summary class="flex cursor-pointer list-none items-center gap-3 py-1.5 text-[0.72rem] font-semibold uppercase tracking-[0.12em] text-[color:var(--app-text-soft)]">
              <span class="h-px flex-1 bg-[rgba(34,66,72,0.12)]"></span>
              <span class="shrink-0">{{ turnGroupSummary(entry) }}</span>
              <i
                class="pi pi-angle-right shrink-0 text-[0.78rem] transition-transform duration-150"
                :class="isTurnGroupOpen(entry) ? 'rotate-90' : ''"
              ></i>
              <span class="h-px flex-1 bg-[rgba(34,66,72,0.12)]"></span>
            </summary>

            <div class="grid gap-3 pt-3">
              <template v-for="groupItem in entry.items" :key="groupItem.key">
                <div v-if="groupItem.type === 'message'" class="min-w-0">
                  <div
                    class="markdown-prose"
                    @click="onMarkdownClick"
                    v-html="renderAssistantMessage(groupItem.message.content)"
                  ></div>
                </div>
                <details
                  v-else-if="hasActivityDetails(groupItem.display)"
                  class="overflow-hidden"
                >
                  <summary class="grid cursor-pointer list-none grid-cols-[minmax(0,1fr)_auto] items-start gap-3 py-1.5">
                    <div
                      class="min-w-0"
                      :class="isShellActivity(groupItem.display.activity) ? 'overflow-x-auto overscroll-x-contain [-ms-overflow-style:none] [scrollbar-width:none]' : ''"
                    >
                      <p
                        class="m-0 inline-flex min-w-0 max-w-full items-baseline gap-2 text-[0.9rem] font-medium max-sm:text-[0.86rem]"
                        :class="isShellActivity(groupItem.display.activity) ? 'min-w-full whitespace-nowrap font-mono' : 'flex-wrap'"
                      >
                        <span class="shrink-0" :class="activitySummaryParts(groupItem.display).verbClass">
                          {{ activitySummaryParts(groupItem.display).verb }}
                        </span>
                        <span class="min-w-0 break-words text-[color:var(--app-text)]">
                          {{ activitySummaryParts(groupItem.display).text }}
                        </span>
                      </p>
                    </div>
                    <i class="pi pi-angle-right text-[0.8rem] text-[color:var(--app-text-soft)]"></i>
                  </summary>

                  <div
                    v-if="isShellActivity(groupItem.display.activity)"
                    class="grid gap-[0.7rem] border-l border-[rgba(34,66,72,0.08)] pl-4"
                  >
                    <HighlightedCodeBlock
                      v-if="shellCommand(groupItem.display.activity)"
                      :content="shellCommand(groupItem.display.activity)"
                      label="Command"
                      language="bash"
                      max-height="compact"
                      :copy-aria-label="`Copy command ${shellCommand(groupItem.display.activity)}`"
                    />

                    <HighlightedCodeBlock
                      v-if="hasShellTranscript(groupItem.display.activity)"
                      :content="shellOutputTranscript(groupItem.display.activity)"
                      label="Output"
                      :meta-label="shellRuntime(groupItem.display.activity)"
                      auto-detect
                      :copy-aria-label="`Copy output from ${shellCommand(groupItem.display.activity) || groupItem.display.activity.title}`"
                    />

                    <HighlightedCodeBlock
                      v-if="!hasShellTranscript(groupItem.display.activity) && groupItem.display.activity.stdout"
                      :content="groupItem.display.activity.stdout"
                      label="Stdout"
                      :meta-label="shellRuntime(groupItem.display.activity)"
                      auto-detect
                      :copy-aria-label="`Copy stdout from ${shellCommand(groupItem.display.activity) || groupItem.display.activity.title}`"
                    />

                    <HighlightedCodeBlock
                      v-if="!hasShellTranscript(groupItem.display.activity) && groupItem.display.activity.stderr"
                      :content="groupItem.display.activity.stderr"
                      label="Stderr"
                      tone="danger"
                      auto-detect
                      :copy-aria-label="`Copy stderr from ${shellCommand(groupItem.display.activity) || groupItem.display.activity.title}`"
                    />

                    <p
                      v-for="note in groupItem.display.activity.meta"
                      :key="`${groupItem.display.activity.id}-${note}`"
                      class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                    >
                      {{ note }}
                    </p>
                  </div>

                  <div
                    v-if="activityUsesMarkdown(groupItem.display.activity) || isApprovalActivity(groupItem.display.activity)"
                    class="grid gap-[0.55rem] border-l border-[rgba(34,66,72,0.08)] pl-4"
                  >
                    <div
                      v-if="activityUsesMarkdown(groupItem.display.activity) && groupItem.display.activity.detail"
                      class="markdown-prose"
                      @click="onMarkdownClick"
                      v-html="renderActivityMarkdown(groupItem.display.activity.detail)"
                    ></div>
                    <div v-if="isApprovalActivity(groupItem.display.activity)" class="grid gap-[0.7rem]">
                      <p
                        v-if="approvalMessage(groupItem.display.activity)"
                        class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                      >
                        {{ approvalMessage(groupItem.display.activity) }}
                      </p>
                      <p
                        v-if="approvalHasUrl(groupItem.display.activity)"
                        class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                      >
                        Open
                        <a
                          :href="approvalUrl(groupItem.display.activity)"
                          target="_blank"
                          rel="noreferrer"
                        >
                          {{ approvalUrl(groupItem.display.activity) }}
                        </a>
                      </p>
                      <div v-if="approvalSchemaPreview(groupItem.display.activity)" class="grid gap-[0.3rem]">
                        <p class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]">Requested schema</p>
                        <ScrollPanel class="w-full">
                          <pre class="rounded-[0.85rem] bg-[rgba(17,38,42,0.94)] px-[0.9rem] py-[0.8rem] font-mono text-[0.86rem] leading-[1.55] break-words whitespace-pre-wrap text-[#f2f5f6]">{{
                            approvalSchemaPreview(groupItem.display.activity)
                          }}</pre>
                        </ScrollPanel>
                      </div>
                      <div
                        v-if="approvalUsesStructuredForm(groupItem.display.activity) && groupItem.display.activity.approval"
                        class="grid gap-[0.7rem]"
                      >
                        <label
                          v-for="field in groupItem.display.activity.approval.formFields"
                          :key="`${groupItem.display.activity.id}-${field.id}`"
                          class="grid gap-1"
                        >
                          <span class="text-[0.92rem] font-bold text-[color:var(--app-text)]">
                            {{ field.label }}
                            <span v-if="field.required" class="text-[#bc5f38]">*</span>
                          </span>
                          <span
                            v-if="approvalFieldPrompt(field)"
                            class="text-[0.82rem] leading-[1.5] text-[color:var(--app-text-soft)]"
                          >
                            {{ approvalFieldPrompt(field) }}
                          </span>
                          <input
                            v-if="field.kind === 'text'"
                            class="w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-[0.8rem] py-[0.7rem] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
                            type="text"
                            :value="approvalFieldValue(field)"
                            @input="onApprovalInput(field, $event, groupItem.display.activity)"
                          />
                          <input
                            v-else-if="field.kind === 'number'"
                            class="w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-[0.8rem] py-[0.7rem] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
                            :step="field.integer ? 1 : 'any'"
                            :min="field.min ?? undefined"
                            :max="field.max ?? undefined"
                            type="number"
                            :value="approvalFieldValue(field)"
                            @input="onApprovalInput(field, $event, groupItem.display.activity)"
                          />
                          <select
                            v-else-if="field.kind === 'boolean' || field.kind === 'select'"
                            class="w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-[0.8rem] py-[0.7rem] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
                            :value="approvalFieldValue(field)"
                            @change="onApprovalSelect(field, $event, groupItem.display.activity)"
                          >
                            <option value="">{{ field.required ? 'Select an option' : 'No selection' }}</option>
                            <template v-if="field.kind === 'boolean'">
                              <option value="true">True</option>
                              <option value="false">False</option>
                            </template>
                            <template v-else>
                              <option
                                v-for="option in field.options ?? []"
                                :key="`${field.id}-${option.value}`"
                                :value="option.value"
                              >
                                {{ option.label }}
                              </option>
                            </template>
                          </select>
                          <select
                            v-else-if="field.kind === 'multiselect'"
                            class="min-h-28 w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-[0.8rem] py-[0.7rem] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
                            multiple
                            :value="approvalFieldValue(field)"
                            @change="onApprovalMultiSelect(field, $event, groupItem.display.activity)"
                          >
                            <option
                              v-for="option in field.options ?? []"
                              :key="`${field.id}-${option.value}`"
                              :value="option.value"
                            >
                              {{ option.label }}
                            </option>
                          </select>
                        </label>
                      </div>
                      <p
                        v-if="approvalUsesJsonFallback(groupItem.display.activity)"
                        class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                      >
                        JSON response
                      </p>
                      <Textarea
                        v-if="approvalUsesJsonFallback(groupItem.display.activity) && groupItem.display.activity.approval"
                        v-model="groupItem.display.activity.approval.responseDraft"
                        auto-resize
                        fluid
                        rows="5"
                      />
                      <p
                        v-if="groupItem.display.activity.approval?.validationError"
                        class="m-0 text-[0.84rem] leading-[1.45] text-[#bc5f38]"
                      >
                        {{ groupItem.display.activity.approval.validationError }}
                      </p>
                      <div v-if="groupItem.display.activity.approval" class="flex flex-wrap gap-2">
                        <Button
                          v-for="option in groupItem.display.activity.approval.options"
                          :key="`${groupItem.display.activity.id}-${option.value}`"
                          :label="option.label"
                          size="small"
                          :severity="option.value === 'decline' || option.value === 'cancel' ? 'secondary' : undefined"
                          :outlined="option.value === 'decline' || option.value === 'cancel'"
                          @click="submitApproval(groupItem.display.activity, option.value)"
                        />
                      </div>
                    </div>
                  </div>

                  <div
                    v-if="groupItem.display.change"
                    class="grid gap-[0.7rem] border-l border-[rgba(34,66,72,0.08)] pl-4"
                  >
                    <div class="min-w-0">
                      <div class="flex flex-wrap items-center gap-2">
                        <p class="m-0 text-[0.78rem] font-bold uppercase tracking-[0.08em] text-[color:var(--app-text-soft)]">
                          {{ fileChangeKindLabel(groupItem.display.change) }}
                        </p>
                        <span
                          v-if="fileChangeMetaLabel(groupItem.display.change)"
                          class="inline-flex max-w-full items-center rounded-full border border-[rgba(34,66,72,0.1)] bg-[rgba(21,94,99,0.08)] px-2.5 py-1 text-[0.74rem] font-medium text-[color:var(--app-accent-deep)]"
                        >
                          <span class="truncate">
                            {{ fileChangeMetaLabel(groupItem.display.change) }}
                          </span>
                        </span>
                      </div>
                      <p class="mt-1 mb-0 break-all font-mono text-[0.84rem] text-[color:var(--app-text)]">
                        {{ groupItem.display.change.path }}
                      </p>
                    </div>
                    <HighlightedCodeBlock
                      v-if="groupItem.display.change.diff"
                      :content="groupItem.display.change.diff"
                      label="Diff"
                      language="diff"
                      :copy-aria-label="`Copy diff for ${groupItem.display.change.path}`"
                    />
                  </div>

                  <div
                    v-if="
                      !isShellActivity(groupItem.display.activity) &&
                      !groupItem.display.change &&
                      (Boolean(genericActivityDetail(groupItem.display.activity)) ||
                        Boolean(groupItem.display.activity.command) ||
                        Boolean(groupItem.display.activity.cwd) ||
                        Boolean(groupItem.display.activity.stdout) ||
                        Boolean(groupItem.display.activity.stderr) ||
                        groupItem.display.activity.meta.length > 0)
                    "
                    class="grid gap-[0.55rem] border-l border-[rgba(34,66,72,0.08)] pl-4"
                  >
                    <p
                      v-if="genericActivityDetail(groupItem.display.activity)"
                      class="m-0 break-words whitespace-pre-wrap text-[0.9rem] text-[color:var(--app-text)]"
                    >
                      {{ genericActivityDetail(groupItem.display.activity) }}
                    </p>
                    <p
                      v-if="groupItem.display.activity.command"
                      class="m-0 break-words whitespace-pre-wrap font-mono text-[0.9rem] text-[color:var(--app-accent-deep)]"
                    >
                      {{ groupItem.display.activity.command }}
                    </p>
                    <p
                      v-if="groupItem.display.activity.cwd"
                      class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                    >
                      cwd {{ groupItem.display.activity.cwd }}
                    </p>
                    <p
                      v-for="note in groupItem.display.activity.meta"
                      :key="`${groupItem.display.activity.id}-${note}`"
                      class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                    >
                      {{ note }}
                    </p>

                    <HighlightedCodeBlock
                      v-if="groupItem.display.activity.stdout"
                      :content="groupItem.display.activity.stdout"
                      label="Stdout"
                      auto-detect
                      :copy-aria-label="`Copy stdout from ${activitySummaryText(groupItem.display)}`"
                    />

                    <HighlightedCodeBlock
                      v-if="groupItem.display.activity.stderr"
                      :content="groupItem.display.activity.stderr"
                      label="Stderr"
                      tone="danger"
                      auto-detect
                      :copy-aria-label="`Copy stderr from ${activitySummaryText(groupItem.display)}`"
                    />
                  </div>
                </details>
                <div
                  v-else
                  class="min-w-0 py-1.5"
                >
                  <div
                    class="min-w-0"
                    :class="isShellActivity(groupItem.display.activity) ? 'overflow-x-auto overscroll-x-contain [-ms-overflow-style:none] [scrollbar-width:none]' : ''"
                  >
                    <p
                      class="m-0 inline-flex min-w-0 max-w-full items-baseline gap-2 text-[0.9rem] font-medium max-sm:text-[0.86rem]"
                      :class="isShellActivity(groupItem.display.activity) ? 'min-w-full whitespace-nowrap font-mono' : 'flex-wrap'"
                    >
                      <span class="shrink-0" :class="activitySummaryParts(groupItem.display).verbClass">
                        {{ activitySummaryParts(groupItem.display).verb }}
                      </span>
                      <span class="min-w-0 break-words text-[color:var(--app-text)]">
                        {{ activitySummaryParts(groupItem.display).text }}
                      </span>
                    </p>
                  </div>
                </div>
              </template>
            </div>
          </details>

          <details
            v-else-if="hasActivityDetails(entry.display)"
            class="overflow-hidden rounded-[1rem] border border-[rgba(34,66,72,0.08)] bg-[rgba(255,250,242,0.72)] shadow-[0_10px_30px_rgba(24,44,48,0.05)]"
            :open="isActivityOpen(entry.display)"
            @toggle="onActivityToggle(entry.display, $event)"
          >
            <summary class="grid cursor-pointer list-none grid-cols-[minmax(0,1fr)_auto] items-start gap-3 px-4 py-3 max-sm:px-3 max-sm:py-2.5">
              <div
                class="min-w-0"
                :class="isShellActivity(entry.display.activity) ? 'overflow-x-auto overscroll-x-contain [-ms-overflow-style:none] [scrollbar-width:none]' : ''"
              >
                <p
                  class="m-0 inline-flex min-w-0 max-w-full items-baseline gap-2 text-[0.92rem] font-medium max-sm:text-[0.88rem]"
                  :class="isShellActivity(entry.display.activity) ? 'min-w-full whitespace-nowrap font-mono' : 'flex-wrap'"
                >
                  <span class="shrink-0" :class="activitySummaryParts(entry.display).verbClass">
                    {{ activitySummaryParts(entry.display).verb }}
                  </span>
                  <span class="min-w-0 break-words text-[color:var(--app-text)]">
                    {{ activitySummaryParts(entry.display).text }}
                  </span>
                </p>
              </div>
              <i
                class="pi pi-angle-down text-[0.82rem] text-[color:var(--app-text-soft)] transition-transform duration-150"
                :class="isActivityOpen(entry.display) ? 'rotate-180' : ''"
              ></i>
            </summary>

            <div
              v-if="isShellActivity(entry.display.activity)"
              class="grid gap-[0.7rem] border-t border-[rgba(34,66,72,0.08)] px-4 pb-4 pt-3 max-sm:px-3 max-sm:pb-3"
            >
              <HighlightedCodeBlock
                v-if="shellCommand(entry.display.activity)"
                :content="shellCommand(entry.display.activity)"
                label="Command"
                language="bash"
                max-height="compact"
                :copy-aria-label="`Copy command ${shellCommand(entry.display.activity)}`"
              />

              <HighlightedCodeBlock
                v-if="hasShellTranscript(entry.display.activity)"
                :content="shellOutputTranscript(entry.display.activity)"
                label="Output"
                :meta-label="shellRuntime(entry.display.activity)"
                auto-detect
                :copy-aria-label="`Copy output from ${shellCommand(entry.display.activity) || entry.display.activity.title}`"
              />

              <HighlightedCodeBlock
                v-if="!hasShellTranscript(entry.display.activity) && entry.display.activity.stdout"
                :content="entry.display.activity.stdout"
                label="Stdout"
                :meta-label="shellRuntime(entry.display.activity)"
                auto-detect
                :copy-aria-label="`Copy stdout from ${shellCommand(entry.display.activity) || entry.display.activity.title}`"
              />

              <HighlightedCodeBlock
                v-if="!hasShellTranscript(entry.display.activity) && entry.display.activity.stderr"
                :content="entry.display.activity.stderr"
                label="Stderr"
                tone="danger"
                auto-detect
                :copy-aria-label="`Copy stderr from ${shellCommand(entry.display.activity) || entry.display.activity.title}`"
              />

              <p
                v-for="note in entry.display.activity.meta"
                :key="`${entry.display.activity.id}-${note}`"
                class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
              >
                {{ note }}
              </p>
            </div>

            <div
              v-if="activityUsesMarkdown(entry.display.activity) || isApprovalActivity(entry.display.activity)"
              class="grid gap-[0.55rem] border-t border-[rgba(34,66,72,0.08)] px-4 pb-4 pt-3 max-sm:px-3 max-sm:pb-3"
            >
              <div
                v-if="activityUsesMarkdown(entry.display.activity) && entry.display.activity.detail"
                class="markdown-prose"
                @click="onMarkdownClick"
                v-html="renderActivityMarkdown(entry.display.activity.detail)"
              ></div>
              <div v-if="isApprovalActivity(entry.display.activity)" class="grid gap-[0.7rem]">
                <p
                  v-if="approvalMessage(entry.display.activity)"
                  class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                >
                  {{ approvalMessage(entry.display.activity) }}
                </p>
                <p
                  v-if="approvalHasUrl(entry.display.activity)"
                  class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                >
                  Open
                  <a
                    :href="approvalUrl(entry.display.activity)"
                    target="_blank"
                    rel="noreferrer"
                  >
                    {{ approvalUrl(entry.display.activity) }}
                  </a>
                </p>
                <div v-if="approvalSchemaPreview(entry.display.activity)" class="grid gap-[0.3rem]">
                  <p class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]">Requested schema</p>
                  <ScrollPanel class="w-full">
                    <pre class="rounded-[0.85rem] bg-[rgba(17,38,42,0.94)] px-[0.9rem] py-[0.8rem] font-mono text-[0.86rem] leading-[1.55] break-words whitespace-pre-wrap text-[#f2f5f6]">{{
                      approvalSchemaPreview(entry.display.activity)
                    }}</pre>
                  </ScrollPanel>
                </div>
                <div
                  v-if="approvalUsesStructuredForm(entry.display.activity) && entry.display.activity.approval"
                  class="grid gap-[0.7rem]"
                >
                  <label
                    v-for="field in entry.display.activity.approval.formFields"
                    :key="`${entry.display.activity.id}-${field.id}`"
                    class="grid gap-1"
                  >
                    <span class="text-[0.92rem] font-bold text-[color:var(--app-text)]">
                      {{ field.label }}
                      <span v-if="field.required" class="text-[#bc5f38]">*</span>
                    </span>
                    <span
                      v-if="approvalFieldPrompt(field)"
                      class="text-[0.82rem] leading-[1.5] text-[color:var(--app-text-soft)]"
                    >
                      {{ approvalFieldPrompt(field) }}
                    </span>
                    <input
                      v-if="field.kind === 'text'"
                      class="w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-[0.8rem] py-[0.7rem] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
                      type="text"
                      :value="approvalFieldValue(field)"
                      @input="onApprovalInput(field, $event, entry.display.activity)"
                    />
                    <input
                      v-else-if="field.kind === 'number'"
                      class="w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-[0.8rem] py-[0.7rem] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
                      :step="field.integer ? 1 : 'any'"
                      :min="field.min ?? undefined"
                      :max="field.max ?? undefined"
                      type="number"
                      :value="approvalFieldValue(field)"
                      @input="onApprovalInput(field, $event, entry.display.activity)"
                    />
                    <select
                      v-else-if="field.kind === 'boolean' || field.kind === 'select'"
                      class="w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-[0.8rem] py-[0.7rem] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
                      :value="approvalFieldValue(field)"
                      @change="onApprovalSelect(field, $event, entry.display.activity)"
                    >
                      <option value="">{{ field.required ? 'Select an option' : 'No selection' }}</option>
                      <template v-if="field.kind === 'boolean'">
                        <option value="true">True</option>
                        <option value="false">False</option>
                      </template>
                      <template v-else>
                        <option
                          v-for="option in field.options ?? []"
                          :key="`${field.id}-${option.value}`"
                          :value="option.value"
                        >
                          {{ option.label }}
                        </option>
                      </template>
                    </select>
                    <select
                      v-else-if="field.kind === 'multiselect'"
                      class="min-h-28 w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-[0.8rem] py-[0.7rem] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
                      multiple
                      :value="approvalFieldValue(field)"
                      @change="onApprovalMultiSelect(field, $event, entry.display.activity)"
                    >
                      <option
                        v-for="option in field.options ?? []"
                        :key="`${field.id}-${option.value}`"
                        :value="option.value"
                      >
                        {{ option.label }}
                      </option>
                    </select>
                  </label>
                </div>
                <p
                  v-if="approvalUsesJsonFallback(entry.display.activity)"
                  class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                >
                  JSON response
                </p>
                <Textarea
                  v-if="approvalUsesJsonFallback(entry.display.activity) && entry.display.activity.approval"
                  v-model="entry.display.activity.approval.responseDraft"
                  auto-resize
                  fluid
                  rows="5"
                />
                <p
                  v-if="entry.display.activity.approval?.validationError"
                  class="m-0 text-[0.84rem] leading-[1.45] text-[#bc5f38]"
                >
                  {{ entry.display.activity.approval.validationError }}
                </p>
                <div v-if="entry.display.activity.approval" class="flex flex-wrap gap-2">
                  <Button
                    v-for="option in entry.display.activity.approval.options"
                    :key="`${entry.display.activity.id}-${option.value}`"
                    :label="option.label"
                    size="small"
                    :severity="option.value === 'decline' || option.value === 'cancel' ? 'secondary' : undefined"
                    :outlined="option.value === 'decline' || option.value === 'cancel'"
                    @click="submitApproval(entry.display.activity, option.value)"
                  />
                </div>
              </div>
            </div>

            <div
              v-if="entry.display.change"
              class="grid gap-[0.7rem] border-t border-[rgba(34,66,72,0.08)] px-4 pb-4 pt-3 max-sm:px-3 max-sm:pb-3"
            >
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <p class="m-0 text-[0.78rem] font-bold uppercase tracking-[0.08em] text-[color:var(--app-text-soft)]">
                    {{ fileChangeKindLabel(entry.display.change) }}
                  </p>
                  <span
                    v-if="fileChangeMetaLabel(entry.display.change)"
                    class="inline-flex max-w-full items-center rounded-full border border-[rgba(34,66,72,0.1)] bg-[rgba(21,94,99,0.08)] px-2.5 py-1 text-[0.74rem] font-medium text-[color:var(--app-accent-deep)]"
                  >
                    <span class="truncate">
                      {{ fileChangeMetaLabel(entry.display.change) }}
                    </span>
                  </span>
                </div>
                <p class="mt-1 mb-0 break-all font-mono text-[0.84rem] text-[color:var(--app-text)]">
                  {{ entry.display.change.path }}
                </p>
              </div>
              <HighlightedCodeBlock
                v-if="entry.display.change.diff"
                :content="entry.display.change.diff"
                label="Diff"
                language="diff"
                :copy-aria-label="`Copy diff for ${entry.display.change.path}`"
              />
            </div>

            <div
              v-if="
                !isShellActivity(entry.display.activity) &&
                !entry.display.change &&
                (Boolean(genericActivityDetail(entry.display.activity)) ||
                  Boolean(entry.display.activity.command) ||
                  Boolean(entry.display.activity.cwd) ||
                  Boolean(entry.display.activity.stdout) ||
                  Boolean(entry.display.activity.stderr) ||
                  entry.display.activity.meta.length > 0)
              "
              class="grid gap-[0.55rem] border-t border-[rgba(34,66,72,0.08)] px-4 pb-4 pt-3 max-sm:px-3 max-sm:pb-3"
            >
              <p
                v-if="genericActivityDetail(entry.display.activity)"
                class="m-0 break-words whitespace-pre-wrap text-[0.9rem] text-[color:var(--app-text)]"
              >
                {{ genericActivityDetail(entry.display.activity) }}
              </p>
              <p
                v-if="entry.display.activity.command"
                class="m-0 break-words whitespace-pre-wrap font-mono text-[0.9rem] text-[color:var(--app-accent-deep)]"
              >
                {{ entry.display.activity.command }}
              </p>
              <p
                v-if="entry.display.activity.cwd"
                class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
              >
                cwd {{ entry.display.activity.cwd }}
              </p>
              <p
                v-for="note in entry.display.activity.meta"
                :key="`${entry.display.activity.id}-${note}`"
                class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
              >
                {{ note }}
              </p>

              <HighlightedCodeBlock
                v-if="entry.display.activity.stdout"
                :content="entry.display.activity.stdout"
                label="Stdout"
                auto-detect
                :copy-aria-label="`Copy stdout from ${activitySummaryText(entry.display)}`"
              />

              <HighlightedCodeBlock
                v-if="entry.display.activity.stderr"
                :content="entry.display.activity.stderr"
                label="Stderr"
                tone="danger"
                auto-detect
                :copy-aria-label="`Copy stderr from ${activitySummaryText(entry.display)}`"
              />
            </div>
          </details>

          <div
            v-else
            class="rounded-[1rem] border border-[rgba(34,66,72,0.08)] bg-[rgba(255,250,242,0.58)] px-4 py-3 max-sm:px-3 max-sm:py-2.5"
          >
            <p
              class="m-0 inline-flex min-w-0 max-w-full items-baseline gap-2 text-[0.92rem] font-medium max-sm:text-[0.88rem]"
              :class="isShellActivity(entry.display.activity) ? 'overflow-x-auto overscroll-x-contain whitespace-nowrap font-mono [-ms-overflow-style:none] [scrollbar-width:none]' : 'flex-wrap'"
            >
              <span class="shrink-0" :class="activitySummaryParts(entry.display).verbClass">
                {{ activitySummaryParts(entry.display).verb }}
              </span>
              <span class="min-w-0 break-words text-[color:var(--app-text)]">
                {{ activitySummaryParts(entry.display).text }}
              </span>
            </p>
          </div>
        </template>
      </div>
    </ScrollPanel>
  </section>
</template>
