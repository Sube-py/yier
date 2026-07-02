<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Select from 'primevue/select'

import type {
  CodexConversationState,
  CodexPromptSubmission,
  CodexQueuedFollowup,
  CodexRemoteConnection,
  CodexRemoteConnectionsResponse,
  CodexThreadGoal,
  CodexThreadGoalStatus,
  CodexWorkMode,
  CodexWorkspaceResponse,
  JsonRecord,
} from '../types'
import { isRecord } from '../lib/format'
import { apiPost } from '../../lib/api'

const draft = defineModel<string>({ required: true })

const props = defineProps<{
  disabled?: boolean
  busy?: boolean
  isWorking?: boolean
  mode: CodexWorkMode
  queuedFollowups: CodexQueuedFollowup[]
  state: CodexConversationState | null
  workspace?: CodexWorkspaceResponse | null
}>()

const emit = defineEmits<{
  sendPrompt: [submission: CodexPromptSubmission]
  steerPrompt: [prompt: string]
  enqueueFollowup: [prompt: string]
  removeFollowup: [messageId: string]
  interruptTurn: []
  setMode: [mode: CodexWorkMode]
  setThreadGoal: [objective: string, tokenBudget?: number | null]
  updateThreadGoalStatus: [status: CodexThreadGoalStatus]
  clearThreadGoal: []
  remoteConnectionChanged: []
}>()

const baseModelOptions = ['gpt-5.4', 'gpt-5.4-mini', 'gpt-5.3-codex', 'gpt-5.2']
const baseReasoningOptions = ['minimal', 'low', 'medium', 'high', 'xhigh']
const selectedModel = ref('')
const selectedReasoningEffort = ref('')
const goalTokenBudgetDraft = ref('')
const isGoalComposeMode = ref(false)
const remoteMenuOpen = ref(false)
const remoteSwitchingId = ref<string | null>(null)
const remoteSwitchError = ref('')

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
  if (isGoalComposeMode.value && !props.isWorking) {
    return 'Start goal'
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
  if (isGoalComposeMode.value) {
    return 'Start goal'
  }
  return 'Send'
})
const primaryDisabled = computed(() => {
  if (primaryAction.value === 'stop') {
    return props.disabled || props.busy
  }
  if (isGoalComposeMode.value && !props.isWorking) {
    return !canSubmitGoal.value
  }
  return !canSubmitText.value
})
const context = computed(() => contextWindowState(props.state))
const threadGoal = computed(() => props.state?.threadGoal ?? null)
const hasThreadGoal = computed(() => Boolean(threadGoal.value))
const goalObjective = computed(() => threadGoal.value?.objective?.trim() ?? '')
const canSubmitGoal = computed(() => hasDraft.value && !props.disabled && !props.busy)
const goalStatus = computed(() => String(threadGoal.value?.status ?? ''))
const goalStatusLabel = computed(() => goalStatusText(threadGoal.value))
const goalDetail = computed(() => goalProgressText(threadGoal.value))
const canResumeGoal = computed(() =>
  ['paused', 'blocked', 'usageLimited'].includes(goalStatus.value),
)
const remoteConnections = computed(() => props.workspace?.remote_connections ?? [])
const activeRemoteConnectionId = computed(() => props.workspace?.active_remote_connection_id ?? '')
const activeRemoteConnection = computed(() =>
  remoteConnections.value.find((connection) => connection.id === activeRemoteConnectionId.value),
)
const activeRunLocationLabel = computed(() =>
  activeRemoteConnection.value ? remoteTitle(activeRemoteConnection.value) : 'Local',
)
const showRunLocationPicker = computed(() => remoteConnections.value.length > 0)
const composerPlaceholder = computed(() => {
  if (isGoalComposeMode.value) {
    return 'Describe a goal for this thread...'
  }
  return props.isWorking ? 'Add a follow-up for the queue...' : 'Ask Codex to work in this thread...'
})

watch(
  () => props.state?.id,
  () => {
    selectedModel.value = ''
    selectedReasoningEffort.value = ''
  },
)

