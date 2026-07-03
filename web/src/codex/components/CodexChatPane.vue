<script setup lang="ts">
import { computed, ref } from 'vue'

import CodexComposer from './CodexComposer.vue'
import CodexConversation from './CodexConversation.vue'
import CodexRequestPanel from './CodexRequestPanel.vue'
import CodexThreadToolbar from './CodexThreadToolbar.vue'
import type {
  CodexConversationState,
  CodexPendingRequest,
  CodexPromptSubmission,
  CodexQueuedFollowup,
  CodexSocketStatus,
  CodexThreadGoalStatus,
  CodexWorkMode,
  CodexWorkspaceResponse,
  JsonRecord,
} from '../types'

const composerText = ref('')

const props = defineProps<{
  activeThreadId: string
  activeThreadState: CodexConversationState | null
  activeUserInputRequest: CodexPendingRequest | null
  activeStatus: string
  activeMode: CodexWorkMode
  queuedFollowups: CodexQueuedFollowup[]
  workspace?: CodexWorkspaceResponse | null
  socketStatus: CodexSocketStatus
  errorMessage?: string
  successMessage?: string
  isCommandBusy?: boolean
  isRenaming?: boolean
  isArchiving?: boolean
  isActiveTurnInProgress?: boolean
  emptyEyebrow?: string
  emptyTitle?: string
  showEmptyHeader?: boolean
}>()

const emit = defineEmits<{
  renameThread: [name: string]
  archiveThread: []
  compactThread: []
  interruptTurn: []
  setMode: [mode: CodexWorkMode]
  setThreadGoal: [objective: string, tokenBudget?: number | null]
  updateThreadGoalStatus: [status: CodexThreadGoalStatus]
  clearThreadGoal: []
  refresh: []
  submitUserInputResponse: [requestId: string, response: JsonRecord]
  sendPrompt: [submission: CodexPromptSubmission]
  steerPrompt: [prompt: string]
  enqueueFollowup: [prompt: string]
  removeFollowup: [messageId: string]
  forkThread: [threadId: string]
  copyError: [message: string]
  remoteConnectionChanged: []
}>()

function submitUserInputResponse(requestId: string, response: JsonRecord) {
  emit('submitUserInputResponse', requestId, response)
}

const gitInfo = computed(() => {
  const value = props.activeThreadState?.gitInfo
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as JsonRecord)
    : null
})
const gitBranch = computed(() => stringValue(gitInfo.value?.branch))
const gitSha = computed(() => stringValue(gitInfo.value?.sha))
const gitOriginUrl = computed(() => stringValue(gitInfo.value?.originUrl ?? gitInfo.value?.origin_url))
const gitShortSha = computed(() => (gitSha.value ? gitSha.value.slice(0, 7) : ''))

function stringValue(value: unknown) {
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}
</script>

<template>
  <section class="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
    <CodexThreadToolbar
      v-if="false && activeThreadId"
      :thread-id="activeThreadId"
      :state="activeThreadState"
      :status="activeStatus"
      :busy="isCommandBusy"
      :renaming="isRenaming"
      @rename-thread="emit('renameThread', $event)"
    />
    <header
      v-else-if="showEmptyHeader !== false"
      class="grid gap-1 border-b border-[color:var(--app-border)] bg-[rgba(255,253,247,0.88)] px-4 py-4 max-sm:px-3"
    >
      <p class="m-0 text-xs font-bold uppercase tracking-[0.14em] text-[color:var(--app-text-soft)]">
        {{ emptyEyebrow || 'Codex workspace' }}
      </p>
      <h2 class="m-0 text-xl font-semibold text-[color:var(--app-text)]">
        {{ emptyTitle || 'Select or start a thread' }}
      </h2>
    </header>

    <div
      v-if="errorMessage || successMessage"
      class="grid gap-2 px-4 pt-3 max-sm:px-3"
    >
      <p
        v-if="errorMessage"
        class="m-0 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm font-semibold text-red-700"
      >
        {{ errorMessage }}
      </p>
      <p
        v-else-if="successMessage"
        class="m-0 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700"
      >
        {{ successMessage }}
      </p>
    </div>

    <div
      v-if="gitInfo"
      class="flex min-w-0 items-center gap-2 border-b border-[rgba(34,66,72,0.08)] bg-[rgba(255,253,247,0.72)] px-4 py-1.5 text-xs text-[color:var(--app-text-soft)] max-sm:px-3"
      data-codex-git-info
    >
      <i class="pi pi-code-branch shrink-0 text-[0.68rem]"></i>
      <span v-if="gitBranch" class="min-w-0 truncate font-semibold text-[color:var(--app-text)]">
        {{ gitBranch }}
      </span>
      <code v-if="gitShortSha" class="shrink-0 rounded bg-white/70 px-1.5 py-0.5 text-[0.68rem]">
        {{ gitShortSha }}
      </code>
      <span v-if="gitOriginUrl" class="min-w-0 truncate" :title="gitOriginUrl">
        {{ gitOriginUrl }}
      </span>
    </div>

    <CodexConversation
      :state="activeThreadState"
      @fork-thread="emit('forkThread', $event)"
      @copy-error="emit('copyError', $event)"
    />

    <CodexRequestPanel
      :request="activeUserInputRequest"
      :disabled="isCommandBusy"
      @submit-response="submitUserInputResponse"
    />

    <CodexComposer
      v-if="!activeUserInputRequest"
      v-model="composerText"
      :disabled="!activeThreadId || socketStatus !== 'open'"
      :busy="isCommandBusy"
      :is-working="isActiveTurnInProgress"
      :mode="activeMode"
      :queued-followups="queuedFollowups"
      :state="activeThreadState"
      :workspace="workspace"
      @send-prompt="emit('sendPrompt', $event)"
      @steer-prompt="emit('steerPrompt', $event)"
      @enqueue-followup="emit('enqueueFollowup', $event)"
      @remove-followup="emit('removeFollowup', $event)"
      @interrupt-turn="emit('interruptTurn')"
      @set-mode="emit('setMode', $event)"
      @set-thread-goal="(objective, tokenBudget) => emit('setThreadGoal', objective, tokenBudget)"
      @update-thread-goal-status="emit('updateThreadGoalStatus', $event)"
      @clear-thread-goal="emit('clearThreadGoal')"
      @remote-connection-changed="emit('remoteConnectionChanged')"
    />
  </section>
</template>
