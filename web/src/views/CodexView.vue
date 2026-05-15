<script setup lang="ts">
import { proxyRefs } from 'vue'
import { RouterLink } from 'vue-router'

import CodexChatPane from '../codex/components/CodexChatPane.vue'
import CodexSidebar from '../codex/components/CodexSidebar.vue'
import { useCodexWorkspace } from '../codex/composables/useCodexWorkspace'
import type { JsonRecord } from '../codex/types'

const codex = proxyRefs(useCodexWorkspace())

function submitUserInputResponse(requestId: string, response: JsonRecord) {
  void codex.submitUserInputResponse(requestId, response)
}

function showCodexError(message: string) {
  codex.errorMessage = message
  codex.successMessage = ''
}
</script>

<template>
  <div class="grid h-screen grid-cols-[minmax(18rem,22rem)_minmax(0,1fr)] overflow-hidden bg-[color:var(--app-bg)] max-lg:grid-cols-1">
    <CodexSidebar
      v-model:project-path="codex.projectPathDraft"
      class="max-lg:hidden"
      :workspace="codex.workspace"
      :active-thread-id="codex.activeThreadId"
      :opening-thread-id="codex.openingThreadId"
      :archiving-thread-id="codex.archivingThreadId"
      :forking-thread-id="codex.forkingThreadId"
      :busy="codex.isCommandBusy || codex.isBooting"
      @select-thread="codex.selectThread"
      @start-thread="codex.startThread"
      @archive-thread="codex.archiveThread"
      @fork-thread="codex.forkThread"
      @copy-error="showCodexError"
      @refresh="codex.refreshWorkspace"
    />

    <main class="flex min-h-0 flex-col overflow-hidden">
      <div class="flex items-center justify-between gap-3 border-b border-[color:var(--app-border)] bg-[rgba(255,253,247,0.94)] px-4 py-2">
        <div class="flex min-w-0 items-center gap-2">
          <span
            class="h-2.5 w-2.5 shrink-0 rounded-full"
            :class="codex.status === 'open' ? 'bg-emerald-500' : codex.status === 'connecting' ? 'bg-amber-500' : 'bg-red-500'"
          ></span>
          <span class="truncate text-sm font-semibold text-[color:var(--app-text)]">
            Codex {{ codex.status }}
          </span>
        </div>
        <nav class="flex shrink-0 items-center gap-2">
          <RouterLink
            to="/chat"
            class="inline-flex h-8 items-center gap-2 rounded-lg border border-[color:var(--app-border)] bg-white px-3 text-sm font-semibold text-[color:var(--app-text)] transition hover:border-[color:var(--app-accent)]"
          >
            <i class="pi pi-comments text-xs"></i>
            <span>Chat</span>
          </RouterLink>
          <RouterLink
            to="/settings"
            class="inline-flex h-8 items-center gap-2 rounded-lg border border-[color:var(--app-border)] bg-white px-3 text-sm font-semibold text-[color:var(--app-text)] transition hover:border-[color:var(--app-accent)]"
          >
            <i class="pi pi-cog text-xs"></i>
            <span>Settings</span>
          </RouterLink>
        </nav>
      </div>

      <div class="grid min-h-0 flex-1 grid-cols-1 overflow-hidden">
        <CodexSidebar
          v-model:project-path="codex.projectPathDraft"
          class="lg:hidden"
          :workspace="codex.workspace"
          :active-thread-id="codex.activeThreadId"
          :opening-thread-id="codex.openingThreadId"
          :archiving-thread-id="codex.archivingThreadId"
          :forking-thread-id="codex.forkingThreadId"
          :busy="codex.isCommandBusy || codex.isBooting"
          @select-thread="codex.selectThread"
          @start-thread="codex.startThread"
          @archive-thread="codex.archiveThread"
          @fork-thread="codex.forkThread"
          @copy-error="showCodexError"
          @refresh="codex.refreshWorkspace"
        />

        <CodexChatPane
          :active-thread-id="codex.activeThreadId"
          :active-thread-state="codex.activeThreadState"
          :active-user-input-request="codex.activeUserInputRequest"
          :active-status="codex.activeStatus"
          :active-mode="codex.activeMode"
          :queued-followups="codex.queuedFollowups"
          :socket-status="codex.status"
          :error-message="codex.errorMessage"
          :success-message="codex.successMessage"
          :is-command-busy="codex.isCommandBusy"
          :is-renaming="codex.isRenaming"
          :is-archiving="codex.isArchiving"
          :is-active-turn-in-progress="codex.isActiveTurnInProgress"
          @rename-thread="codex.renameThread"
          @archive-thread="codex.archiveThread()"
          @compact-thread="codex.compactThread"
          @interrupt-turn="codex.interruptTurn"
          @set-mode="codex.setMode"
          @refresh="codex.refreshWorkspace"
          @submit-user-input-response="submitUserInputResponse"
          @send-prompt="codex.sendPrompt"
          @steer-prompt="codex.steerPrompt"
          @enqueue-followup="codex.enqueueFollowup"
          @remove-followup="codex.removeFollowup"
        />
      </div>
    </main>
  </div>
</template>
