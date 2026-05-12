<script setup lang="ts">
import { computed } from 'vue'

import type { CodexQueuedFollowup, CodexWorkMode } from '../types'

const model = defineModel<string>({ required: true })

const props = defineProps<{
  disabled?: boolean
  busy?: boolean
  isWorking?: boolean
  mode: CodexWorkMode
  queuedFollowups: CodexQueuedFollowup[]
}>()

const emit = defineEmits<{
  sendPrompt: [prompt: string]
  steerPrompt: [prompt: string]
  enqueueFollowup: [prompt: string]
  removeFollowup: [messageId: string]
  interruptTurn: []
  setMode: [mode: CodexWorkMode]
}>()

const canSubmitText = computed(() => model.value.trim().length > 0 && !props.disabled)
const canSend = computed(() => canSubmitText.value && !props.isWorking && !props.busy)
const canSteer = computed(() => canSubmitText.value && !props.busy)
const canQueue = computed(() => canSubmitText.value && !props.busy)

function submitSend() {
  if (!canSend.value) {
    return
  }
  emit('sendPrompt', model.value)
  model.value = ''
}

function submitSteer() {
  if (!canSteer.value) {
    return
  }
  emit('steerPrompt', model.value)
  model.value = ''
}

function submitQueue() {
  if (!canQueue.value) {
    return
  }
  emit('enqueueFollowup', model.value)
  model.value = ''
}

function onKeydown(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    submitSend()
  }
}

function followupText(followup: CodexQueuedFollowup) {
  const text = typeof followup.text === 'string' ? followup.text : followup.prompt
  return typeof text === 'string' ? text : ''
}

function followupId(followup: CodexQueuedFollowup, index: number) {
  return followup.id || `followup-${index}`
}
</script>

<template>
  <section class="border-t border-[color:var(--app-border)] bg-[rgba(255,253,247,0.94)] px-4 py-3">
    <div class="mx-auto grid max-w-5xl gap-3">
      <div v-if="queuedFollowups.length" class="grid gap-2">
        <article
          v-for="(followup, index) in queuedFollowups"
          :key="followupId(followup, index)"
          class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-lg border border-[rgba(21,94,99,0.12)] bg-[rgba(21,94,99,0.06)] px-3 py-2"
        >
          <p class="m-0 min-w-0 truncate text-sm text-[color:var(--app-text)]">
            {{ followupText(followup) }}
          </p>
          <button
            type="button"
            class="inline-flex h-7 w-7 items-center justify-center rounded-md text-[color:var(--app-text-soft)] hover:bg-white hover:text-red-700"
            aria-label="Remove queued follow-up"
            :disabled="busy"
            @click="emit('removeFollowup', followupId(followup, index))"
          >
            <i class="pi pi-times text-xs"></i>
          </button>
        </article>
      </div>

      <div class="grid gap-2 rounded-xl border border-[color:var(--app-border)] bg-white p-2 shadow-[0_10px_28px_rgba(24,44,48,0.06)]">
        <textarea
          v-model="model"
          class="min-h-24 w-full resize-y rounded-lg border-0 bg-transparent px-2 py-2 text-sm leading-6 text-[color:var(--app-text)] outline-none"
          :disabled="disabled"
          placeholder="Ask Codex to work in this thread..."
          @keydown="onKeydown"
        ></textarea>

        <div class="flex flex-wrap items-center justify-between gap-2">
          <div class="inline-flex rounded-lg border border-[color:var(--app-border)] bg-[rgba(255,253,247,0.86)] p-0.5">
            <button
              type="button"
              class="h-8 rounded-md px-3 text-sm font-semibold transition"
              :class="mode === 'build' ? 'bg-[color:var(--app-accent)] text-white' : 'text-[color:var(--app-text-soft)] hover:text-[color:var(--app-text)]'"
              :disabled="busy || disabled"
              @click="emit('setMode', 'build')"
            >
              Build
            </button>
            <button
              type="button"
              class="h-8 rounded-md px-3 text-sm font-semibold transition"
              :class="mode === 'plan' ? 'bg-[color:var(--app-accent)] text-white' : 'text-[color:var(--app-text-soft)] hover:text-[color:var(--app-text)]'"
              :disabled="busy || disabled"
              @click="emit('setMode', 'plan')"
            >
              Plan
            </button>
          </div>

          <div class="flex flex-wrap items-center gap-2">
            <button
              v-if="isWorking"
              type="button"
              class="inline-flex h-9 items-center gap-2 rounded-lg border border-red-200 bg-white px-3 text-sm font-semibold text-red-700 transition hover:bg-red-50"
              :disabled="busy || disabled"
              @click="emit('interruptTurn')"
            >
              <i class="pi pi-stop-circle text-xs"></i>
              <span>Interrupt</span>
            </button>
            <button
              type="button"
              class="inline-flex h-9 items-center gap-2 rounded-lg border border-[color:var(--app-border)] bg-white px-3 text-sm font-semibold text-[color:var(--app-text)] transition hover:border-[color:var(--app-accent)] disabled:cursor-not-allowed disabled:opacity-45"
              :disabled="!canQueue"
              @click="submitQueue"
            >
              <i class="pi pi-clock text-xs"></i>
              <span>Queue</span>
            </button>
            <button
              type="button"
              class="inline-flex h-9 items-center gap-2 rounded-lg border border-[color:var(--app-border)] bg-white px-3 text-sm font-semibold text-[color:var(--app-text)] transition hover:border-[color:var(--app-accent)] disabled:cursor-not-allowed disabled:opacity-45"
              :disabled="!canSteer"
              @click="submitSteer"
            >
              <i class="pi pi-directions text-xs"></i>
              <span>Steer</span>
            </button>
            <button
              type="button"
              class="inline-flex h-9 items-center gap-2 rounded-lg bg-[color:var(--app-accent)] px-3 text-sm font-semibold text-white transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-45"
              :disabled="!canSend"
              @click="submitSend"
            >
              <i class="pi pi-arrow-up text-xs"></i>
              <span>Send</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

