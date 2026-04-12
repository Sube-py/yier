<script setup lang="ts">
import { computed } from 'vue'

import Fieldset from 'primevue/fieldset'

import type { UiChatMessage } from '../../types/api'

const props = defineProps<{
  message: UiChatMessage
  onMarkdownClick?: (event: MouseEvent) => void
  renderMarkdown: (content: string) => string
  showFinalSeparator?: boolean
}>()

const imageAttachments = computed(() =>
  (props.message.attachments ?? []).filter(
    (attachment) => attachment.kind === 'image' && attachment.preview_url,
  ),
)

const fileAttachments = computed(() =>
  (props.message.attachments ?? []).filter(
    (attachment) => attachment.kind !== 'image' || !attachment.preview_url,
  ),
)

function formatBytes(size?: number | null) {
  if (typeof size !== 'number' || !Number.isFinite(size) || size <= 0) {
    return ''
  }
  if (size < 1024) {
    return `${size} B`
  }
  if (size < 1024 * 1024) {
    return `${Math.round(size / 1024)} KB`
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
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
      <div
        v-if="message.content"
        class="markdown-prose [&>:first-child]:mt-0 [&>:last-child]:mb-0"
        v-html="renderMarkdown(message.content)"
        @click="onMarkdownClick"
      ></div>
      <div
        v-if="imageAttachments.length"
        class="mt-3 grid gap-2"
      >
        <img
          v-for="attachment in imageAttachments"
          :key="attachment.id ?? attachment.preview_url ?? attachment.name"
          :src="attachment.preview_url ?? ''"
          :alt="attachment.name"
          class="block max-h-80 w-full rounded-2xl border border-[rgba(34,66,72,0.1)] object-contain bg-white/70"
        />
      </div>
      <div
        v-if="fileAttachments.length"
        class="mt-3 grid gap-2"
      >
        <component
          :is="attachment.content_url ? 'a' : 'div'"
          v-for="attachment in fileAttachments"
          :key="attachment.id ?? attachment.path ?? attachment.name"
          :href="attachment.content_url ?? undefined"
          :target="attachment.content_url ? '_blank' : undefined"
          :rel="attachment.content_url ? 'noreferrer noopener' : undefined"
          class="flex min-w-0 items-center gap-2 rounded-2xl border border-[rgba(34,66,72,0.1)] bg-white/70 px-3 py-2 text-inherit no-underline"
        >
          <span class="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-[rgba(21,94,99,0.1)] text-[color:var(--app-accent-deep)]">
            <i class="pi pi-file text-sm"></i>
          </span>
          <span class="min-w-0">
            <span class="block truncate text-sm font-semibold">{{ attachment.name }}</span>
            <span
              v-if="attachment.mime_type || formatBytes(attachment.size)"
              class="block truncate text-[0.72rem] text-[color:var(--app-text-soft)]"
            >
              {{ [attachment.mime_type, formatBytes(attachment.size)].filter(Boolean).join(' · ') }}
            </span>
          </span>
        </component>
      </div>
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
