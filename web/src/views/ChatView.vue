<script setup lang="ts">
import Button from 'primevue/button'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'

import ChatComposer from '../components/ChatComposer.vue'
import CodexAdvancedModeDock from '../components/CodexAdvancedModeDock.vue'
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
      class="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-3xl border border-[color:var(--app-border)] bg-[color:var(--app-panel)] shadow-[var(--app-shadow)] backdrop-blur-[14px] max-[1023px]:rounded-[1.35rem]"
    >
      <div
        v-if="workspace.isSwitchingSession"
        class="absolute inset-0 z-10 flex items-center justify-center bg-[rgba(255,252,245,0.74)] backdrop-blur-[2px]"
      >
        <div class="flex items-center gap-3 rounded-2xl border border-[rgba(21,94,99,0.12)] bg-white/86 px-4 py-3 text-sm font-semibold text-[color:var(--app-accent-deep)] shadow-[0_18px_40px_rgba(34,66,72,0.08)]">
          <ProgressSpinner stroke-width="5" style="width: 1.35rem; height: 1.35rem" />
          <span>Switching session…</span>
        </div>
      </div>
      <div class="min-h-0 flex-1">
        <ChatTimeline
          :messages="workspace.chatMessages"
          :activities="workspace.activities"
          :turn-timings="workspace.codexTurnTimings"
          :is-sending="workspace.isSending"
          :is-hydrating-older-activity="workspace.isHydratingOlderActivity"
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
          <CodexAdvancedModeDock v-if="workspace.isCodexWorkspace" />
          <div
            v-if="workspace.isCodexWorkspace && workspace.activeSessionRuntime?.status === 'active'"
            class="rounded-[1.15rem] border border-[rgba(21,94,99,0.12)] bg-[rgba(235,248,246,0.72)] p-3 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.5)]"
          >
            <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
              <input
                v-model="workspace.steerText"
                class="min-w-0 flex-1 rounded-xl border border-[rgba(34,66,72,0.12)] bg-white/75 px-3 py-2 text-sm text-[color:var(--app-text)] outline-none transition focus:border-[rgba(21,94,99,0.36)] focus:shadow-[0_0_0_3px_rgba(21,94,99,0.1)]"
                placeholder="Steer the active Codex turn..."
                :disabled="workspace.isSteering || workspace.isInterrupting"
                @keydown.enter.prevent="workspace.submitCodexSteer"
              />
              <div class="flex shrink-0 gap-2">
                <Button
                  label="Steer"
                  icon="pi pi-directions"
                  size="small"
                  :loading="workspace.isSteering"
                  :disabled="!workspace.steerText.trim() || workspace.isInterrupting"
                  @click="workspace.submitCodexSteer"
                />
                <Button
                  label="Interrupt"
                  icon="pi pi-stop-circle"
                  size="small"
                  severity="secondary"
                  outlined
                  :loading="workspace.isInterrupting"
                  :disabled="workspace.isSteering"
                  @click="workspace.interruptCodexTurn"
                />
              </div>
            </div>
          </div>
          <Message v-if="workspace.activeSession?.source === 'channel'" severity="info" class="m-0">
            This session comes from
            {{ workspace.activeSession.channel_meta?.platform ?? 'channel' }} and is read-only in
            the chat workspace.
          </Message>
          <ChatComposer
            v-model="workspace.composerText"
            :disabled="!workspace.canSendToSession"
            :is-sending="workspace.isSending"
            :attachments="workspace.composerAttachments"
            :attachments-enabled="workspace.isCodexWorkspace && workspace.activeSession?.source !== 'channel'"
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
            @upload-files="workspace.uploadComposerFiles"
            @remove-attachment="workspace.removeComposerAttachment"
            @retry-attachment="workspace.retryComposerAttachment"
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
