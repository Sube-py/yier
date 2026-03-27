<script setup lang="ts">
import ScrollPanel from 'primevue/scrollpanel'
import Button from 'primevue/button'
import Tag from 'primevue/tag'

import type {
  ChannelAccountSummary,
  ChannelConfigResponse,
  ChannelPlatformsResponse,
  ChannelWorkspaceResponse,
  SessionSummary,
} from '../types/api'

defineProps<{
  workspace: ChannelWorkspaceResponse | null
  platforms: ChannelPlatformsResponse | null
  config: ChannelConfigResponse | null
  monitorSessions: SessionSummary[]
  loginState: {
    qrcodeUrl: string
    accountId: string
    status: string
  }
}>()

const emit = defineEmits<{
  loginWeixin: []
  startAccount: [accountId: string]
  stopAccount: [accountId: string]
  openSession: [sessionId: string]
}>()

function statusSeverity(account: ChannelAccountSummary) {
  if (account.last_error) {
    return 'danger'
  }
  if (account.running) {
    return 'success'
  }
  if (account.configured) {
    return 'info'
  }
  return 'warn'
}
</script>

<template>
  <section class="flex min-h-0 flex-1 flex-col overflow-hidden">
    <ScrollPanel class="min-h-0 flex-1">
      <div class="pr-[0.35rem]">
        <div class="flex items-start justify-between gap-4 border-b border-[color:var(--app-border)] pb-4 max-md:flex-col max-md:items-stretch">
          <div>
            <p class="eyebrow">Channel Workspace</p>
            <h3>Multi-platform runtime</h3>
            <p class="mt-[0.65rem] mb-0 max-w-[42rem] leading-[1.6] text-[color:var(--app-text-soft)]">
              Run channel adapters as a shared workspace package, monitor live traffic, and inspect
              channel-backed sessions alongside the main chat history.
            </p>
          </div>
          <Button label="Login Weixin" icon="pi pi-qrcode" @click="emit('loginWeixin')" />
        </div>

        <section class="grid gap-[0.9rem] pt-4">
          <div class="flex items-center justify-between gap-3">
            <div>
              <p class="eyebrow">Platforms</p>
              <h4>Registered channel platforms</h4>
            </div>
          </div>
          <article
            v-for="platform in workspace?.platforms ?? []"
            :key="platform.name"
            class="rounded-[1.2rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
          >
            <p class="eyebrow">{{ platform.label }}</p>
            <h4>{{ platform.name }}</h4>
            <p>{{ platform.account_count }} accounts · {{ platform.running_count }} running</p>
          </article>
          <p v-if="!(workspace?.platforms.length ?? 0)" class="m-0 text-[0.92rem] leading-[1.6] text-[color:var(--app-text-soft)]">
            No channel platforms registered yet.
          </p>
        </section>

        <section class="grid gap-[0.9rem] pt-4">
          <div class="flex items-center justify-between gap-3">
            <div>
              <p class="eyebrow">Accounts</p>
              <h4>Weixin accounts</h4>
            </div>
          </div>
          <article
            v-for="account in workspace?.accounts ?? []"
            :key="`${account.platform}:${account.account_id}`"
            class="grid gap-3 rounded-[1.2rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
          >
            <div class="flex items-center justify-between gap-3 max-md:flex-col max-md:items-stretch">
              <div>
                <strong>{{ account.account_id }}</strong>
                <p>{{ account.platform }}</p>
              </div>
              <Tag :value="account.running ? 'Running' : account.configured ? 'Stopped' : 'Needs login'" :severity="statusSeverity(account)" />
            </div>
            <p v-if="account.last_error" class="m-0 text-[0.92rem] leading-[1.6] text-[color:var(--app-text-soft)]">
              {{ account.last_error }}
            </p>
            <div class="flex justify-end gap-3">
              <Button
                label="Start"
                icon="pi pi-play"
                severity="secondary"
                outlined
                :disabled="!account.configured || account.running"
                @click="emit('startAccount', account.account_id)"
              />
              <Button
                label="Stop"
                icon="pi pi-stop"
                severity="secondary"
                outlined
                :disabled="!account.running"
                @click="emit('stopAccount', account.account_id)"
              />
            </div>
          </article>
          <p v-if="!(workspace?.accounts.length ?? 0)" class="m-0 text-[0.92rem] leading-[1.6] text-[color:var(--app-text-soft)]">
            No configured accounts yet. Start with Weixin QR login.
          </p>
        </section>

        <section class="grid gap-[0.9rem] pt-4">
          <div class="flex items-center justify-between gap-3">
            <div>
              <p class="eyebrow">Login</p>
              <h4>Latest QR login state</h4>
            </div>
          </div>
          <p class="m-0 text-[0.92rem] leading-[1.6] text-[color:var(--app-text-soft)]">
            Status: {{ loginState.status || 'idle' }}
            <span v-if="loginState.accountId"> · {{ loginState.accountId }}</span>
          </p>
          <a
            v-if="loginState.qrcodeUrl"
            :href="loginState.qrcodeUrl"
            target="_blank"
            rel="noreferrer"
            class="inline-flex w-fit items-center rounded-full border border-[color:var(--app-border)] bg-white/80 px-4 py-2 text-sm font-semibold text-[color:var(--app-accent)] no-underline transition hover:bg-white"
          >
            Open QR code
          </a>
        </section>

        <section class="grid gap-[0.9rem] pt-4">
          <div class="flex items-center justify-between gap-3">
            <div>
              <p class="eyebrow">Monitor</p>
              <h4>Channel-backed sessions</h4>
            </div>
          </div>
          <div class="grid gap-[0.65rem]">
            <button
              v-for="session in monitorSessions"
              :key="session.session_id"
              type="button"
              class="rounded-[0.8rem] border border-[rgba(34,66,72,0.08)] bg-[rgba(255,250,242,0.72)] px-[0.65rem] py-[0.55rem] text-left transition duration-150 hover:border-[rgba(21,94,99,0.18)] hover:bg-[rgba(255,250,242,0.92)]"
              @click="emit('openSession', session.session_id)"
            >
              <div class="min-w-0">
                <p class="m-0 font-bold leading-[1.35]">{{ session.title }}</p>
                <p class="mt-[0.18rem] mb-0 break-words text-[0.88rem] leading-[1.45] text-[color:var(--app-text-soft)]">
                  {{ session.preview }}
                </p>
                <p class="mt-[0.28rem] mb-0 text-[0.76rem] text-[color:var(--app-text-soft)]">
                  {{ session.channel_meta?.platform ?? 'channel' }}
                  <span v-if="session.channel_meta?.peer_id"> · {{ session.channel_meta.peer_id }}</span>
                </p>
              </div>
            </button>
          </div>
          <p v-if="!monitorSessions.length" class="m-0 text-[0.92rem] leading-[1.6] text-[color:var(--app-text-soft)]">
            No channel sessions yet.
          </p>
        </section>
      </div>
    </ScrollPanel>
  </section>
</template>
