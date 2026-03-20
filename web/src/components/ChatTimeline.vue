<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import ProgressSpinner from 'primevue/progressspinner'
import Tag from 'primevue/tag'

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

const timelineBody = ref<HTMLElement | null>(null)
const shouldStickToBottom = ref(true)
const bottomThreshold = 72

function renderAssistantMessage(content: string) {
  return markdown.render(content)
}

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
</script>

<template>
  <section class="timeline-shell">
    <div class="timeline-meta">
      <div>
        <p class="eyebrow">Current session</p>
        <div class="timeline-title-row">
          <h3>Session {{ sessionLabel }}</h3>
          <Tag :value="messages.length ? `${messages.length} msgs` : 'New'" rounded severity="secondary" />
        </div>
      </div>
      <div v-if="isSending" class="timeline-processing">
        <ProgressSpinner stroke-width="4" />
        <span>Working…</span>
      </div>
    </div>

    <div v-if="!messages.length && !activities.length" class="timeline-empty">
      <p class="eyebrow">Ready</p>
      <h4>Start with a local task.</h4>
      <p>
        Try asking yier to inspect this repo, summarize a file, or edit code inside the allowed
        roots.
      </p>
    </div>

    <div v-else ref="timelineBody" class="timeline-body" @scroll="onTimelineScroll">
      <div class="message-stack">
        <article
          v-for="message in messages"
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

      <div v-if="activities.length" class="activity-panel">
        <p class="eyebrow">Run activity</p>
        <div class="activity-list">
          <details
            v-for="activity in activities"
            :key="activity.id"
            class="activity-item"
            :open="activity.state === 'running' || Boolean(activity.stdout || activity.stderr)"
          >
            <summary class="activity-summary">
              <span class="activity-state" :class="`activity-state--${activity.state}`"></span>
              <div class="activity-summary-copy">
                <p class="activity-title">{{ activity.title }}</p>
                <p class="activity-detail">{{ activity.detail }}</p>
              </div>
            </summary>

            <div class="activity-body">
              <p v-if="activity.command" class="activity-command">{{ activity.command }}</p>
              <p v-if="activity.cwd" class="activity-meta">cwd {{ activity.cwd }}</p>
              <p v-for="note in activity.meta" :key="`${activity.id}-${note}`" class="activity-meta">
                {{ note }}
              </p>

              <div v-if="activity.stdout" class="activity-stream">
                <p class="activity-stream-label">stdout</p>
                <pre class="activity-stream-output">{{ activity.stdout }}</pre>
              </div>

              <div v-if="activity.stderr" class="activity-stream activity-stream--stderr">
                <p class="activity-stream-label">stderr</p>
                <pre class="activity-stream-output">{{ activity.stderr }}</pre>
              </div>
            </div>
          </details>
        </div>
      </div>
    </div>
  </section>
</template>
