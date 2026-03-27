<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import Button from 'primevue/button'
import ProgressSpinner from 'primevue/progressspinner'
import ScrollPanel from 'primevue/scrollpanel'
import Tag from 'primevue/tag'
import Textarea from 'primevue/textarea'
import Timeline from 'primevue/timeline'

import type {
  ApprovalDecision,
  ApprovalFormFieldState,
  BackendRuntime,
  ChatActivity,
  UiChatMessage,
} from '../types/api'

const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
})

const props = defineProps<{
  messages: UiChatMessage[]
  activities: ChatActivity[]
  isSending: boolean
  sessionLabel: string
  sessionRuntime: BackendRuntime | null
  projectPath: string
  assistantLabel?: string
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
const copiedActivityId = ref('')
let copiedResetTimer: number | null = null
const bottomThreshold = 72
const timelineScrollPt = {
  contentContainer: {
    class: 'h-full w-full',
  },
  content: {
    class: 'flex min-h-0 flex-col gap-4 pr-[0.35rem]',
    ref: (element: HTMLElement | null) => {
      timelineBody.value = element
    },
    onScroll: onTimelineScroll,
  },
}

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

function isCopied(activityId: string) {
  return copiedActivityId.value === activityId
}

async function copyShellCommand(activity: ChatActivity) {
  const command = shellCommand(activity).trim()
  if (!command) {
    return
  }

  await navigator.clipboard.writeText(command)
  copiedActivityId.value = activity.id
  if (copiedResetTimer !== null) {
    window.clearTimeout(copiedResetTimer)
  }
  copiedResetTimer = window.setTimeout(() => {
    if (copiedActivityId.value === activity.id) {
      copiedActivityId.value = ''
    }
    copiedResetTimer = null
  }, 1600)
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

function isHiddenActivity(activity: ChatActivity) {
  if (activity.kind === 'approval') {
    return false
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

const trailingAssistantMessage = computed(() => {
  if (!visibleActivities.value.length) {
    return null
  }

  const lastMessage = props.messages[props.messages.length - 1]
  if (!lastMessage || lastMessage.role !== 'assistant') {
    return null
  }

  return lastMessage
})

const leadingMessages = computed(() => {
  if (!trailingAssistantMessage.value) {
    return props.messages
  }
  return props.messages.slice(0, -1)
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
  () => props.activities.map((activity) => activity.id),
  (ids) => {
    if (copiedActivityId.value && !ids.includes(copiedActivityId.value)) {
      copiedActivityId.value = ''
    }
  },
)

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
</script>

<template>
  <section
    class="flex min-h-0 flex-1 flex-col gap-4 overflow-hidden rounded-3xl border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-[1.1rem] shadow-[var(--app-shadow)] backdrop-blur-[14px] max-[1023px]:rounded-[1.35rem] max-[1023px]:p-4 max-sm:gap-3 max-sm:p-3"
  >
    <div class="flex items-start justify-between gap-3 max-[1023px]:flex-col max-[1023px]:items-stretch">
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

    <div
      v-if="!messages.length && !visibleActivities.length"
      class="grid place-items-center gap-2 p-10 text-center"
    >
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
      <div class="grid gap-4">
        <article
          v-for="message in leadingMessages"
          :key="message.id"
          class="max-w-[min(48rem,85%)] rounded-[1.3rem] border p-4 max-[1023px]:max-w-full max-sm:rounded-[1.1rem] max-sm:p-3"
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
            class="prose prose-stone max-w-none prose-headings:text-[color:var(--app-text)] prose-p:text-[color:var(--app-text)] prose-li:text-[color:var(--app-text)] prose-strong:text-[color:var(--app-text)] prose-a:text-[color:var(--app-accent)] prose-code:rounded-md prose-code:bg-[rgba(21,94,99,0.1)] prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[color:var(--app-accent-deep)] prose-pre:rounded-2xl prose-pre:bg-[rgba(17,38,42,0.92)] prose-pre:px-4 prose-pre:py-3 prose-pre:text-[#f2f5f6] prose-pre:shadow-[inset_0_0_0_1px_rgba(255,255,255,0.06)] prose-blockquote:border-l-[3px] prose-blockquote:border-[rgba(21,94,99,0.32)] prose-blockquote:text-[color:var(--app-text-soft)]"
            v-html="renderAssistantMessage(message.content)"
          ></div>
        </article>
      </div>

      <div
        v-if="visibleActivities.length"
        class="border-t border-[color:var(--app-border)] pt-[0.4rem]"
      >
        <p class="eyebrow">Run activity</p>
        <Timeline
          :value="visibleActivities"
          align="left"
          class="mt-[0.85rem]"
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
              :open="
                slotProps.item.state === 'running' ||
                Boolean(slotProps.item.stdout || slotProps.item.stderr) ||
                activityUsesMarkdown(slotProps.item)
              "
            >
              <summary class="grid cursor-pointer list-none grid-cols-[minmax(0,1fr)_auto] items-start gap-[0.7rem] px-[0.9rem] py-[0.8rem] max-[1023px]:grid-cols-1 max-sm:px-3 max-sm:py-3">
                <div class="min-w-0">
                  <p class="m-0 font-bold">{{ slotProps.item.title }}</p>
                  <p
                    v-if="
                      !isShellActivity(slotProps.item) &&
                      slotProps.item.detail &&
                      !activityUsesMarkdown(slotProps.item)
                    "
                    class="mt-1 mb-0 break-words whitespace-pre-wrap text-[color:var(--app-text-soft)]"
                  >
                    {{ slotProps.item.detail }}
                  </p>
                </div>
                <p
                  v-if="isShellActivity(slotProps.item) && shellCwd(slotProps.item)"
                  class="m-0 max-w-[min(40vw,24rem)] overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[0.78rem] text-[color:var(--app-text-soft)] max-[1023px]:max-w-full max-[1023px]:whitespace-normal max-[1023px]:break-all"
                >
                  {{ shellCwd(slotProps.item) }}
                </p>
              </summary>

              <div
                v-if="isShellActivity(slotProps.item)"
                class="grid gap-[0.7rem] border-t border-[rgba(34,66,72,0.08)] px-[0.9rem] pb-[0.9rem] pl-[2.35rem] max-[1023px]:pl-4 max-sm:px-3 max-sm:pb-3"
              >
                <p
                  v-if="shellCommand(slotProps.item)"
                  class="m-0 flex items-center justify-between gap-3 rounded-t-[0.85rem] bg-[linear-gradient(180deg,rgba(16,33,37,0.98),rgba(21,43,48,0.98))] px-4 py-[0.85rem] font-mono text-[0.9rem] text-[#f7fffc] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.04)] max-[1023px]:flex-col max-[1023px]:items-stretch max-sm:px-3"
                >
                  <span class="min-w-0 flex-1 break-words whitespace-pre-wrap">
                    $ {{ shellCommand(slotProps.item) }}
                  </span>
                  <button
                    type="button"
                    class="shrink-0 rounded-full border-0 bg-white/12 px-[0.7rem] py-[0.28rem] text-[0.76rem] font-bold text-[#f7fffc] transition hover:bg-white/18 active:translate-y-px max-[1023px]:self-start"
                    :aria-label="`Copy command ${shellCommand(slotProps.item)}`"
                    @click="copyShellCommand(slotProps.item)"
                  >
                    {{ isCopied(slotProps.item.id) ? 'Copied' : 'Copy' }}
                  </button>
                </p>

                <div
                  v-if="hasShellTranscript(slotProps.item)"
                  class="relative grid gap-0"
                >
                  <ScrollPanel class="max-h-72 w-full">
                    <pre class="min-h-16 rounded-b-[0.85rem] bg-[rgba(17,38,42,0.94)] px-[0.9rem] pt-[0.3rem] pb-[1.7rem] font-mono text-[0.86rem] leading-[1.55] break-words whitespace-pre-wrap text-[#f2f5f6]">{{
                      shellOutputTranscript(slotProps.item)
                    }}</pre>
                  </ScrollPanel>
                  <span
                    v-if="shellRuntime(slotProps.item)"
                    class="absolute right-[0.85rem] bottom-[0.7rem] font-mono text-[0.74rem] leading-none text-[rgba(242,245,246,0.72)]"
                  >
                    {{ shellRuntime(slotProps.item) }}
                  </span>
                </div>

                <div
                  v-if="!hasShellTranscript(slotProps.item) && slotProps.item.stdout"
                  class="relative grid gap-0"
                >
                  <ScrollPanel class="max-h-72 w-full">
                    <pre class="min-h-16 rounded-b-[0.85rem] bg-[rgba(17,38,42,0.94)] px-[0.9rem] pt-[0.3rem] pb-[1.7rem] font-mono text-[0.86rem] leading-[1.55] break-words whitespace-pre-wrap text-[#f2f5f6]">{{ slotProps.item.stdout }}</pre>
                  </ScrollPanel>
                  <span
                    v-if="shellRuntime(slotProps.item)"
                    class="absolute right-[0.85rem] bottom-[0.7rem] font-mono text-[0.74rem] leading-none text-[rgba(242,245,246,0.72)]"
                  >
                    {{ shellRuntime(slotProps.item) }}
                  </span>
                </div>

                <div
                  v-if="!hasShellTranscript(slotProps.item) && slotProps.item.stderr"
                  class="grid gap-0"
                >
                  <ScrollPanel class="max-h-72 w-full">
                    <pre class="min-h-16 rounded-b-[0.85rem] bg-[rgba(78,31,24,0.92)] px-[0.9rem] pt-[0.3rem] pb-[1.7rem] font-mono text-[0.86rem] leading-[1.55] break-words whitespace-pre-wrap text-[#f2f5f6]">{{ slotProps.item.stderr }}</pre>
                  </ScrollPanel>
                </div>

                <p
                  v-for="note in slotProps.item.meta"
                  :key="`${slotProps.item.id}-${note}`"
                  class="m-0 text-[0.84rem] text-[color:var(--app-text-soft)]"
                >
                  {{ note }}
                </p>
              </div>

              <div class="grid gap-[0.55rem] border-t border-[rgba(34,66,72,0.08)] px-[0.9rem] pb-[0.9rem] pl-[2.35rem] max-[1023px]:pl-4 max-sm:px-3 max-sm:pb-3">
                <div
                  v-if="activityUsesMarkdown(slotProps.item) && slotProps.item.detail"
                  class="prose prose-stone max-w-none prose-headings:text-[color:var(--app-text)] prose-p:text-[color:var(--app-text)] prose-li:text-[color:var(--app-text)] prose-strong:text-[color:var(--app-text)] prose-a:text-[color:var(--app-accent)] prose-code:rounded-md prose-code:bg-[rgba(21,94,99,0.1)] prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[color:var(--app-accent-deep)] prose-pre:rounded-2xl prose-pre:bg-[rgba(17,38,42,0.92)] prose-pre:px-4 prose-pre:py-3 prose-pre:text-[#f2f5f6] prose-pre:shadow-[inset_0_0_0_1px_rgba(255,255,255,0.06)] prose-blockquote:border-l-[3px] prose-blockquote:border-[rgba(21,94,99,0.32)] prose-blockquote:text-[color:var(--app-text-soft)]"
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

      <div v-if="trailingAssistantMessage" class="grid gap-4">
        <article
          :key="trailingAssistantMessage.id"
          class="max-w-[min(48rem,85%)] rounded-[1.3rem] border border-[rgba(153,125,93,0.15)] bg-[color:var(--app-panel-strong)] p-4 max-[1023px]:max-w-full max-sm:rounded-[1.1rem] max-sm:p-3"
        >
          <p class="mb-[0.35rem] mt-0 text-[0.82rem] font-bold uppercase tracking-[0.08em] text-[color:var(--app-text-soft)]">
            {{ assistantLabel ?? 'Yier' }}
          </p>
          <div
            class="prose prose-stone max-w-none prose-headings:text-[color:var(--app-text)] prose-p:text-[color:var(--app-text)] prose-li:text-[color:var(--app-text)] prose-strong:text-[color:var(--app-text)] prose-a:text-[color:var(--app-accent)] prose-code:rounded-md prose-code:bg-[rgba(21,94,99,0.1)] prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[color:var(--app-accent-deep)] prose-pre:rounded-2xl prose-pre:bg-[rgba(17,38,42,0.92)] prose-pre:px-4 prose-pre:py-3 prose-pre:text-[#f2f5f6] prose-pre:shadow-[inset_0_0_0_1px_rgba(255,255,255,0.06)] prose-blockquote:border-l-[3px] prose-blockquote:border-[rgba(21,94,99,0.32)] prose-blockquote:text-[color:var(--app-text-soft)]"
            v-html="renderAssistantMessage(trailingAssistantMessage.content)"
          ></div>
        </article>
      </div>
    </ScrollPanel>
  </section>
</template>
