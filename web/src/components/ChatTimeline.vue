<script setup lang="ts">
import ProgressSpinner from 'primevue/progressspinner'
import Tag from 'primevue/tag'

import type { ChatActivity, UiChatMessage } from '../types/api'

defineProps<{
  messages: UiChatMessage[]
  activities: ChatActivity[]
  isSending: boolean
  sessionLabel: string
}>()
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

    <div v-else class="message-stack">
      <article
        v-for="message in messages"
        :key="message.id"
        class="message-bubble"
        :class="`message-bubble--${message.role}`"
      >
        <p class="message-role">{{ message.role === 'user' ? 'You' : 'Yier' }}</p>
        <p class="message-content">{{ message.content }}</p>
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
  </section>
</template>
