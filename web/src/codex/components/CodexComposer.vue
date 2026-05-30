<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Select from 'primevue/select'

import type {
  CodexConversationState,
  CodexPromptSubmission,
  CodexQueuedFollowup,
  CodexWorkMode,
  JsonRecord,
} from '../types'
import { isRecord } from '../lib/format'

const draft = defineModel<string>({ required: true })

const props = defineProps<{
  disabled?: boolean
  busy?: boolean
  isWorking?: boolean
  mode: CodexWorkMode
  queuedFollowups: CodexQueuedFollowup[]
  state: CodexConversationState | null
}>()

const emit = defineEmits<{
  sendPrompt: [submission: CodexPromptSubmission]
  steerPrompt: [prompt: string]
  enqueueFollowup: [prompt: string]
  removeFollowup: [messageId: string]
  interruptTurn: []
  setMode: [mode: CodexWorkMode]
}>()

const baseModelOptions = ['gpt-5.4', 'gpt-5.4-mini', 'gpt-5.3-codex', 'gpt-5.2']
const baseReasoningOptions = ['minimal', 'low', 'medium', 'high', 'xhigh']
const selectedModel = ref('')
const selectedReasoningEffort = ref('')

const latestModel = computed(() => props.state?.latestModel?.trim() || 'gpt-5.4')
const latestReasoningEffort = computed(() => props.state?.latestReasoningEffort?.trim() || 'medium')
const modelOptions = computed(() => optionItems([latestModel.value, ...baseModelOptions]))
const reasoningOptions = computed(() =>
  optionItems([latestReasoningEffort.value, ...baseReasoningOptions]),
)
const activeModel = computed(() => selectedModel.value || latestModel.value)
const activeReasoningEffort = computed(() => selectedReasoningEffort.value || latestReasoningEffort.value)
const hasDraft = computed(() => draft.value.trim().length > 0)
const canSubmitText = computed(() => hasDraft.value && !props.disabled && !props.busy)
const primaryAction = computed(() => {
  if (props.isWorking && !hasDraft.value) {
    return 'stop'
  }
  if (props.isWorking) {
    return 'queue'
  }
  return 'send'
})
const primaryLabel = computed(() => {
  if (primaryAction.value === 'stop') {
    return 'Stop'
  }
  return 'Send'
})
const primaryIcon = computed(() => {
  if (primaryAction.value === 'stop') {
    return 'pi pi-stop'
  }
  return 'pi pi-arrow-up'
})
const primaryTitle = computed(() => {
  if (primaryAction.value === 'stop') {
    return 'Stop the active response'
  }
  if (primaryAction.value === 'queue') {
    return 'Send after the active response'
  }
  return 'Send'
})
const primaryDisabled = computed(() => {
  if (primaryAction.value === 'stop') {
    return props.disabled || props.busy
  }
  return !canSubmitText.value
})
const context = computed(() => contextWindowState(props.state))

watch(
  () => props.state?.id,
  () => {
    selectedModel.value = ''
    selectedReasoningEffort.value = ''
  },
)

function sendSubmission() {
  if (!canSubmitText.value || props.isWorking) {
    return
  }
  emit('sendPrompt', {
    prompt: draft.value,
    model: activeModel.value,
    reasoningEffort: activeReasoningEffort.value,
  })
  draft.value = ''
}

function submitQueue() {
  if (!canSubmitText.value) {
    return
  }
  emit('enqueueFollowup', draft.value)
  draft.value = ''
}

function submitPrimary() {
  if (primaryAction.value === 'stop') {
    if (!primaryDisabled.value) {
      emit('interruptTurn')
    }
    return
  }
  if (primaryAction.value === 'queue') {
    submitQueue()
    return
  }
  sendSubmission()
}

function onKeydown(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    submitPrimary()
  }
}

function followupText(followup: CodexQueuedFollowup) {
  const text = typeof followup.text === 'string' ? followup.text : followup.prompt
  return typeof text === 'string' ? text : ''
}

function followupId(followup: CodexQueuedFollowup, index: number) {
  return followup.id || `followup-${index}`
}

function steerFollowup(followup: CodexQueuedFollowup, index: number) {
  if (!props.isWorking || props.busy || props.disabled) {
    return
  }
  const prompt = followupText(followup).trim()
  if (!prompt) {
    return
  }
  emit('steerPrompt', prompt)
  emit('removeFollowup', followupId(followup, index))
}

function optionItems(values: string[]) {
  return [...new Set(values.filter(Boolean))].map((value) => ({
    label: value,
    value,
  }))
}

function contextWindowState(state: CodexConversationState | null) {
  const explicit = explicitContextWindow(state)
  if (explicit) {
    return explicit
  }

  const textLength =
    state?.turns?.reduce((total, turn) => {
      const itemLength = (turn.items ?? []).reduce(
        (sum, item) => sum + JSON.stringify(item).length,
        0,
      )
      return total + itemLength
    }, 0) ?? 0
  const usedTokens = Math.round(textLength / 4)
  const totalTokens = 128_000
  const percent = Math.min(Math.round((usedTokens / totalTokens) * 100), 100)
  return {
    label: `${percent}% context`,
    detail: `~${formatTokenCount(usedTokens)} / ${formatTokenCount(totalTokens)} tokens`,
    percent,
    estimated: true,
  }
}