watch(
  () => props.state?.id,
  () => {
    goalTokenBudgetDraft.value = ''
    isGoalComposeMode.value = false
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

function submitGoal() {
  if (!canSubmitGoal.value) {
    return
  }
  emit('setThreadGoal', draft.value, parsedGoalTokenBudget())
  draft.value = ''
  goalTokenBudgetDraft.value = ''
  isGoalComposeMode.value = false
}

function parsedGoalTokenBudget() {
  const value = Number(goalTokenBudgetDraft.value)
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : null
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
  if (isGoalComposeMode.value && !props.isWorking) {
    submitGoal()
    return
  }
  if (primaryAction.value === 'queue') {
    submitQueue()
    return
  }
  sendSubmission()
}

function toggleGoalComposeMode() {
  if (props.busy || props.disabled || props.isWorking || hasThreadGoal.value) {
    return
  }
  isGoalComposeMode.value = !isGoalComposeMode.value
}

async function activateRunLocation(connectionId: string) {
  if (props.busy || props.disabled || remoteSwitchingId.value !== null) {
    return
  }
  remoteSwitchError.value = ''
  remoteSwitchingId.value = connectionId || 'local'
  try {
    const path = connectionId
      ? `/api/codex/remote-connections/${encodeURIComponent(connectionId)}/activate`
      : '/api/codex/remote-connections/activate-local'
    await apiPost<CodexRemoteConnectionsResponse>(path, {})
    remoteMenuOpen.value = false
    emit('remoteConnectionChanged')
  } catch (error) {
    remoteSwitchError.value = error instanceof Error ? error.message : String(error)
  } finally {
    remoteSwitchingId.value = null
  }
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

function remoteTitle(connection: CodexRemoteConnection) {
  return connection.display_name || connection.ssh_alias || connection.ssh_host
}

function remoteSubtitle(connection: CodexRemoteConnection) {
  const target = connection.ssh_alias || connection.ssh_host
  const port = connection.ssh_port ? `:${connection.ssh_port}` : ''
  return `${target}${port}`
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
    label: `${percent}%`,
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
        label: `${computedPercent}%`,
        detail: `${formatTokenCount(used)} / ${formatTokenCount(total)} tokens`,
        percent: computedPercent,
        estimated: false,
      }
    }
    if (percent != null) {
      const normalized = percent <= 1 ? percent * 100 : percent
      return {
        label: `${Math.round(normalized)}%`,
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

function goalStatusText(goal: CodexThreadGoal | null) {
  if (!goal) {
    return 'Goal'
  }
  const status = String(goal.status)
  if (status === 'active') {
    return 'Pursuing goal'
  }
  if (status === 'paused') {
    return 'Paused goal'
  }
  if (status === 'blocked') {
    return 'Goal blocked'
  }
  if (status === 'usageLimited') {
    return 'Goal usage limited'
  }
  if (status === 'budgetLimited') {
    return 'Goal limited'
  }
  if (status === 'complete') {
    return 'Goal achieved'
  }
  return 'Goal'
}

function goalProgressText(goal: CodexThreadGoal | null) {
  if (!goal) {
    return ''
  }
  const seconds = typeof goal.timeUsedSeconds === 'number' ? goal.timeUsedSeconds : 0
  const minutes = Math.floor(seconds / 60)
  const time = minutes > 0 ? `${minutes}m` : `${seconds}s`
  if (typeof goal.tokenBudget === 'number' && goal.tokenBudget > 0) {
    const used = typeof goal.tokensUsed === 'number' ? goal.tokensUsed : 0
    return `${formatTokenCount(used)} / ${formatTokenCount(goal.tokenBudget)} tokens · ${time}`
  }
  return time
}
</script>

<template>
  <section
    class="sticky bottom-0 z-10 mt-auto w-full border-t border-[color:var(--app-border)] bg-[rgba(255,253,247,0.94)] px-4 pb-[calc(1rem+env(safe-area-inset-bottom))] pt-3 max-sm:px-2.5"
    data-codex-composer-shell
  >
    <div class="mx-auto grid max-w-5xl gap-2">
      <div
        class="grid min-w-0 gap-2 rounded-2xl border border-[color:var(--app-border)] bg-white/95 p-2 shadow-[0_14px_34px_rgba(24,44,48,0.08)] transition max-sm:rounded-xl"
        data-codex-composer
      >
        <div
          v-if="hasThreadGoal || isGoalComposeMode"
          class="-mb-1 flex min-w-0 flex-wrap items-center gap-1.5 px-1 text-xs"
          data-codex-goal-panel
        >
          <template v-if="hasThreadGoal">
            <span
              class="inline-flex h-7 min-w-0 max-w-full items-center gap-1.5 rounded-lg bg-[rgba(21,94,99,0.08)] px-2 font-bold text-[color:var(--app-accent)]"
              data-codex-goal-status
            >
              <i class="pi pi-flag text-[0.62rem]"></i>
              <span class="truncate">{{ goalStatusLabel }}</span>
            </span>
            <span class="min-w-0 flex-1 truncate font-semibold text-[color:var(--app-text)]">
              {{ goalObjective }}
            </span>
            <span class="shrink-0 text-[color:var(--app-text-soft)]">
              {{ goalDetail }}
            </span>
            <button
              v-if="canResumeGoal"
              type="button"
              class="inline-flex h-7 w-7 items-center justify-center rounded-md text-[color:var(--app-text-soft)] transition hover:bg-[rgba(21,94,99,0.07)] hover:text-[color:var(--app-text)] disabled:cursor-not-allowed disabled:opacity-45"
              aria-label="Resume goal"
              :disabled="busy || disabled"
              data-codex-goal-resume
              @click="emit('updateThreadGoalStatus', 'active')"
            >
              <i class="pi pi-play text-[0.62rem]"></i>
            </button>
            <button
              v-else-if="goalStatus === 'active'"
              type="button"
              class="inline-flex h-7 w-7 items-center justify-center rounded-md text-[color:var(--app-text-soft)] transition hover:bg-[rgba(21,94,99,0.07)] hover:text-[color:var(--app-text)] disabled:cursor-not-allowed disabled:opacity-45"
              aria-label="Pause goal"
              :disabled="busy || disabled"
              data-codex-goal-pause
              @click="emit('updateThreadGoalStatus', 'paused')"
            >
              <i class="pi pi-pause text-[0.62rem]"></i>
            </button>
            <button
              type="button"
              class="inline-flex h-7 w-7 items-center justify-center rounded-md text-[color:var(--app-text-soft)] transition hover:bg-[rgba(21,94,99,0.07)] hover:text-[color:var(--app-text)] disabled:cursor-not-allowed disabled:opacity-45"
              aria-label="Complete goal"
              :disabled="busy || disabled"
              data-codex-goal-complete
              @click="emit('updateThreadGoalStatus', 'complete')"
            >
              <i class="pi pi-check text-[0.62rem]"></i>
            </button>
            <button
              type="button"
              class="inline-flex h-7 w-7 items-center justify-center rounded-md text-[color:var(--app-text-soft)] transition hover:bg-[rgba(21,94,99,0.07)] hover:text-[color:var(--app-text)] disabled:cursor-not-allowed disabled:opacity-45"
              aria-label="Mark goal blocked"
              :disabled="busy || disabled"
              data-codex-goal-blocked
              @click="emit('updateThreadGoalStatus', 'blocked')"
            >
              <i class="pi pi-ban text-[0.62rem]"></i>
            </button>
            <button
              type="button"
              class="inline-flex h-7 w-7 items-center justify-center rounded-md text-[color:var(--app-text-soft)] transition hover:bg-red-50 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-45"
              aria-label="Clear goal"
              :disabled="busy || disabled"
              data-codex-goal-clear
              @click="emit('clearThreadGoal')"
            >
              <i class="pi pi-times text-[0.62rem]"></i>
            </button>
          </template>
          <template v-else>
            <span
              class="inline-flex h-7 items-center gap-1.5 rounded-lg bg-[rgba(21,94,99,0.08)] px-2 font-bold text-[color:var(--app-accent)]"
              data-codex-goal-status
            >
              <i class="pi pi-flag text-[0.62rem]"></i>
              New goal
            </span>
          </template>
        </div>

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
                <i
                  class="pi pi-bars pointer-events-none absolute left-0 top-1/2 -translate-y-1/2 text-[0.56rem] opacity-0 transition-opacity group-hover:opacity-100"></i>
                <i class="pi pi-clock text-[0.62rem]"></i>
              </span>
              <span class="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap leading-4 text-[color:var(--app-text-soft)]">
                {{ followupText(followup) }}
              </span>
            </div>
            <div
              class="flex shrink-0 items-center gap-1 opacity-80 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 sm:opacity-0"
            >
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
          :placeholder="composerPlaceholder"
          @keydown="onKeydown"
        ></textarea>

        <div
          class="composer-footer grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-end gap-2 max-sm:items-start"
          data-codex-composer-footer
        >
          <div
            class="hide-scrollbar flex min-w-0 flex-wrap items-center gap-1.5 max-sm:flex-nowrap max-sm:overflow-x-auto max-sm:overscroll-x-contain max-sm:pb-0.5"
            data-codex-composer-controls
          >
            <div
              v-if="showRunLocationPicker"
              class="relative shrink-0"
              data-codex-run-location-picker
            >
              <button
                type="button"
                class="inline-flex h-8 max-w-48 items-center gap-1.5 rounded-lg border border-[color:var(--app-border)] bg-white px-2 text-sm font-semibold text-[color:var(--app-text)] transition hover:bg-[rgba(21,94,99,0.05)] disabled:cursor-not-allowed disabled:opacity-45 max-sm:max-w-36"
                :disabled="busy || disabled || remoteSwitchingId !== null"
                aria-label="Select where to run the task"
                :aria-expanded="remoteMenuOpen"
                data-codex-run-location-trigger
                @click="remoteMenuOpen = !remoteMenuOpen"
              >
                <i :class="activeRemoteConnection ? 'pi pi-server' : 'pi pi-desktop'" class="text-[0.7rem]"></i>
                <span class="min-w-0 truncate">{{ activeRunLocationLabel }}</span>
                <i class="pi pi-chevron-down text-[0.55rem] text-[color:var(--app-text-soft)]"></i>
              </button>
              <div
                v-if="remoteMenuOpen"
                class="absolute bottom-full left-0 z-30 mb-1 grid w-72 max-w-[calc(100vw-2rem)] gap-1 rounded-lg border border-[color:var(--app-border)] bg-white p-1.5 shadow-xl"
                data-codex-run-location-menu
              >
                <button
                  type="button"
                  class="grid grid-cols-[1rem_minmax(0,1fr)_1rem] items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition hover:bg-[rgba(21,94,99,0.06)] disabled:cursor-wait disabled:opacity-60"
                  :class="!activeRemoteConnectionId ? 'font-semibold text-[color:var(--app-text)]' : 'text-[color:var(--app-text-soft)]'"
                  :disabled="remoteSwitchingId !== null"
                  data-codex-run-location-local
                  @click="activateRunLocation('')"
                >
                  <i class="pi pi-desktop text-[0.68rem]"></i>
                  <span class="truncate">Work locally</span>
                  <i v-if="!activeRemoteConnectionId" class="pi pi-check text-[0.68rem] text-[color:var(--app-accent)]"></i>
                </button>
                <button
                  v-for="connection in remoteConnections"
                  :key="connection.id"
                  type="button"
                  class="grid grid-cols-[1rem_minmax(0,1fr)_1rem] items-center gap-2 rounded-md px-2 py-1.5 text-left transition hover:bg-[rgba(21,94,99,0.06)] disabled:cursor-wait disabled:opacity-60"
                  :class="connection.id === activeRemoteConnectionId ? 'font-semibold text-[color:var(--app-text)]' : 'text-[color:var(--app-text-soft)]'"
                  :disabled="remoteSwitchingId !== null"
                  data-codex-run-location-remote
                  @click="activateRunLocation(connection.id)"
                >
                  <i class="pi pi-server text-[0.68rem]"></i>
                  <span class="min-w-0">
                    <span class="block truncate text-sm">{{ remoteTitle(connection) }}</span>
                    <span class="block truncate text-[0.68rem] font-normal text-[color:var(--app-text-soft)]">
                      {{ remoteSubtitle(connection) }}
                    </span>
                  </span>
                  <i v-if="connection.id === activeRemoteConnectionId" class="pi pi-check text-[0.68rem] text-[color:var(--app-accent)]"></i>
                </button>
                <p
                  v-if="remoteSwitchError"
                  class="m-0 line-clamp-2 px-2 py-1 text-[0.68rem] text-red-700"
                  data-codex-run-location-error
                >
                  {{ remoteSwitchError }}
                </p>
              </div>
            </div>

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

            <button
              type="button"
              class="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-[color:var(--app-border)] bg-white px-2 text-sm font-semibold transition hover:bg-[rgba(21,94,99,0.05)] disabled:cursor-not-allowed disabled:opacity-45"
              :class="isGoalComposeMode || hasThreadGoal ? 'text-[color:var(--app-accent)]' : 'text-[color:var(--app-text-soft)]'"
              :disabled="busy || disabled || isWorking || hasThreadGoal"
              :aria-pressed="isGoalComposeMode"
              aria-label="Use prompt as thread goal"
              data-codex-goal-toggle
              @click="toggleGoalComposeMode"
            >
              <i class="pi pi-flag text-[0.68rem]"></i>
              <span>Goal</span>
            </button>

            <input
              v-if="isGoalComposeMode && !hasThreadGoal"
              v-model="goalTokenBudgetDraft"
              class="h-8 w-24 shrink-0 rounded-lg border border-[color:var(--app-border)] bg-white px-2 text-sm text-[color:var(--app-text)] outline-none placeholder:text-[color:var(--app-text-soft)]"
              :disabled="busy || disabled"
              inputmode="numeric"
              placeholder="Tokens"
              aria-label="Goal token budget"
              data-codex-goal-token-budget
              @keydown.enter.prevent="submitGoal"
            />

            <label
              class="inline-flex h-8 min-w-0 max-w-full items-center gap-1.5 rounded-lg border border-[color:var(--app-border)] bg-white px-2 text-sm text-[color:var(--app-text-soft)] max-sm:w-max max-sm:max-w-none max-sm:shrink-0"
            >
              <i class="pi pi-microchip-ai text-[0.72rem]"></i>
              <!-- <span class="composer-footer__label--sm">Model</span> -->
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

            <label
              class="inline-flex h-8 min-w-0 max-w-full items-center gap-1.5 rounded-lg border border-[color:var(--app-border)] bg-white px-2 text-sm text-[color:var(--app-text-soft)] max-sm:w-max max-sm:max-w-none max-sm:shrink-0"
            >
              <i class="pi pi-sparkles text-[0.72rem]"></i>
              <!-- <span class="composer-footer__label--sm">Reasoning</span> -->
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
              class="inline-flex h-8 w-fit max-w-full items-center gap-1.5 rounded-lg border border-[color:var(--app-border)] bg-white px-2 text-xs text-[color:var(--app-text-soft)] max-sm:shrink-0"
              :title="`${context.detail}${context.estimated ? ' estimated' : ''}`"
              data-codex-context-window
            >
              <span class="h-1.5 w-10 overflow-hidden rounded-full bg-[rgba(34,66,72,0.12)]">
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
              :class="primaryAction === 'stop'
                ? 'border border-red-200 bg-white text-red-700 hover:bg-red-50'
                : 'bg-[color:var(--app-accent)] text-white hover:brightness-95'
                "
              :disabled="primaryDisabled"
              :aria-label="primaryLabel"
              :title="primaryTitle"
              data-codex-primary-submit
              @click="submitPrimary"
            >
              <i
                :class="primaryIcon"
                class="text-xs"
              ></i>
              <span class="sr-only">{{ primaryLabel }}</span>
            </button>
          </div>
        </div>

      </div>
    </div>
  </section>
</template>
