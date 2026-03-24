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
    class: 'timeline-body-scrollpanel__container',
  },
  content: {
    class: 'timeline-body',
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
  return `activity-state--${activity.state}`
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
  <section class="timeline-shell">
    <div class="timeline-meta">
      <div>
        <p class="eyebrow">Current session</p>
        <div class="timeline-title-row">
          <h3>Session {{ sessionLabel }}</h3>
          <Tag
            :value="messages.length ? `${messages.length} msgs` : 'New'"
            rounded
            severity="secondary"
          />
        </div>
        <p v-if="sessionRuntime" class="activity-meta">
          {{ sessionRuntime.label }} · {{ runtimeStatusLabel(sessionRuntime.status) }}
          <span v-if="sessionRuntime.thread_id"> · {{ sessionRuntime.thread_id }}</span>
        </p>
        <p v-if="sessionRuntime?.detail" class="activity-meta">{{ sessionRuntime.detail }}</p>
        <p v-if="projectPath" class="activity-meta">{{ projectPath }}</p>
      </div>
      <div v-if="isSending" class="timeline-processing">
        <ProgressSpinner stroke-width="4" />
        <span>Working…</span>
      </div>
    </div>

    <div v-if="!messages.length && !visibleActivities.length" class="timeline-empty">
      <p class="eyebrow">Ready</p>
      <h4>Start with a local task.</h4>
      <p>
        Try asking yier to inspect this repo, summarize a file, or edit code inside the allowed
        roots.
      </p>
    </div>

    <ScrollPanel v-else class="timeline-body-scrollpanel" :pt="timelineScrollPt">
      <div class="message-stack">
        <article
          v-for="message in leadingMessages"
          :key="message.id"
          class="message-bubble"
          :class="`message-bubble--${message.role}`"
        >
          <p class="message-role">{{ message.role === 'user' ? 'You' : assistantLabel ?? 'Yier' }}</p>
          <p v-if="message.role === 'user'" class="message-content">{{ message.content }}</p>
          <div
            v-else
            class="message-content message-content--markdown"
            v-html="renderAssistantMessage(message.content)"
          ></div>
        </article>
      </div>

      <div v-if="visibleActivities.length" class="activity-panel">
        <p class="eyebrow">Run activity</p>
        <Timeline :value="visibleActivities" align="left" class="activity-timeline">
          <template #marker="slotProps">
            <span class="activity-timeline-marker">
              <span class="activity-state" :class="activityMarkerClass(slotProps.item)"></span>
            </span>
          </template>
          <template #content="slotProps">
            <details
              class="activity-item"
              :open="
                slotProps.item.state === 'running' ||
                Boolean(slotProps.item.stdout || slotProps.item.stderr) ||
                activityUsesMarkdown(slotProps.item)
              "
            >
              <summary class="activity-summary">
                <div class="activity-summary-copy">
                  <p class="activity-title">{{ slotProps.item.title }}</p>
                  <p
                    v-if="
                      !isShellActivity(slotProps.item) &&
                      slotProps.item.detail &&
                      !activityUsesMarkdown(slotProps.item)
                    "
                    class="activity-detail"
                  >
                    {{ slotProps.item.detail }}
                  </p>
                </div>
                <p
                  v-if="isShellActivity(slotProps.item) && shellCwd(slotProps.item)"
                  class="activity-summary-cwd"
                >
                  {{ shellCwd(slotProps.item) }}
                </p>
              </summary>

              <div
                v-if="isShellActivity(slotProps.item)"
                class="activity-body activity-body--terminal"
              >
                <p
                  v-if="shellCommand(slotProps.item)"
                  class="activity-command activity-command--terminal"
                >
                  <span class="activity-command-text">$ {{ shellCommand(slotProps.item) }}</span>
                  <button
                    type="button"
                    class="activity-command-copy"
                    :aria-label="`Copy command ${shellCommand(slotProps.item)}`"
                    @click="copyShellCommand(slotProps.item)"
                  >
                    {{ isCopied(slotProps.item.id) ? 'Copied' : 'Copy' }}
                  </button>
                </p>

                <div
                  v-if="hasShellTranscript(slotProps.item)"
                  class="activity-stream activity-stream--terminal"
                >
                  <ScrollPanel class="activity-stream-panel activity-stream-panel--terminal">
                    <pre class="activity-stream-output">{{
                      shellOutputTranscript(slotProps.item)
                    }}</pre>
                  </ScrollPanel>
                  <span v-if="shellRuntime(slotProps.item)" class="activity-runtime">
                    {{ shellRuntime(slotProps.item) }}
                  </span>
                </div>

                <div
                  v-if="!hasShellTranscript(slotProps.item) && slotProps.item.stdout"
                  class="activity-stream activity-stream--terminal"
                >
                  <ScrollPanel class="activity-stream-panel activity-stream-panel--terminal">
                    <pre class="activity-stream-output">{{ slotProps.item.stdout }}</pre>
                  </ScrollPanel>
                  <span v-if="shellRuntime(slotProps.item)" class="activity-runtime">
                    {{ shellRuntime(slotProps.item) }}
                  </span>
                </div>

                <div
                  v-if="!hasShellTranscript(slotProps.item) && slotProps.item.stderr"
                  class="activity-stream activity-stream--stderr activity-stream--terminal"
                >
                  <ScrollPanel class="activity-stream-panel activity-stream-panel--terminal">
                    <pre class="activity-stream-output">{{ slotProps.item.stderr }}</pre>
                  </ScrollPanel>
                </div>

                <p
                  v-for="note in slotProps.item.meta"
                  :key="`${slotProps.item.id}-${note}`"
                  class="activity-meta"
                >
                  {{ note }}
                </p>
              </div>

              <div v-else class="activity-body">
                <div
                  v-if="activityUsesMarkdown(slotProps.item) && slotProps.item.detail"
                  class="message-content message-content--markdown"
                  v-html="renderActivityMarkdown(slotProps.item.detail)"
                ></div>
                <div v-if="isApprovalActivity(slotProps.item)" class="approval-card">
                  <p v-if="approvalMessage(slotProps.item)" class="activity-meta">
                    {{ approvalMessage(slotProps.item) }}
                  </p>
                  <p v-if="approvalHasUrl(slotProps.item)" class="activity-meta">
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
                    class="activity-stream"
                  >
                    <p class="activity-stream-label">Requested schema</p>
                    <ScrollPanel class="activity-stream-panel">
                      <pre class="activity-stream-output">{{
                        approvalSchemaPreview(slotProps.item)
                      }}</pre>
                    </ScrollPanel>
                  </div>
                  <div
                    v-if="approvalUsesStructuredForm(slotProps.item) && slotProps.item.approval"
                    class="approval-form"
                  >
                    <label
                      v-for="field in slotProps.item.approval.formFields"
                      :key="`${slotProps.item.id}-${field.id}`"
                      class="approval-field"
                    >
                      <span class="approval-field-label">
                        {{ field.label }}
                        <span v-if="field.required" class="approval-field-required">*</span>
                      </span>
                      <span v-if="approvalFieldPrompt(field)" class="approval-field-prompt">
                        {{ approvalFieldPrompt(field) }}
                      </span>
                      <input
                        v-if="field.kind === 'text'"
                        class="approval-input"
                        type="text"
                        :value="approvalFieldValue(field)"
                        @input="onApprovalInput(field, $event, slotProps.item)"
                      />
                      <input
                        v-else-if="field.kind === 'number'"
                        class="approval-input"
                        :step="field.integer ? 1 : 'any'"
                        :min="field.min ?? undefined"
                        :max="field.max ?? undefined"
                        type="number"
                        :value="approvalFieldValue(field)"
                        @input="onApprovalInput(field, $event, slotProps.item)"
                      />
                      <select
                        v-else-if="field.kind === 'boolean' || field.kind === 'select'"
                        class="approval-select"
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
                        class="approval-select approval-select--multiple"
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
                    class="activity-stream-label"
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
                    class="approval-validation"
                  >
                    {{ slotProps.item.approval.validationError }}
                  </p>
                  <div v-if="slotProps.item.approval" class="approval-actions">
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

                <p v-if="slotProps.item.command" class="activity-command">
                  {{ slotProps.item.command }}
                </p>
                <p v-if="slotProps.item.cwd" class="activity-meta">cwd {{ slotProps.item.cwd }}</p>
                <p
                  v-for="note in slotProps.item.meta"
                  :key="`${slotProps.item.id}-${note}`"
                  class="activity-meta"
                >
                  {{ note }}
                </p>

                <div v-if="slotProps.item.stdout" class="activity-stream">
                  <p class="activity-stream-label">stdout</p>
                  <ScrollPanel class="activity-stream-panel">
                    <pre class="activity-stream-output">{{ slotProps.item.stdout }}</pre>
                  </ScrollPanel>
                </div>

                <div v-if="slotProps.item.stderr" class="activity-stream activity-stream--stderr">
                  <p class="activity-stream-label">stderr</p>
                  <ScrollPanel class="activity-stream-panel">
                    <pre class="activity-stream-output">{{ slotProps.item.stderr }}</pre>
                  </ScrollPanel>
                </div>
              </div>
            </details>
          </template>
        </Timeline>
      </div>

      <div v-if="trailingAssistantMessage" class="message-stack">
        <article
          :key="trailingAssistantMessage.id"
          class="message-bubble message-bubble--assistant"
        >
          <p class="message-role">{{ assistantLabel ?? 'Yier' }}</p>
          <div
            class="message-content message-content--markdown"
            v-html="renderAssistantMessage(trailingAssistantMessage.content)"
          ></div>
        </article>
      </div>
    </ScrollPanel>
  </section>
</template>
