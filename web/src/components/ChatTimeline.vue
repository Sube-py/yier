<script setup lang="ts">
import MarkdownIt from 'markdown-it'
import ProgressSpinner from 'primevue/progressspinner'
import Tag from 'primevue/tag'

import type { ChatActivity, UiChatMessage } from '../types/api'

const markdown = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
})

defineProps<{
  messages: UiChatMessage[]
  activities: ChatActivity[]
  isSending: boolean
  sessionLabel: string
}>()

function renderAssistantMessage(content: string) {
  return markdown.render(content)
}
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

    <div v-if="!messages.length" class="timeline-empty">
      <p class="eyebrow">Ready</p>
      <h4>Start with a local task.</h4>
      <p>
        Try asking yier to inspect this repo, summarize a file, or edit code inside the allowed
        roots.
      </p>
    </div>

    <div v-else class="timeline-body">
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
        <ul class="activity-list">
          <li v-for="activity in activities" :key="activity.id" class="activity-item">
            <span class="activity-state" :class="`activity-state--${activity.state}`"></span>
            <div>
              <p class="activity-title">{{ activity.title }}</p>
              <p class="activity-detail">{{ activity.detail }}</p>
            </div>
          </li>
        </ul>
      </div>
    </div>
  </section>
</template>
