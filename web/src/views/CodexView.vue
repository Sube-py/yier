<script setup lang="ts">
import { proxyRefs, ref } from 'vue'
import { RouterLink } from 'vue-router'

import CodexComposer from '../codex/components/CodexComposer.vue'
import CodexConversation from '../codex/components/CodexConversation.vue'
import CodexRequestPanel from '../codex/components/CodexRequestPanel.vue'
import CodexSidebar from '../codex/components/CodexSidebar.vue'
import CodexThreadToolbar from '../codex/components/CodexThreadToolbar.vue'
import { useCodexWorkspace } from '../codex/composables/useCodexWorkspace'
import type { JsonRecord } from '../codex/types'

const codex = proxyRefs(useCodexWorkspace())
const composerText = ref('')

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

        <section class="flex min-h-0 flex-col overflow-hidden">
          <CodexThreadToolbar
            v-if="codex.activeThreadId"
            :thread-id="codex.activeThreadId"
            :state="codex.activeThreadState"
            :status="codex.activeStatus"
            :mode="codex.activeMode"
            :busy="codex.isCommandBusy"
            :renaming="codex.isRenaming"
            :archiving="codex.isArchiving"
            @rename-thread="codex.renameThread"
            @archive-thread="codex.archiveThread()"
            @compact-thread="codex.compactThread"
            @interrupt-turn="codex.interruptTurn"
            @set-mode="codex.setMode"
            @refresh="codex.refreshWorkspace"
          />
          <header
            v-else
            class="grid gap-1 border-b border-[color:var(--app-border)] bg-[rgba(255,253,247,0.88)] px-4 py-4"
          >
            <p class="m-0 text-xs font-bold uppercase tracking-[0.14em] text-[color:var(--app-text-soft)]">
              Codex workspace
            </p>
            <h2 class="m-0 text-xl font-semibold text-[color:var(--app-text)]">
              Select or start a thread
            </h2>
          </header>

          <div v-if="codex.errorMessage || codex.successMessage" class="grid gap-2 px-4 pt-3">
            <p
              v-if="codex.errorMessage"
              class="m-0 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm font-semibold text-red-700"
            >
              {{ codex.errorMessage }}
            </p>
            <p
              v-else-if="codex.successMessage"
              class="m-0 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700"
            >
              {{ codex.successMessage }}
            </p>
          </div>

          <CodexConversation :state="codex.activeThreadState" />

          <CodexRequestPanel
            :request="codex.activeUserInputRequest"
            :disabled="codex.isCommandBusy"
            @submit-response="submitUserInputResponse"
          />

          <CodexComposer
            v-model="composerText"
            :disabled="!codex.activeThreadId || codex.status !== 'open'"
            :busy="codex.isCommandBusy"
            :is-working="codex.isActiveTurnInProgress"
            :mode="codex.activeMode"
            :queued-followups="codex.queuedFollowups"
            @send-prompt="codex.sendPrompt"
            @steer-prompt="codex.steerPrompt"
            @enqueue-followup="codex.enqueueFollowup"
            @remove-followup="codex.removeFollowup"
            @interrupt-turn="codex.interruptTurn"
            @set-mode="codex.setMode"
          />
        </section>
      </div>
    </main>
  </div>
</template>
