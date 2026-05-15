<script setup lang="ts">
import { ref } from 'vue'

import CodexComposer from './CodexComposer.vue'
import CodexConversation from './CodexConversation.vue'
import CodexRequestPanel from './CodexRequestPanel.vue'
import CodexThreadToolbar from './CodexThreadToolbar.vue'
import type {
  CodexConversationState,
  CodexPendingRequest,
  CodexQueuedFollowup,
  CodexSocketStatus,
  CodexWorkMode,
  JsonRecord,
} from '../types'

const composerText = ref('')

defineProps<{
  activeThreadId: string
  activeThreadState: CodexConversationState | null
  activeUserInputRequest: CodexPendingRequest | null
  activeStatus: string
  activeMode: CodexWorkMode
  queuedFollowups: CodexQueuedFollowup[]
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
  refresh: []
  submitUserInputResponse: [requestId: string, response: JsonRecord]
  sendPrompt: [prompt: string]
  steerPrompt: [prompt: string]
  enqueueFollowup: [prompt: string]
  removeFollowup: [messageId: string]
}>()

function submitUserInputResponse(requestId: string, response: JsonRecord) {
  emit('submitUserInputResponse', requestId, response)
}
</script>

<template>
  <section class="flex min-h-0 flex-1 flex-col overflow-hidden">
    <CodexThreadToolbar
      v-if="activeThreadId"
      :thread-id="activeThreadId"
      :state="activeThreadState"
      :status="activeStatus"
      :mode="activeMode"
      :busy="isCommandBusy"
      :renaming="isRenaming"
      :archiving="isArchiving"
      @rename-thread="emit('renameThread', $event)"
      @archive-thread="emit('archiveThread')"
      @compact-thread="emit('compactThread')"
      @interrupt-turn="emit('interruptTurn')"
      @set-mode="emit('setMode', $event)"
      @refresh="emit('refresh')"
    />
    <header
      v-else-if="showEmptyHeader !== false"
      class="grid gap-1 border-b border-[color:var(--app-border)] bg-[rgba(255,253,247,0.88)] px-4 py-4"
    >
      <p class="m-0 text-xs font-bold uppercase tracking-[0.14em] text-[color:var(--app-text-soft)]">
        {{ emptyEyebrow || 'Codex workspace' }}
      </p>
      <h2 class="m-0 text-xl font-semibold text-[color:var(--app-text)]">
        {{ emptyTitle || 'Select or start a thread' }}
      </h2>
    </header>

    <div v-if="errorMessage || successMessage" class="grid gap-2 px-4 pt-3">
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

    <CodexConversation :state="activeThreadState" />

    <CodexRequestPanel
      :request="activeUserInputRequest"
      :disabled="isCommandBusy"
      @submit-response="submitUserInputResponse"
    />

    <CodexComposer
      v-model="composerText"
      :disabled="!activeThreadId || socketStatus !== 'open'"
      :busy="isCommandBusy"
      :is-working="isActiveTurnInProgress"
      :mode="activeMode"
      :queued-followups="queuedFollowups"
      @send-prompt="emit('sendPrompt', $event)"
      @steer-prompt="emit('steerPrompt', $event)"
      @enqueue-followup="emit('enqueueFollowup', $event)"
      @remove-followup="emit('removeFollowup', $event)"
      @interrupt-turn="emit('interruptTurn')"
      @set-mode="emit('setMode', $event)"
    />
  </section>
</template>
