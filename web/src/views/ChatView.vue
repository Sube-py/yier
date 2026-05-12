<script setup lang="ts">
import Button from 'primevue/button'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'

import ChatComposer from '../components/ChatComposer.vue'
import ComposerUserInputPanel from '../components/ComposerUserInputPanel.vue'
import ChatTimeline from '../components/ChatTimeline.vue'
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
      Configure the LLM connection before sending messages.
    </h3>
    <p class="m-0 max-w-3xl text-[color:var(--app-text-soft)]">
      Add a provider or `base_url`, plus `api_key` and `model`, in Settings. Your values stay on
      this machine in `~/.yier/web/settings.json`.
    </p>
    <Button label="Open Settings" icon="pi pi-cog" @click="workspace.openSettings" />
  </section>

  <section
    v-else
    class="flex min-h-0 flex-1 flex-col overflow-hidden"
    :class="{
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
            <ChatComposer
              v-model="workspace.composerText"
              :disabled="!workspace.canComposeToSession"
              :is-sending="workspace.isSending"
              submit-mode="send"
              :interrupting="false"
              :attachments="[]"
              :attachments-enabled="false"
              :attachments-locked="workspace.isSending || workspace.isSwitchingSession"
              model-label=""
              reasoning-label=""
              :placeholder="workspace.composerPlaceholder"
              :sandbox="null"
              :saving-sandbox="false"
              :selection-start="workspace.composerSelectionStart"
              :selection-end="workspace.composerSelectionEnd"
              :selection-version="workspace.composerSelectionVersion"
              :plan-mode="undefined"
              :plan-mode-enabled="false"
              @selection-change="workspace.handleComposerSelectionChange"
              @submit="workspace.submitMessage"
            />
          </template>
        </div>
      </div>
    </div>
  </section>
</template>
