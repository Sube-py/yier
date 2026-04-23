<script setup lang="ts">
import Button from 'primevue/button'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'

import ChatComposer from '../components/ChatComposer.vue'
import ComposerUserInputPanel from '../components/ComposerUserInputPanel.vue'
import ChatTimeline from '../components/ChatTimeline.vue'
import CodexWorkbar from '../components/CodexWorkbar.vue'
import PlanModeSuggestion from '../components/PlanModeSuggestion.vue'
import { toggleWorkMode } from '../composables/usePlanModeKeyboard'
import { useWorkspaceAppContext } from '../composables/useWorkspaceApp'

const workspace = useWorkspaceAppContext()

function handleTogglePlanMode() {
  const next = toggleWorkMode(workspace.activeCodexWorkMode)
  workspace.updateCodexWorkMode(next)
}
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
          @approval-action="workspace.submitPendingRequestDecision"
        />
      </div>
      <div class="shrink-0 px-[1.1rem] pt-2 pb-4 max-[1023px]:px-4 max-sm:px-3 max-sm:pb-3">
        <div class="flex flex-col gap-3">
          <div
            v-if="workspace.showQueuedComposerFollowupsPanel"
            class="grid gap-2"
          >
            <div
              v-for="followup in workspace.queuedComposerFollowups"
              :key="followup.id"
              class="queued-followup-item rounded-[1rem] border border-[rgba(34,66,72,0.1)] bg-[rgba(235,248,246,0.72)] px-3 py-2.5 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.45)]"
            >
              <div class="flex items-center justify-between gap-3 max-sm:items-stretch max-sm:flex-col">
                <p class="m-0 min-w-0 flex-1 whitespace-pre-wrap break-words pt-[0.08rem] text-sm leading-[1.6] text-[color:var(--app-text)]">
                  {{ followup.message }}
                </p>
                <div class="flex shrink-0 items-center gap-1.5 self-center max-sm:w-full max-sm:flex-wrap max-sm:self-stretch">
                  <Button
                    label="Steer"
                    icon="pi pi-directions"
                    size="small"
                    class="queued-followup-steer max-sm:flex-1"
                    :pt="{
                      root: {
                        class:
                          '!h-8 !px-3 text-[0.76rem] font-semibold',
                      },
                      label: {
                        class: 'text-[0.76rem]',
                      },
                      icon: {
                        class: 'text-[0.72rem]',
                      },
                    }"
                    :loading="workspace.steeringQueuedComposerFollowupId === followup.id"
                    :disabled="workspace.isInterrupting || workspace.isSwitchingSession"
                    @click="workspace.submitQueuedComposerFollowupSteer(followup.id)"
                  />
                  <Button
                    icon="pi pi-times"
                    size="small"
                    severity="secondary"
                    text
                    rounded
                    class="queued-followup-remove !h-8 !w-8 max-sm:self-start"
                    aria-label="Remove queued follow-up"
                    :disabled="workspace.steeringQueuedComposerFollowupId === followup.id"
                    @click="workspace.removeQueuedComposerFollowup(followup.id)"
                  />
                </div>
              </div>
            </div>
          </div>
          <Message v-if="workspace.activeSession?.source === 'channel'" severity="info" class="m-0">
            This session comes from
            {{ workspace.activeSession.channel_meta?.platform ?? 'channel' }} and is read-only in
            the chat workspace.
          </Message>
          <ComposerUserInputPanel
            v-if="workspace.composerUserInputRequest"
            :request="workspace.composerUserInputRequest"
            :pending-request="workspace.composerPendingRequest"
            :disabled="workspace.isSwitchingSession"
            @submit-request="workspace.submitPendingRequestDecision"
          />
          <ComposerUserInputPanel
            v-else-if="workspace.composerImplementPlanRequest"
            :request="workspace.composerImplementPlanRequest"
            :pending-request="workspace.composerPendingRequest"
            :disabled="workspace.isSwitchingSession"
            @submit-request="workspace.submitPendingRequestDecision"
          />
          <template v-else>
            <PlanModeSuggestion
              v-if="workspace.isCodexWorkspace"
              :composer-text="workspace.composerText"
              :is-plan-mode="workspace.activeCodexWorkMode === 'plan'"
              @activate-plan="workspace.updateCodexWorkMode('plan')"
            />
            <ChatComposer
              v-model="workspace.composerText"
              :disabled="!workspace.canComposeToSession"
              :is-sending="workspace.isSending"
              :submit-mode="workspace.isSending ? 'interrupt' : 'send'"
              :interrupting="workspace.isInterrupting"
              :attachments="workspace.composerAttachments"
              :attachments-enabled="workspace.isCodexWorkspace && workspace.activeSession?.source !== 'channel'"
              :attachments-locked="
                workspace.isSending || workspace.isInterrupting || workspace.isSwitchingSession
              "
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
              :plan-mode="workspace.isCodexWorkspace ? workspace.activeCodexWorkMode : undefined"
              :plan-mode-enabled="workspace.isCodexWorkspace"
              @update-sandbox="workspace.appForm.codexSandbox = $event"
              @save-sandbox="workspace.saveCodexSandboxMode"
              @upload-files="workspace.uploadComposerFiles"
              @remove-attachment="workspace.removeComposerAttachment"
              @retry-attachment="workspace.retryComposerAttachment"
              @selection-change="workspace.handleComposerSelectionChange"
              @interrupt="workspace.interruptCodexTurn"
              @submit="workspace.submitMessage"
              @toggle-plan-mode="handleTogglePlanMode"
            />
          </template>
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