function explicitContextWindow(state: CodexConversationState | null) {
  const candidates = [
    state?.contextWindow,
    state?.context_window,
    state?.context,
    state?.tokenUsage,
    state?.token_usage,
  ]
  for (const candidate of candidates) {
    if (!isRecord(candidate)) {
      continue
    }
    const used = numberFromRecord(candidate, ['usedTokens', 'used_tokens', 'inputTokens', 'input_tokens', 'used'])
    const total = numberFromRecord(candidate, ['totalTokens', 'total_tokens', 'limit', 'maxTokens', 'max_tokens'])
    const percent = numberFromRecord(candidate, ['percent', 'ratio'])
    if (used != null && total != null && total > 0) {
      const computedPercent = Math.min(Math.round((used / total) * 100), 100)
      return {
        label: `${computedPercent}% context`,
        detail: `${formatTokenCount(used)} / ${formatTokenCount(total)} tokens`,
        percent: computedPercent,
        estimated: false,
      }
    }
    if (percent != null) {
      const normalized = percent <= 1 ? percent * 100 : percent
      return {
        label: `${Math.round(normalized)}% context`,
        detail: 'Context window',
        percent: Math.min(Math.round(normalized), 100),
        estimated: false,
      }
    }
  }
  return null
}

function numberFromRecord(record: JsonRecord, keys: string[]) {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value
    }
  }
  return null
}

