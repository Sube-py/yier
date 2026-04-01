<script setup lang="ts">
import Fieldset from 'primevue/fieldset'

import type { UiChatMessage } from '../../types/api'

defineProps<{
  message: UiChatMessage
  onMarkdownClick?: (event: MouseEvent) => void
  renderMarkdown: (content: string) => string
  showFinalSeparator?: boolean
}>()

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
</script>

<template>
  <div
    class="flex min-w-0"
    :class="message.role === 'user' ? 'justify-end' : 'justify-start'"
  >
    <Fieldset
      v-if="message.role === 'user'"
      legend="You"
      :pt="messageFieldsetPt()"
    >
      <p class="m-0 whitespace-pre-wrap leading-[1.65]">
        {{ message.content }}
      </p>
    </Fieldset>

    <div
      v-else
      class="message-bubble--assistant min-w-0 max-w-full flex-1 px-1 max-sm:px-0"
    >
      <div
        v-if="showFinalSeparator"
        class="mb-3 flex items-center gap-3 text-[0.72rem] font-semibold uppercase tracking-[0.12em] text-[color:var(--app-text-soft)]"
      >
        <span class="h-px flex-1 bg-[rgba(34,66,72,0.12)]"></span>
        <span>Final message</span>
        <span class="h-px flex-1 bg-[rgba(34,66,72,0.12)]"></span>
      </div>
      <div
        class="markdown-prose"
        v-html="renderMarkdown(message.content)"
        @click="onMarkdownClick"
      ></div>
    </div>
  </div>
</template>
