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
  <section class="settings-page">
    <ScrollPanel class="settings-scrollpanel">
      <div class="settings-scrollpanel-content">
        <div class="settings-page-header">
          <div>
            <p class="eyebrow">Channel Workspace</p>
            <h3>Multi-platform runtime</h3>
            <p class="settings-page-copy">
              Run channel adapters as a shared workspace package, monitor live traffic, and inspect
              channel-backed sessions alongside the main chat history.
            </p>
          </div>
          <Button label="Login Weixin" icon="pi pi-qrcode" @click="emit('loginWeixin')" />
        </div>

        <section class="settings-section">
          <div class="section-header-row">
            <div>
              <p class="eyebrow">Platforms</p>
              <h4>Registered channel platforms</h4>
            </div>
          </div>
          <article
            v-for="platform in workspace?.platforms ?? []"
            :key="platform.name"
            class="runtime-card"
          >
            <p class="eyebrow">{{ platform.label }}</p>
            <h4>{{ platform.name }}</h4>
            <p>{{ platform.account_count }} accounts · {{ platform.running_count }} running</p>
          </article>
          <p v-if="!(workspace?.platforms.length ?? 0)" class="settings-hint">
            No channel platforms registered yet.
          </p>
        </section>

        <section class="settings-section">
          <div class="section-header-row">
            <div>
              <p class="eyebrow">Accounts</p>
              <h4>Weixin accounts</h4>
            </div>
          </div>
          <article
            v-for="account in workspace?.accounts ?? []"
            :key="`${account.platform}:${account.account_id}`"
            class="mcp-card"
          >
            <div class="mcp-card-header">
              <div>
                <strong>{{ account.account_id }}</strong>
                <p>{{ account.platform }}</p>
              </div>
              <Tag :value="account.running ? 'Running' : account.configured ? 'Stopped' : 'Needs login'" :severity="statusSeverity(account)" />
            </div>
            <p v-if="account.last_error" class="settings-hint">{{ account.last_error }}</p>
            <div class="settings-actions">
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
          <p v-if="!(workspace?.accounts.length ?? 0)" class="settings-hint">
            No configured accounts yet. Start with Weixin QR login.
          </p>
        </section>

        <section class="settings-section">
          <div class="section-header-row">
            <div>
              <p class="eyebrow">Login</p>
              <h4>Latest QR login state</h4>
            </div>
          </div>
          <p class="settings-hint">
            Status: {{ loginState.status || 'idle' }}
            <span v-if="loginState.accountId"> · {{ loginState.accountId }}</span>
          </p>
          <a
            v-if="loginState.qrcodeUrl"
            :href="loginState.qrcodeUrl"
            target="_blank"
            rel="noreferrer"
            class="channel-qr-link"
          >
            Open QR code
          </a>
        </section>

        <section class="settings-section">
          <div class="section-header-row">
            <div>
              <p class="eyebrow">Monitor</p>
              <h4>Channel-backed sessions</h4>
            </div>
          </div>
          <div class="session-history-list">
            <button
              v-for="session in monitorSessions"
              :key="session.session_id"
              type="button"
              class="session-history-main"
              @click="emit('openSession', session.session_id)"
            >
              <div class="session-history-copy">
                <p class="session-history-title">{{ session.title }}</p>
                <p class="session-history-preview">{{ session.preview }}</p>
                <p class="session-history-meta">
                  {{ session.channel_meta?.platform ?? 'channel' }}
                  <span v-if="session.channel_meta?.peer_id"> · {{ session.channel_meta.peer_id }}</span>
                </p>
              </div>
            </button>
          </div>
          <p v-if="!monitorSessions.length" class="settings-hint">No channel sessions yet.</p>
        </section>
      </div>
    </ScrollPanel>
  </section>
</template>