function formatTokenCount(value: number) {
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1)}M`
  }
  if (value >= 1000) {
    return `${Math.round(value / 1000)}k`
  }
  return String(value)
}
</script>

<template>
  <section class="border-t border-[color:var(--app-border)] bg-[rgba(255,253,247,0.94)] px-4 pb-[calc(0.75rem+env(safe-area-inset-bottom))] pt-3 max-sm:px-2.5">
    <div class="mx-auto grid max-w-5xl gap-2">
      <div
        class="grid min-w-0 gap-2 rounded-2xl border border-[color:var(--app-border)] bg-white/95 p-2 shadow-[0_14px_34px_rgba(24,44,48,0.08)] transition max-sm:rounded-xl"
        data-codex-composer
      >
        <div
          v-if="queuedFollowups.length"
          class="vertical-scroll-fade-mask hide-scrollbar -mx-1 -mt-1 flex max-h-[30dvh] flex-col gap-px overflow-x-hidden overflow-y-auto rounded-t-xl border-b border-[rgba(34,66,72,0.1)] px-3 py-2 max-sm:px-2"
          data-codex-queued-followups
        >
          <article
            v-for="(followup, index) in queuedFollowups"
            :key="followupId(followup, index)"
            class="group flex min-w-0 items-center justify-between gap-2 py-0.5 text-sm"
          >
            <div class="flex min-w-0 flex-1 items-start gap-1.5">
              <span
                class="relative -ml-3 flex h-4 shrink-0 cursor-default items-center justify-center pl-3 text-[color:var(--app-text-soft)]/70"
                aria-hidden="true"
              >
                <i class="pi pi-bars pointer-events-none absolute left-0 top-1/2 -translate-y-1/2 text-[0.56rem] opacity-0 transition-opacity group-hover:opacity-100"></i>
                <i class="pi pi-clock text-[0.62rem]"></i>
              </span>
              <span class="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap leading-4 text-[color:var(--app-text-soft)]">
                {{ followupText(followup) }}
              </span>
            </div>
            <div class="flex shrink-0 items-center gap-1 opacity-80 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 sm:opacity-0">
              <button
                type="button"
                class="inline-flex h-7 shrink-0 items-center gap-1 rounded-full px-2 text-xs font-semibold text-[color:var(--app-text)] transition hover:bg-[rgba(21,94,99,0.07)] disabled:cursor-not-allowed disabled:opacity-45"
                aria-label="Steer queued follow-up"
                title="Submit without interrupting the model"
                :disabled="!isWorking || busy || disabled"
                data-codex-queued-steer
                @click="steerFollowup(followup, index)"
              >
                <i class="pi pi-directions text-[0.68rem]"></i>
                <span>Steer</span>
              </button>
              <button
                type="button"
                class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[color:var(--app-text-soft)] transition hover:bg-red-50 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-45"
                aria-label="Remove queued follow-up"
                :disabled="busy"
                data-codex-queued-remove
                @click="emit('removeFollowup', followupId(followup, index))"
              >
                <i class="pi pi-times text-[0.62rem]"></i>
              </button>
            </div>
          </article>
        </div>

        <textarea
          v-model="draft"
          class="min-h-24 w-full min-w-0 resize-y rounded-xl border-0 bg-transparent px-2 py-2 text-sm leading-6 text-[color:var(--app-text)] outline-none placeholder:text-[color:var(--app-text-soft)] max-sm:min-h-20"
          :disabled="disabled"
          :placeholder="props.isWorking ? 'Add a follow-up for the queue...' : 'Ask Codex to work in this thread...'"
          @keydown="onKeydown"
        ></textarea>

        <div class="composer-footer grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-end gap-2 max-sm:items-start" data-codex-composer-footer>
          <div class="hide-scrollbar flex min-w-0 flex-wrap items-center gap-1.5 max-sm:flex-nowrap max-sm:overflow-x-auto max-sm:overscroll-x-contain max-sm:pb-0.5" data-codex-composer-controls>
            <div
              class="grid w-[7.25rem] shrink-0 grid-cols-2 rounded-lg border border-[color:var(--app-border)] bg-[rgba(255,253,247,0.86)] p-0.5"
              data-codex-mode-switch
            >
              <button
                type="button"
                class="h-8 min-w-0 rounded-md px-2 text-sm font-semibold transition-colors"
                :class="mode === 'build' ? 'bg-[color:var(--app-accent)] text-white' : 'text-[color:var(--app-text-soft)] hover:text-[color:var(--app-text)]'"
                :disabled="busy || disabled"
                @click="emit('setMode', 'build')"
              >
                Build
              </button>
              <button
                type="button"
                class="h-8 min-w-0 rounded-md px-2 text-sm font-semibold transition-colors"
                :class="mode === 'plan' ? 'bg-[color:var(--app-accent)] text-white' : 'text-[color:var(--app-text-soft)] hover:text-[color:var(--app-text)]'"
                :disabled="busy || disabled"
                @click="emit('setMode', 'plan')"
              >
                Plan
              </button>
            </div>

            <label class="inline-flex h-8 min-w-0 max-w-full items-center gap-1.5 rounded-lg border border-[color:var(--app-border)] bg-white px-2 text-sm text-[color:var(--app-text-soft)] max-sm:w-max max-sm:max-w-none max-sm:shrink-0">
              <i class="pi pi-microchip text-[0.72rem]"></i>
              <span class="composer-footer__label--sm">Model</span>
              <Select
                v-model="selectedModel"
                :options="modelOptions"
                option-label="label"
                option-value="value"
                :placeholder="latestModel"
                size="small"
                append-to="body"
                checkmark
                class="composer-inline-select codex-composer-select max-w-36 max-sm:max-w-24"
                :disabled="busy || disabled"
                aria-label="Choose model"
                data-codex-model-select
              />
            </label>

            <label class="inline-flex h-8 min-w-0 max-w-full items-center gap-1.5 rounded-lg border border-[color:var(--app-border)] bg-white px-2 text-sm text-[color:var(--app-text-soft)] max-sm:w-max max-sm:max-w-none max-sm:shrink-0">
              <i class="pi pi-sparkles text-[0.72rem]"></i>
              <span class="composer-footer__label--sm">Reasoning</span>
              <Select
                v-model="selectedReasoningEffort"
                :options="reasoningOptions"
                option-label="label"
                option-value="value"
                :placeholder="latestReasoningEffort"
                size="small"
                append-to="body"
                checkmark
                class="composer-inline-select codex-composer-select max-w-28 capitalize max-sm:max-w-20"
                :disabled="busy || disabled"
                aria-label="Choose reasoning effort"
                data-codex-reasoning-select
              />
            </label>

            <div
              class="inline-flex h-8 min-w-36 max-w-full items-center gap-2 rounded-lg border border-[color:var(--app-border)] bg-white px-2 text-xs text-[color:var(--app-text-soft)] max-sm:shrink-0"
              :title="`${context.detail}${context.estimated ? ' estimated' : ''}`"
              data-codex-context-window
            >
              <span class="h-1.5 min-w-12 overflow-hidden rounded-full bg-[rgba(34,66,72,0.12)]">
                <span
                  class="block h-full rounded-full bg-[color:var(--app-accent)]"
                  :style="{ width: `${context.percent}%` }"
                ></span>
              </span>
              <span class="composer-footer__label--xs whitespace-nowrap">
                {{ context.label }}
              </span>
            </div>
          </div>

          <div class="flex shrink-0 items-center justify-end gap-1.5">
            <button
              type="button"
              class="inline-flex h-9 min-w-9 items-center justify-center rounded-lg px-3 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-45"
              :class="
                primaryAction === 'stop'
                  ? 'border border-red-200 bg-white text-red-700 hover:bg-red-50'
                  : 'bg-[color:var(--app-accent)] text-white hover:brightness-95'
              "
              :disabled="primaryDisabled"
              :aria-label="primaryLabel"
              :title="primaryTitle"
              data-codex-primary-submit
              @click="submitPrimary"
            >
              <i :class="primaryIcon" class="text-xs"></i>
              <span class="sr-only">{{ primaryLabel }}</span>
            </button>
          </div>
        </div>

      </div>
    </div>
  </section>
</template>
