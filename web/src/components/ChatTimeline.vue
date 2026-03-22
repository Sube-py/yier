<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import ProgressSpinner from 'primevue/progressspinner'
import ScrollPanel from 'primevue/scrollpanel'
import Tag from 'primevue/tag'
import Timeline from 'primevue/timeline'

import type { ChatActivity, UiChatMessage } from '../types/api'

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

function renderAssistantMessage(content: string) {
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

function isHiddenActivity(activity: ChatActivity) {
  if (activity.kind === 'reasoning') {
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
          <p class="message-role">{{ message.role === 'user' ? 'You' : 'Yier' }}</p>
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
                Boolean(slotProps.item.stdout || slotProps.item.stderr)
              "
            >
              <summary class="activity-summary">
                <div class="activity-summary-copy">
                  <p class="activity-title">{{ slotProps.item.title }}</p>
                  <p v-if="!isShellActivity(slotProps.item) && slotProps.item.detail" class="activity-detail">
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
          <p class="message-role">Yier</p>
          <div
            class="message-content message-content--markdown"
            v-html="renderAssistantMessage(trailingAssistantMessage.content)"
          ></div>
        </article>
      </div>
    </ScrollPanel>
  </section>
</template>
