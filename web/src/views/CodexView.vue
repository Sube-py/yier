<script setup lang="ts">
import { proxyRefs, ref } from 'vue'
import { RouterLink } from 'vue-router'

import CodexChatPane from '../codex/components/CodexChatPane.vue'
import CodexSidebar from '../codex/components/CodexSidebar.vue'
import { useCodexWorkspace } from '../codex/composables/useCodexWorkspace'
import type { JsonRecord } from '../codex/types'

const codex = proxyRefs(useCodexWorkspace())
const isMobileThreadDrawerOpen = ref(false)

function submitUserInputResponse(requestId: string, response: JsonRecord) {
  void codex.submitUserInputResponse(requestId, response)
}

function showCodexError(message: string) {
  codex.errorMessage = message
  codex.successMessage = ''
}

function openMobileThreadDrawer() {
  isMobileThreadDrawerOpen.value = true
}

function closeMobileThreadDrawer() {
  isMobileThreadDrawerOpen.value = false
}

function selectMobileThread(threadId: string) {
  codex.selectThread(threadId)
  closeMobileThreadDrawer()
}

function startMobileThread(projectPath: string) {
  codex.startThread(projectPath)
  closeMobileThreadDrawer()
}
</script>

<template>
  <div class="grid h-dvh grid-cols-[minmax(18rem,22rem)_minmax(0,1fr)] overflow-hidden bg-[color:var(--app-bg)] max-lg:grid-cols-1">
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
      <div class="flex items-center justify-between gap-2 border-b border-[color:var(--app-border)] bg-[rgba(255,253,247,0.94)] px-4 py-2 pt-[calc(0.5rem+env(safe-area-inset-top))] max-sm:px-3">
        <div class="flex min-w-0 items-center gap-2">
          <button
            type="button"
            class="hidden h-8 shrink-0 items-center gap-2 rounded-lg border border-[color:var(--app-border)] bg-white px-2.5 text-sm font-semibold text-[color:var(--app-text)] transition hover:border-[color:var(--app-accent)] max-lg:inline-flex"
            aria-label="Open Codex threads"
            data-codex-mobile-thread-drawer-open
            @click="openMobileThreadDrawer"
          >
            <i class="pi pi-bars text-xs"></i>
            <span class="max-[380px]:sr-only">Threads</span>
          </button>
          <span
            class="h-2.5 w-2.5 shrink-0 rounded-full"
            :class="codex.status === 'open' ? 'bg-emerald-500' : codex.status === 'connecting' ? 'bg-amber-500' : 'bg-red-500'"
          ></span>
          <span class="truncate text-sm font-semibold text-[color:var(--app-text)]">
            Codex {{ codex.status }}
          </span>
        </div>
        <nav class="flex shrink-0 items-center gap-2 max-[380px]:gap-1">
          <RouterLink
            to="/chat"
            class="inline-flex h-8 items-center gap-2 rounded-lg border border-[color:var(--app-border)] bg-white px-3 text-sm font-semibold text-[color:var(--app-text)] transition hover:border-[color:var(--app-accent)] max-sm:px-2.5"
          >
            <i class="pi pi-comments text-xs"></i>
            <span class="max-[380px]:sr-only">Chat</span>
          </RouterLink>
          <RouterLink
            to="/settings"
            class="inline-flex h-8 items-center gap-2 rounded-lg border border-[color:var(--app-border)] bg-white px-3 text-sm font-semibold text-[color:var(--app-text)] transition hover:border-[color:var(--app-accent)] max-sm:px-2.5"
          >
            <i class="pi pi-cog text-xs"></i>
            <span class="max-[380px]:sr-only">Settings</span>
          </RouterLink>
        </nav>
      </div>

      <div class="grid min-h-0 flex-1 grid-cols-1 overflow-hidden">
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

    <Teleport to="body">
      <Transition name="sheet-fade">
        <button
          v-if="isMobileThreadDrawerOpen"
          type="button"
          class="sheet-overlay lg:hidden"
          aria-label="Close Codex threads"
          data-codex-mobile-thread-drawer-overlay
          @click="closeMobileThreadDrawer"
        ></button>
      </Transition>
      <Transition name="drawer-slide">
        <div
          v-if="isMobileThreadDrawerOpen"
          class="drawer-panel lg:hidden"
          role="dialog"
          aria-modal="true"
          aria-label="Codex threads"
          data-codex-mobile-thread-drawer
        >
          <div class="flex items-center justify-between gap-3 border-b border-[color:var(--app-border)] px-4 py-3 pt-[calc(0.75rem+env(safe-area-inset-top))]">
            <div class="min-w-0">
              <p class="m-0 text-xs font-bold uppercase tracking-[0.14em] text-[color:var(--app-text-soft)]">
                Codex
              </p>
              <h2 class="m-0 truncate text-lg font-semibold text-[color:var(--app-text)]">
                Threads
              </h2>
            </div>
            <button
              type="button"
              class="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[color:var(--app-border)] bg-white text-[color:var(--app-text-soft)] transition hover:text-[color:var(--app-text)]"
              aria-label="Close Codex threads"
              data-codex-mobile-thread-drawer-close
              @click="closeMobileThreadDrawer"
            >
              <i class="pi pi-times text-xs"></i>
            </button>
          </div>

          <CodexSidebar
            v-model:project-path="codex.projectPathDraft"
            class="min-h-0 flex-1 border-r-0"
            data-codex-mobile-thread-sidebar
            :workspace="codex.workspace"
            :active-thread-id="codex.activeThreadId"
            :opening-thread-id="codex.openingThreadId"
            :archiving-thread-id="codex.archivingThreadId"
            :forking-thread-id="codex.forkingThreadId"
            :busy="codex.isCommandBusy || codex.isBooting"
            @select-thread="selectMobileThread"
            @start-thread="startMobileThread"
            @archive-thread="codex.archiveThread"
            @fork-thread="codex.forkThread"
            @copy-error="showCodexError"
            @refresh="codex.refreshWorkspace"
          />
        </div>
      </Transition>
    </Teleport>
  </div>
</template>
