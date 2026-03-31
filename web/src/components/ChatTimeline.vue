<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/common'
import Button from 'primevue/button'
import ProgressSpinner from 'primevue/progressspinner'
import ScrollPanel from 'primevue/scrollpanel'
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'
import Timeline from 'primevue/timeline'

import HighlightedCodeBlock from './HighlightedCodeBlock.vue'
import { resolveHighlightLanguage } from '../lib/codeHighlight'

import type {
  ApprovalDecision,
  ApprovalFormFieldState,
  BackendRuntime,
  ChatActivity,
  FileChangeRecord,
  UiChatMessage,
} from '../types/api'

interface TimelineSegment {
  kind: 'messages' | 'activities'
  key: string
  messages: UiChatMessage[]
  activities: ChatActivity[]
}

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

function activityMarkerClass(activity: ChatActivity) {
  if (activity.state === 'running') {
    return 'bg-[#347f86]'
  }
  if (activity.state === 'done') {
    return 'bg-[#4b8b58]'
  }
  if (activity.state === 'error') {
    return 'bg-[#b85d48]'
  }
  return 'bg-[#7a6b4e]'
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

function hasFileChangeDiffs(activity: ChatActivity) {
  return fileChangeRecords(activity).length > 0
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

function fileChangeMetaLabel(change: FileChangeRecord) {
  if (change.kind.type === 'move' && change.kind.move_path) {
    return change.kind.move_path
  }
  return ''
}

function activitySummaryPrimary(activity: ChatActivity) {
  if (activity.kind === 'tool' && activity.detail.trim()) {
    return activity.detail
  }

  return activity.title
}

function activitySummarySecondary(activity: ChatActivity) {
  if (isShellActivity(activity) || activity.kind === 'tool') {
    return ''
  }

  if (!activityUsesMarkdown(activity) && activity.detail.trim()) {
    return activity.detail
  }

  return ''
}

function activitySummaryPrimaryClass(activity: ChatActivity) {
  return 'font-bold'
}

function shouldAutoOpenActivity(activity: ChatActivity) {
  if (activity.kind === 'approval') {
    return activity.state === 'queued' || activity.state === 'running'
  }

  return activity.state === 'running'
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

const timelineSegments = computed<TimelineSegment[]>(() => {
  const explicitSequences = [...props.messages, ...visibleActivities.value]
    .map((item) => item.sequence)
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value))

  if (!explicitSequences.length) {
    const segments: TimelineSegment[] = []
    const lastMessage = props.messages[props.messages.length - 1]
    const trailingAssistantMessage =
      visibleActivities.value.length && lastMessage?.role === 'assistant' ? lastMessage : null
    const leadingMessages = trailingAssistantMessage
      ? props.messages.slice(0, -1)
      : props.messages

    if (leadingMessages.length) {
      segments.push({
        kind: 'messages',
        key: `legacy-messages:${leadingMessages[0]?.id ?? 'empty'}`,
        messages: leadingMessages,
        activities: [],
      })
    }

    if (visibleActivities.value.length) {
      segments.push({
        kind: 'activities',
        key: `legacy-activities:${visibleActivities.value[0]?.id ?? 'empty'}`,
        messages: [],
        activities: visibleActivities.value,
      })
    }

    if (trailingAssistantMessage) {
      segments.push({
        kind: 'messages',
        key: `legacy-trailing:${trailingAssistantMessage.id}`,
        messages: [trailingAssistantMessage],
        activities: [],
      })
    }

    return segments
  }

  let fallbackSequence = explicitSequences.length ? Math.max(...explicitSequences) + 1 : 0

  const entries = [
    ...props.messages.map((message) => ({
      kind: 'message' as const,
      key: `message:${message.id}`,
      sequence:
        typeof message.sequence === 'number' && Number.isFinite(message.sequence)
          ? message.sequence
          : fallbackSequence++,
      message,
    })),
    ...visibleActivities.value.map((activity) => ({
      kind: 'activity' as const,
      key: `activity:${activity.id}`,
      sequence:
        typeof activity.sequence === 'number' && Number.isFinite(activity.sequence)
          ? activity.sequence
          : fallbackSequence++,
      activity,
    })),
  ].sort((left, right) => {
    if (left.sequence !== right.sequence) {
      return left.sequence - right.sequence
    }
    if (left.kind === right.kind) {
      return 0
    }
    return left.kind === 'message' ? -1 : 1
  })

  const segments: TimelineSegment[] = []
  for (const entry of entries) {
    const previousSegment = segments[segments.length - 1]
    if (entry.kind === 'message') {
      if (previousSegment && previousSegment.kind === 'messages') {
        previousSegment.messages.push(entry.message)
        continue
      }
      segments.push({
        kind: 'messages',
        key: entry.key,
        messages: [entry.message],
        activities: [],
      })
      continue
    }

    if (previousSegment && previousSegment.kind === 'activities') {
      previousSegment.activities.push(entry.activity)
      continue
    }
    segments.push({
      kind: 'activities',
      key: entry.key,
      messages: [],
      activities: [entry.activity],
    })
  }

  return segments
})

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

    <div v-if="!timelineSegments.length" class="grid place-items-center gap-2 p-10 text-center">
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
      <div
        v-for="segment in timelineSegments"
        :key="segment.key"
        class="grid min-w-0 grid-cols-1 gap-4"
      >
        <template v-if="segment.kind === 'messages'">
          <article
            v-for="message in segment.messages"
            :key="message.id"
            class="min-w-0 max-w-[min(48rem,85%)] overflow-hidden rounded-[1.3rem] border p-4 max-[1023px]:w-full max-[1023px]:max-w-full max-sm:rounded-[1.1rem] max-sm:p-3"
            :class="
              message.role === 'user'
                ? 'ml-auto border-[rgba(21,94,99,0.16)] bg-[linear-gradient(135deg,rgba(21,94,99,0.13),rgba(69,141,145,0.08))]'
                : 'border-[rgba(153,125,93,0.15)] bg-[color:var(--app-panel-strong)]'
            "
          >
            <p class="mb-[0.35rem] mt-0 text-[0.82rem] font-bold uppercase tracking-[0.08em] text-[color:var(--app-text-soft)]">
              {{ message.role === 'user' ? 'You' : assistantLabel ?? 'Yier' }}
            </p>
            <p v-if="message.role === 'user'" class="m-0 whitespace-pre-wrap leading-[1.65]">
              {{ message.content }}
            </p>
            <div
              v-else
              class="markdown-prose"
              @click="onMarkdownClick"
              v-html="renderAssistantMessage(message.content)"
            ></div>
          </article>
        </template>

        <Timeline
          v-else
          :value="segment.activities"
          align="left"
          :pt="{
            root: {
              class:
                '[&_.p-timeline-event]:items-stretch [&_.p-timeline-event-content]:min-w-0 [&_.p-timeline-event-content]:flex-1 [&_.p-timeline-event-content]:pb-4 [&_.p-timeline-event-marker]:bg-transparent [&_.p-timeline-event-marker]:shadow-none [&_.p-timeline-event-opposite]:hidden [&_.p-timeline-event-separator]:basis-[2.2rem] [&_.p-timeline-event-connector]:w-0.5 [&_.p-timeline-event-connector]:bg-[linear-gradient(180deg,rgba(52,127,134,0.18),rgba(52,127,134,0.42))]',
            },
          }"
        >
          <template #marker="slotProps">
            <span
              class="mt-[0.15rem] inline-flex h-[1.4rem] w-[1.4rem] items-center justify-center rounded-full border border-[rgba(34,66,72,0.1)] bg-[rgba(255,250,242,0.96)]"
            >
              <span
                class="h-3 w-3 rounded-full"
                :class="activityMarkerClass(slotProps.item)"
              ></span>
            </span>
          </template>
          <template #content="slotProps">
            <details
              class="overflow-hidden rounded-2xl border border-[rgba(34,66,72,0.08)] bg-[rgba(255,250,242,0.8)]"
              :open="shouldAutoOpenActivity(slotProps.item)"
            >
              <summary class="grid cursor-pointer list-none grid-cols-[minmax(0,1fr)_auto] items-start gap-[0.7rem] px-[0.9rem] py-[0.8rem] max-[1023px]:grid-cols-1 max-sm:px-3 max-sm:py-3">
                <div class="min-w-0">
                  <div
                    v-if="isShellActivity(slotProps.item)"
                    class="max-w-full overflow-x-auto overscroll-x-contain [-ms-overflow-style:none] [scrollbar-width:none]"
                  >
                    <p
                      class="m-0 inline-flex min-w-full items-center gap-2 whitespace-nowrap font-mono text-[0.92rem] font-medium text-[color:var(--app-accent-deep)]"
                    >
                      <span class="shrink-0 text-[color:var(--app-accent)]">Ran</span>
                      <span class="text-[color:var(--app-accent-deep)]">
                        {{ shellCommand(slotProps.item) || slotProps.item.title }}
                      </span>
                    </p>
                  </div>
                  <p
                    v-else
                    class="m-0"
                    :class="activitySummaryPrimaryClass(slotProps.item)"
                  >
                    {{ activitySummaryPrimary(slotProps.item) }}
                  </p>
                  <p
                    v-if="activitySummarySecondary(slotProps.item)"
                    class="mt-1 mb-0 break-words whitespace-pre-wrap text-[color:var(--app-text-soft)]"
                  >
                    {{ activitySummarySecondary(slotProps.item) }}
                  </p>
                </div>
              </summary>

              <div
                v-if="isShellActivity(slotProps.item)"
                class="grid gap-[0.7rem] border-t border-[rgba(34,66,72,0.08)] px-[0.9rem] pb-[0.9rem] pl-[2.35rem] max-[1023px]:pl-4 max-sm:px-3 max-sm:pb-3"
              >
                <HighlightedCodeBlock
                  v-if="shellCommand(slotProps.item)"
                  :content="shellCommand(slotProps.item)"
                  label="Command"
                  language="bash"
                  max-height="compact"
                  :copy-aria-label="`Copy command ${shellCommand(slotProps.item)}`"
                />

                <HighlightedCodeBlock
                  v-if="hasShellTranscript(slotProps.item)"
                  :content="shellOutputTranscript(slotProps.item)"
                  label="Output"
                  :meta-label="shellRuntime(slotProps.item)"
                  auto-detect
                  :copy-aria-label="`Copy output from ${shellCommand(slotProps.item) || slotProps.item.title}`"
                />

                <HighlightedCodeBlock
                  v-if="!hasShellTranscript(slotProps.item) && slotProps.item.stdout"
                  :content="slotProps.item.stdout"
                  label="Stdout"
                  :meta-label="shellRuntime(slotProps.item)"
                  auto-detect
                  :copy-aria-label="`Copy stdout from ${shellCommand(slotProps.item) || slotProps.item.title}`"
                />

                <HighlightedCodeBlock
                  v-if="!hasShellTranscript(slotProps.item) && slotProps.item.stderr"
                  :content="slotProps.item.stderr"
                  label="Stderr"
                  tone="danger"
                  auto-detect
                  :copy-aria-label="`Copy stderr from ${shellCommand(slotProps.item) || slotProps.item.title}`"
                />

                <p
                  v-for="note in slotProps.item.meta"
                  :key="`${slotProps.item.id}-${note}`"
                  class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                >
                  {{ note }}
                </p>
              </div>

              <div
                v-if="activityUsesMarkdown(slotProps.item) || isApprovalActivity(slotProps.item)"
                class="grid gap-[0.55rem] border-t border-[rgba(34,66,72,0.08)] px-[0.9rem] pb-[0.9rem] pl-[2.35rem] max-[1023px]:pl-4 max-sm:px-3 max-sm:pb-3"
              >
                <div
                  v-if="activityUsesMarkdown(slotProps.item) && slotProps.item.detail"
                  class="markdown-prose"
                  @click="onMarkdownClick"
                  v-html="renderActivityMarkdown(slotProps.item.detail)"
                ></div>
                <div v-if="isApprovalActivity(slotProps.item)" class="grid gap-[0.7rem]">
                  <p
                    v-if="approvalMessage(slotProps.item)"
                    class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                  >
                    {{ approvalMessage(slotProps.item) }}
                  </p>
                  <p
                    v-if="approvalHasUrl(slotProps.item)"
                    class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                  >
                    Open
                    <a
                      :href="approvalUrl(slotProps.item)"
                      target="_blank"
                      rel="noreferrer"
                    >
                      {{ approvalUrl(slotProps.item) }}
                    </a>
                  </p>
                  <div
                    v-if="approvalSchemaPreview(slotProps.item)"
                    class="grid gap-[0.3rem]"
                  >
                    <p class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]">Requested schema</p>
                    <ScrollPanel class="w-full">
                      <pre class="rounded-[0.85rem] bg-[rgba(17,38,42,0.94)] px-[0.9rem] py-[0.8rem] font-mono text-[0.86rem] leading-[1.55] break-words whitespace-pre-wrap text-[#f2f5f6]">{{
                        approvalSchemaPreview(slotProps.item)
                      }}</pre>
                    </ScrollPanel>
                  </div>
                  <div
                    v-if="approvalUsesStructuredForm(slotProps.item) && slotProps.item.approval"
                    class="grid gap-[0.7rem]"
                  >
                    <label
                      v-for="field in slotProps.item.approval.formFields"
                      :key="`${slotProps.item.id}-${field.id}`"
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
                        @input="onApprovalInput(field, $event, slotProps.item)"
                      />
                      <input
                        v-else-if="field.kind === 'number'"
                        class="w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-[0.8rem] py-[0.7rem] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
                        :step="field.integer ? 1 : 'any'"
                        :min="field.min ?? undefined"
                        :max="field.max ?? undefined"
                        type="number"
                        :value="approvalFieldValue(field)"
                        @input="onApprovalInput(field, $event, slotProps.item)"
                      />
                      <select
                        v-else-if="field.kind === 'boolean' || field.kind === 'select'"
                        class="w-full rounded-[0.85rem] border border-[rgba(34,66,72,0.12)] bg-[rgba(255,252,247,0.9)] px-[0.8rem] py-[0.7rem] text-[color:var(--app-text)] outline-none focus:border-[rgba(21,94,99,0.35)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.08)]"
                        :value="approvalFieldValue(field)"
                        @change="onApprovalSelect(field, $event, slotProps.item)"
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
                        @change="onApprovalMultiSelect(field, $event, slotProps.item)"
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
                    v-if="approvalUsesJsonFallback(slotProps.item)"
                    class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                  >
                    JSON response
                  </p>
                  <Textarea
                    v-if="approvalUsesJsonFallback(slotProps.item) && slotProps.item.approval"
                    v-model="slotProps.item.approval.responseDraft"
                    auto-resize
                    fluid
                    rows="5"
                  />
                  <p
                    v-if="slotProps.item.approval?.validationError"
                    class="m-0 text-[0.84rem] leading-[1.45] text-[#bc5f38]"
                  >
                    {{ slotProps.item.approval.validationError }}
                  </p>
                  <div v-if="slotProps.item.approval" class="flex flex-wrap gap-2">
                    <Button
                      v-for="option in slotProps.item.approval.options"
                      :key="`${slotProps.item.id}-${option.value}`"
                      :label="option.label"
                      size="small"
                      :severity="option.value === 'decline' || option.value === 'cancel' ? 'secondary' : undefined"
                      :outlined="option.value === 'decline' || option.value === 'cancel'"
                      @click="submitApproval(slotProps.item, option.value)"
                    />
                  </div>
                </div>
              </div>

              <div
                v-if="hasFileChangeDiffs(slotProps.item)"
                class="grid gap-[0.7rem] border-t border-[rgba(34,66,72,0.08)] px-[0.9rem] pb-[0.9rem] pl-[2.35rem] max-[1023px]:pl-4 max-sm:px-3 max-sm:pb-3"
              >
                <article
                  v-for="change in fileChangeRecords(slotProps.item)"
                  :key="`${slotProps.item.id}-${change.path}-${change.kind.type}-${change.kind.move_path ?? ''}`"
                  class="grid gap-[0.45rem] rounded-[1rem] border border-[rgba(34,66,72,0.08)] bg-[rgba(255,255,255,0.38)] p-3"
                >
                  <div class="flex items-start justify-between gap-3 max-[1023px]:flex-col max-[1023px]:items-stretch">
                    <div class="min-w-0">
                      <div class="flex flex-wrap items-center gap-2">
                        <p class="m-0 text-[0.78rem] font-bold uppercase tracking-[0.08em] text-[color:var(--app-text-soft)]">
                          {{ fileChangeKindLabel(change) }}
                        </p>
                        <span
                          v-if="fileChangeMetaLabel(change)"
                          class="inline-flex max-w-full items-center rounded-full border border-[rgba(34,66,72,0.1)] bg-[rgba(21,94,99,0.08)] px-2.5 py-1 text-[0.74rem] font-medium text-[color:var(--app-accent-deep)]"
                        >
                          <span class="truncate">
                            {{ fileChangeMetaLabel(change) }}
                          </span>
                        </span>
                      </div>
                      <p class="mt-1 mb-0 break-all font-mono text-[0.84rem] text-[color:var(--app-text)]">
                        {{ change.path }}
                      </p>
                    </div>
                  </div>
                  <HighlightedCodeBlock
                    v-if="change.diff"
                    :content="change.diff"
                    label="Diff"
                    language="diff"
                    :copy-aria-label="`Copy diff for ${change.path}`"
                  />
                </article>
              </div>

              <div
                v-if="
                  !isShellActivity(slotProps.item) &&
                  (Boolean(slotProps.item.command) ||
                    Boolean(slotProps.item.cwd) ||
                    Boolean(slotProps.item.stdout) ||
                    Boolean(slotProps.item.stderr) ||
                    slotProps.item.meta.length > 0)
                "
                class="grid gap-[0.55rem] border-t border-[rgba(34,66,72,0.08)] px-[0.9rem] pb-[0.9rem] pl-[2.35rem] max-[1023px]:pl-4 max-sm:px-3 max-sm:pb-3"
              >
                <p
                  v-if="slotProps.item.command"
                  class="m-0 break-words whitespace-pre-wrap font-mono text-[0.9rem] text-[color:var(--app-accent-deep)]"
                >
                  {{ slotProps.item.command }}
                </p>
                <p
                  v-if="slotProps.item.cwd"
                  class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                >
                  cwd {{ slotProps.item.cwd }}
                </p>
                <p
                  v-for="note in slotProps.item.meta"
                  :key="`${slotProps.item.id}-${note}`"
                  class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                >
                  {{ note }}
                </p>

                <div v-if="slotProps.item.stdout" class="grid gap-[0.3rem]">
                  <p class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]">stdout</p>
                  <ScrollPanel class="w-full">
                    <pre class="rounded-[0.85rem] bg-[rgba(17,38,42,0.94)] px-[0.9rem] py-[0.8rem] font-mono text-[0.86rem] leading-[1.55] break-words whitespace-pre-wrap text-[#f2f5f6]">{{ slotProps.item.stdout }}</pre>
                  </ScrollPanel>
                </div>

                <div v-if="slotProps.item.stderr" class="grid gap-[0.3rem]">
                  <p class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]">stderr</p>
                  <ScrollPanel class="w-full">
                    <pre class="rounded-[0.85rem] bg-[rgba(78,31,24,0.92)] px-[0.9rem] py-[0.8rem] font-mono text-[0.86rem] leading-[1.55] break-words whitespace-pre-wrap text-[#f2f5f6]">{{ slotProps.item.stderr }}</pre>
                  </ScrollPanel>
                </div>
              </div>
            </details>
          </template>
        </Timeline>
      </div>
    </ScrollPanel>
  </section>
</template>
