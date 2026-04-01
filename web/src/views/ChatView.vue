<script setup lang="ts">
import Button from 'primevue/button'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'

import ChatComposer from '../components/ChatComposer.vue'
import ChatTimeline from '../components/ChatTimeline.vue'
import CodexWorkbar from '../components/CodexWorkbar.vue'
import { useWorkspaceAppContext } from '../composables/useWorkspaceApp'

const workspace = useWorkspaceAppContext()
</script>

<template>
  <section
    v-if="workspace.isBooting"
    class="grid min-h-72 place-items-center justify-items-center gap-3 rounded-3xl border border-dashed border-[color:var(--app-border-strong)] bg-[rgba(255,252,245,0.7)] p-8 text-center"
  >
    <ProgressSpinner stroke-width="4" />
    <p>Preparing your local workspace…</p>
  </section>

  <section
    v-else-if="!workspace.activeBackendReady"
    class="grid min-h-72 place-items-center justify-items-center gap-3 rounded-3xl border border-dashed border-[color:var(--app-border-strong)] bg-[rgba(255,252,245,0.7)] p-8 text-center"
  >
    <p class="eyebrow">Setup needed</p>
    <h3 class="m-0 font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-2xl font-semibold">
      {{
        workspace.activeBackendId === 'codex'
          ? 'Configure the Codex backend before sending messages.'
          : 'Configure the LLM connection before sending messages.'
      }}
    </h3>
    <p class="m-0 max-w-3xl text-[color:var(--app-text-soft)]">
      {{
        workspace.activeBackendId === 'codex'
          ? 'Set the Codex launcher command in Settings and make sure the executable is on your PATH.'
          : 'Add a provider or `base_url`, plus `api_key` and `model`, in Settings. Your values stay on this machine in `~/.yier/web/settings.json`.'
      }}
    </p>
    <Button label="Open Settings" icon="pi pi-cog" @click="workspace.openSettings" />
  </section>

  <section
    v-else
    class="flex min-h-0 flex-1 flex-col overflow-hidden"
    :class="{
      'grid grid-cols-[minmax(0,1fr)_20rem] gap-4 items-stretch':
        workspace.isCodexWorkspace && !workspace.isCodexCompactLayout,
      'max-[1023px]:min-h-0 max-[1023px]:overflow-hidden': workspace.isMobileChatPage,
      'max-[1023px]:overflow-visible': !workspace.isMobileChatPage,
    }"
  >
    <div
      class="flex min-h-0 flex-1 flex-col overflow-hidden rounded-3xl border border-[color:var(--app-border)] bg-[color:var(--app-panel)] shadow-[var(--app-shadow)] backdrop-blur-[14px] max-[1023px]:rounded-[1.35rem]"
    >
      <div class="min-h-0 flex-1">
        <ChatTimeline
          :messages="workspace.chatMessages"
          :activities="workspace.activities"
          :turn-timings="workspace.codexTurnTimings"
          :is-sending="workspace.isSending"
          :session-label="workspace.sessionLabel"
          :session-runtime="workspace.activeSessionRuntime"
          :project-path="workspace.activeProjectPath"
          :assistant-label="workspace.assistantLabel"
          :compact-header="workspace.isMobileChatPage"
          :show-reasoning-cards="workspace.appForm.codexShowReasoningCards"
          @approval-action="workspace.submitApprovalDecision"
        />
      </div>
      <div class="shrink-0 px-[1.1rem] pb-4 max-[1023px]:px-4 max-sm:px-3 max-sm:pb-3">
        <div class="flex flex-col gap-3">
          <Message v-if="workspace.activeSession?.source === 'channel'" severity="info" class="m-0">
            This session comes from
            {{ workspace.activeSession.channel_meta?.platform ?? 'channel' }} and is read-only in
            the chat workspace.
          </Message>
          <ChatComposer
            v-model="workspace.composerText"
            :disabled="!workspace.canSendToSession"
            :is-sending="workspace.isSending"
            :model-label="workspace.isCodexWorkspace ? workspace.appForm.codexModel : ''"
            :reasoning-label="
              workspace.isCodexWorkspace ? workspace.appForm.codexReasoningEffort : ''
            "
            :placeholder="workspace.composerPlaceholder"
            :sandbox="workspace.isCodexWorkspace ? workspace.appForm.codexSandbox : null"
            :saving-sandbox="workspace.savingState.codexSandbox"
            :selection-start="workspace.composerSelectionStart"
            :selection-end="workspace.composerSelectionEnd"
            :selection-version="workspace.composerSelectionVersion"
            @update-sandbox="workspace.appForm.codexSandbox = $event"
            @save-sandbox="workspace.saveCodexSandboxMode"
            @selection-change="workspace.handleComposerSelectionChange"
            @submit="workspace.submitMessage"
          />
        </div>
      </div>
    </div>
    <CodexWorkbar
      v-if="workspace.isCodexWorkspace && !workspace.isCodexCompactLayout"
      :runtime="workspace.activeSessionRuntime"
      :project-path="workspace.activeProjectPath"
      :paired-editors="workspace.activeCodexPairedEditors"
    />
  </section>
</template>
