<script setup lang="ts">
import { computed, ref } from 'vue'

import Button from 'primevue/button'
import InputText from 'primevue/inputtext'

import { apiPost, apiPut } from '../../lib/api'
import type {
  CodexRemoteConnection,
  CodexRemoteConnectionChatGptLoginResponse,
  CodexRemoteConnectionPayload,
  CodexRemoteConnectionResponse,
  CodexRemoteConnectionTestResponse,
  CodexWorkspaceResponse,
} from '../types'

const props = defineProps<{
  workspace: CodexWorkspaceResponse
  busy?: boolean
}>()

const emit = defineEmits<{
  remoteConnectionChanged: []
}>()

const remoteDialogVisible = ref(false)
const remoteError = ref('')
const remoteTestingId = ref('')
const remoteInstallingId = ref('')
const remoteChatGptLoginId = ref('')
const remoteSaving = ref(false)
const remoteEditingId = ref('')
const remoteDraft = ref<CodexRemoteConnectionPayload>(emptyRemoteDraft())
const remotePortDraft = ref('')
const apiKeyDialogConnection = ref<CodexRemoteConnection | null>(null)
const apiKeyDraft = ref('')
const apiKeySaving = ref(false)

const remoteConnections = computed(() => props.workspace.remote_connections ?? [])
const remoteStatuses = computed(() => props.workspace.remote_connection_statuses ?? {})
const activeRemoteConnectionId = computed(
  () => props.workspace.active_remote_connection_id ?? '',
)
const activeRemoteLabel = computed(() => {
  const remote = remoteConnections.value.find(
    (connection) => connection.id === activeRemoteConnectionId.value,
  )
  return remote?.display_name || 'Local'
})

function emptyRemoteDraft(): CodexRemoteConnectionPayload {
  return {
    display_name: '',
    ssh_host: '',
    ssh_port: null,
    ssh_alias: '',
    identity_file: '',
    remote_path: '~',
    auto_connect: false,
  }
}

function remoteTitle(connection: CodexRemoteConnection) {
  return connection.display_name || connection.ssh_alias || connection.ssh_host
}

function remoteSubtitle(connection: CodexRemoteConnection) {
  const target = connection.ssh_alias || connection.ssh_host
  const port = connection.ssh_port ? `:${connection.ssh_port}` : ''
  return `${target}${port} · ${connection.remote_path || '~'}`
}

function remoteStatus(connection: CodexRemoteConnection) {
  return remoteStatuses.value[connection.id] ?? {
    status: connection.id === activeRemoteConnectionId.value ? 'connecting' : 'disconnected',
    detail: connection.id === activeRemoteConnectionId.value ? 'Connecting' : 'Disconnected',
  }
}

function remoteStatusLabel(connection: CodexRemoteConnection) {
  const status = remoteStatus(connection).status
  if (status === 'connected') {
    return 'Connected'
  }
  if (status === 'connecting') {
    return 'Connecting'
  }
  if (status === 'error') {
    return 'Connection failed'
  }
  return 'Disconnected'
}

function remoteStatusClass(connection: CodexRemoteConnection) {
  const status = remoteStatus(connection).status
  if (status === 'connected') {
    return 'bg-emerald-50 text-emerald-700'
  }
  if (status === 'connecting') {
    return 'bg-amber-50 text-amber-700'
  }
  if (status === 'error') {
    return 'bg-red-50 text-red-700'
  }
  return 'bg-slate-100 text-slate-600'
}

function openAddRemoteDialog() {
  remoteEditingId.value = ''
  remoteDraft.value = emptyRemoteDraft()
  remotePortDraft.value = ''
  remoteError.value = ''
  remoteDialogVisible.value = true
}

function openEditRemoteDialog(connection: CodexRemoteConnection) {
  remoteEditingId.value = connection.id
  remoteDraft.value = {
    display_name: connection.display_name,
    ssh_host: connection.ssh_host,
    ssh_port: connection.ssh_port ?? null,
    ssh_alias: connection.ssh_alias,
    identity_file: connection.identity_file,
    remote_path: connection.remote_path || '~',
    auto_connect: connection.auto_connect,
  }
  remotePortDraft.value = connection.ssh_port ? String(connection.ssh_port) : ''
  remoteError.value = ''
  remoteDialogVisible.value = true
}

function closeRemoteDialog() {
  remoteDialogVisible.value = false
  remoteEditingId.value = ''
  remoteError.value = ''
}

function openApiKeyDialog(connection: CodexRemoteConnection) {
  apiKeyDialogConnection.value = connection
  apiKeyDraft.value = ''
  remoteError.value = ''
}

function closeApiKeyDialog() {
  apiKeyDialogConnection.value = null
  apiKeyDraft.value = ''
}

async function saveRemoteConnection() {
  remoteError.value = ''
  const payload = {
    ...remoteDraft.value,
    ssh_port: remotePortDraft.value.trim() ? Number(remotePortDraft.value.trim()) : null,
  }
  if (payload.ssh_port !== null && !Number.isInteger(payload.ssh_port)) {
    remoteError.value = 'SSH port must be an integer.'
    return
  }
  if (!payload.ssh_alias.trim() && !payload.ssh_host.trim()) {
    remoteError.value = 'Hostname or alias is required.'
    return
  }
  remoteSaving.value = true
  try {
    if (remoteEditingId.value) {
      await apiPut<CodexRemoteConnectionResponse>(
        `/api/codex/remote-connections/${encodeURIComponent(remoteEditingId.value)}`,
        payload,
      )
    } else {
      await apiPost<CodexRemoteConnectionResponse>('/api/codex/remote-connections', payload)
    }
    closeRemoteDialog()
    emit('remoteConnectionChanged')
  } catch (error) {
    remoteError.value = error instanceof Error ? error.message : String(error)
  } finally {
    remoteSaving.value = false
  }
}

async function activateRemoteConnection(connectionId: string) {
  if (props.busy) {
    return
  }
  remoteError.value = ''
  try {
    const path = connectionId
      ? `/api/codex/remote-connections/${encodeURIComponent(connectionId)}/activate`
      : '/api/codex/remote-connections/activate-local'
    await apiPost(path, {})
    emit('remoteConnectionChanged')
  } catch (error) {
    remoteError.value = error instanceof Error ? error.message : String(error)
  }
}

async function testRemoteConnection(connection: CodexRemoteConnection) {
  remoteTestingId.value = connection.id
  remoteError.value = ''
  try {
    const result = await apiPost<CodexRemoteConnectionTestResponse>(
      `/api/codex/remote-connections/${encodeURIComponent(connection.id)}/test`,
      {},
    )
    remoteError.value = result.ok ? `Connected: ${result.detail}` : result.detail
  } catch (error) {
    remoteError.value = error instanceof Error ? error.message : String(error)
  } finally {
    remoteTestingId.value = ''
  }
}

async function restartRemoteConnection(connection: CodexRemoteConnection) {
  remoteError.value = ''
  try {
    await apiPost(
      `/api/codex/remote-connections/${encodeURIComponent(connection.id)}/restart`,
      {},
    )
    emit('remoteConnectionChanged')
  } catch (error) {
    remoteError.value = error instanceof Error ? error.message : String(error)
  }
}

async function installRemoteCodex(connection: CodexRemoteConnection) {
  remoteInstallingId.value = connection.id
  remoteError.value = ''
  try {
    const result = await apiPost<CodexRemoteConnectionTestResponse>(
      `/api/codex/remote-connections/${encodeURIComponent(connection.id)}/install`,
      {},
    )
    remoteError.value = result.ok ? `Installed: ${result.detail}` : result.detail
    emit('remoteConnectionChanged')
  } catch (error) {
    remoteError.value = error instanceof Error ? error.message : String(error)
  } finally {
    remoteInstallingId.value = ''
  }
}

async function loginRemoteApiKey() {
  const connection = apiKeyDialogConnection.value
  const apiKey = apiKeyDraft.value.trim()
  if (!connection || !apiKey) {
    remoteError.value = 'API key is required.'
    return
  }
  apiKeySaving.value = true
  remoteError.value = ''
  try {
    const result = await apiPost<CodexRemoteConnectionTestResponse>(
      `/api/codex/remote-connections/${encodeURIComponent(connection.id)}/login-api-key`,
      { apiKey },
    )
    remoteError.value = result.ok ? result.detail : result.detail
    closeApiKeyDialog()
    emit('remoteConnectionChanged')
  } catch (error) {
    remoteError.value = error instanceof Error ? error.message : String(error)
  } finally {
    apiKeySaving.value = false
  }
}

async function loginRemoteChatGpt(connection: CodexRemoteConnection) {
  remoteChatGptLoginId.value = connection.id
  remoteError.value = ''
  try {
    const result = await apiPost<CodexRemoteConnectionChatGptLoginResponse>(
      `/api/codex/remote-connections/${encodeURIComponent(connection.id)}/login-chatgpt`,
      {},
    )
    if (!result.ok) {
      remoteError.value = result.detail
      return
    }
    if (result.auth_url) {
      window.open(result.auth_url, '_blank', 'noopener,noreferrer')
    }
    remoteError.value = 'Complete ChatGPT login in the browser.'
    emit('remoteConnectionChanged')
  } catch (error) {
    remoteError.value = error instanceof Error ? error.message : String(error)
  } finally {
    remoteChatGptLoginId.value = ''
  }
}

async function deleteRemoteConnection(connection: CodexRemoteConnection) {
  remoteError.value = ''
  try {
    await apiPost(
      `/api/codex/remote-connections/${encodeURIComponent(connection.id)}/delete`,
      {},
    )
    emit('remoteConnectionChanged')
  } catch (error) {
    remoteError.value = error instanceof Error ? error.message : String(error)
  }
}
</script>

<template>
  <section class="border-b border-[color:var(--app-border)] px-3 py-2">
    <div class="mb-2 flex items-center justify-between gap-2">
      <div class="min-w-0">
        <p class="m-0 truncate text-xs font-semibold uppercase tracking-wide text-[color:var(--app-text-soft)]">
          Environment
        </p>
        <p class="m-0 truncate text-sm font-semibold text-[color:var(--app-text)]">
          {{ activeRemoteLabel }}
        </p>
      </div>
      <Button
        icon="pi pi-plus"
        severity="secondary"
        text
        rounded
        size="small"
        aria-label="Add SSH connection"
        data-codex-add-remote
        :disabled="busy"
        @click="openAddRemoteDialog"
      />
    </div>

    <div class="grid gap-1">
      <button
        type="button"
        class="grid grid-cols-[1rem_minmax(0,1fr)] items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition hover:bg-white/65 disabled:cursor-wait disabled:opacity-60"
        data-codex-local-remote
        :class="!activeRemoteConnectionId ? 'font-semibold text-[color:var(--app-text)]' : 'text-[color:var(--app-text-soft)]'"
        :disabled="busy"
        @click="activateRemoteConnection('')"
      >
        <i class="pi pi-desktop text-[0.72rem]"></i>
        <span class="truncate">Local</span>
      </button>

      <div
        v-for="connection in remoteConnections"
        :key="connection.id"
        class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-1 rounded-md px-2 py-1.5 transition hover:bg-white/65"
        data-codex-remote-row
      >
        <button
          type="button"
          class="min-w-0 text-left disabled:cursor-wait disabled:opacity-60"
          :disabled="busy"
          @click="activateRemoteConnection(connection.id)"
        >
          <span
            class="block truncate text-sm"
            :class="connection.id === activeRemoteConnectionId ? 'font-semibold text-[color:var(--app-text)]' : 'font-medium text-[color:var(--app-text-soft)]'"
          >
            {{ remoteTitle(connection) }}
          </span>
          <span class="block truncate text-[0.68rem] text-[color:var(--app-text-soft)]">
            {{ remoteSubtitle(connection) }}
          </span>
          <span
            class="mt-1 inline-flex max-w-full items-center rounded px-1.5 py-0.5 text-[0.62rem] font-semibold"
            :class="remoteStatusClass(connection)"
            :title="remoteStatus(connection).detail"
            data-codex-remote-runtime-status
          >
            {{ remoteStatusLabel(connection) }}
          </span>
        </button>
        <div class="flex items-center gap-0.5">
          <Button
            icon="pi pi-refresh"
            severity="secondary"
            text
            rounded
            size="small"
            class="!h-6 !w-6 !min-w-6 !p-0 !text-[0.68rem]"
            aria-label="Restart SSH connection"
            data-codex-restart-remote
            :disabled="busy"
            @click.stop="restartRemoteConnection(connection)"
          />
          <Button
            icon="pi pi-download"
            severity="secondary"
            text
            rounded
            size="small"
            class="!h-6 !w-6 !min-w-6 !p-0 !text-[0.68rem]"
            aria-label="Install Codex on remote"
            data-codex-install-remote
            :loading="remoteInstallingId === connection.id"
            :disabled="busy"
            @click.stop="installRemoteCodex(connection)"
          />
          <Button
            icon="pi pi-key"
            severity="secondary"
            text
            rounded
            size="small"
            class="!h-6 !w-6 !min-w-6 !p-0 !text-[0.68rem]"
            aria-label="Sign in with API key"
            data-codex-login-api-key-remote
            :disabled="busy"
            @click.stop="openApiKeyDialog(connection)"
          />
          <Button
            icon="pi pi-sign-in"
            severity="secondary"
            text
            rounded
            size="small"
            class="!h-6 !w-6 !min-w-6 !p-0 !text-[0.68rem]"
            aria-label="Sign in with ChatGPT"
            data-codex-login-chatgpt-remote
            :loading="remoteChatGptLoginId === connection.id"
            :disabled="busy"
            @click.stop="loginRemoteChatGpt(connection)"
          />
          <Button
            icon="pi pi-verified"
            severity="secondary"
            text
            rounded
            size="small"
            class="!h-6 !w-6 !min-w-6 !p-0 !text-[0.68rem]"
            aria-label="Test SSH connection"
            data-codex-test-remote
            :loading="remoteTestingId === connection.id"
            :disabled="busy"
            @click.stop="testRemoteConnection(connection)"
          />
          <Button
            icon="pi pi-pencil"
            severity="secondary"
            text
            rounded
            size="small"
            class="!h-6 !w-6 !min-w-6 !p-0 !text-[0.68rem]"
            aria-label="Edit SSH connection"
            data-codex-edit-remote
            :disabled="busy"
            @click.stop="openEditRemoteDialog(connection)"
          />
          <Button
            icon="pi pi-trash"
            severity="secondary"
            text
            rounded
            size="small"
            class="!h-6 !w-6 !min-w-6 !p-0 !text-[0.68rem]"
            aria-label="Delete SSH connection"
            data-codex-delete-remote
            :disabled="busy"
            @click.stop="deleteRemoteConnection(connection)"
          />
        </div>
      </div>
    </div>

    <p
      v-if="remoteError"
      class="m-0 mt-2 line-clamp-2 text-[0.72rem] text-[color:var(--app-text-soft)]"
      data-codex-remote-status
    >
      {{ remoteError }}
    </p>
  </section>

  <div
    v-if="remoteDialogVisible"
    class="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4"
    data-codex-remote-dialog
    @click.self="closeRemoteDialog"
  >
    <section class="grid w-full max-w-md gap-3 rounded-lg border border-[color:var(--app-border)] bg-white p-4 shadow-xl">
      <header class="flex items-center justify-between gap-3">
        <h2 class="m-0 text-base font-semibold text-[color:var(--app-text)]">
          {{ remoteEditingId ? 'Edit SSH connection' : 'Add SSH connection' }}
        </h2>
        <Button
          icon="pi pi-times"
          severity="secondary"
          text
          rounded
          aria-label="Close SSH connection dialog"
          @click="closeRemoteDialog"
        />
      </header>

      <label class="grid gap-1 text-sm font-medium text-[color:var(--app-text)]">
        Display name
        <InputText
          v-model="remoteDraft.display_name"
          data-codex-remote-display-name
        />
      </label>
      <label class="grid gap-1 text-sm font-medium text-[color:var(--app-text)]">
        Hostname
        <InputText
          v-model="remoteDraft.ssh_host"
          placeholder="host.com or user@host.com"
          data-codex-remote-host
        />
      </label>
      <div class="grid grid-cols-[minmax(0,1fr)_7rem] gap-2">
        <label class="grid gap-1 text-sm font-medium text-[color:var(--app-text)]">
          Alias
          <InputText
            v-model="remoteDraft.ssh_alias"
            data-codex-remote-alias
          />
        </label>
        <label class="grid gap-1 text-sm font-medium text-[color:var(--app-text)]">
          Port
          <InputText
            v-model="remotePortDraft"
            data-codex-remote-port
          />
        </label>
      </div>
      <label class="grid gap-1 text-sm font-medium text-[color:var(--app-text)]">
        Identity file
        <InputText
          v-model="remoteDraft.identity_file"
          data-codex-remote-identity
        />
      </label>
      <label class="grid gap-1 text-sm font-medium text-[color:var(--app-text)]">
        Remote path
        <InputText
          v-model="remoteDraft.remote_path"
          data-codex-remote-path
        />
      </label>
      <label class="flex items-center gap-2 text-sm font-medium text-[color:var(--app-text)]">
        <input
          v-model="remoteDraft.auto_connect"
          type="checkbox"
          class="h-4 w-4"
          data-codex-remote-auto-connect
        >
        Auto connect
      </label>

      <p
        v-if="remoteError"
        class="m-0 text-sm text-red-700"
        data-codex-remote-dialog-error
      >
        {{ remoteError }}
      </p>
      <footer class="flex justify-end gap-2">
        <Button
          label="Cancel"
          severity="secondary"
          outlined
          @click="closeRemoteDialog"
        />
        <Button
          label="Save"
          icon="pi pi-save"
          data-codex-save-remote
          :loading="remoteSaving"
          @click="saveRemoteConnection"
        />
      </footer>
    </section>
  </div>

  <div
    v-if="apiKeyDialogConnection"
    class="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4"
    data-codex-remote-api-key-dialog
    @click.self="closeApiKeyDialog"
  >
    <section class="grid w-full max-w-md gap-3 rounded-lg border border-[color:var(--app-border)] bg-white p-4 shadow-xl">
      <header class="flex items-center justify-between gap-3">
        <h2 class="m-0 text-base font-semibold text-[color:var(--app-text)]">
          Sign in to {{ remoteTitle(apiKeyDialogConnection) }}
        </h2>
        <Button
          icon="pi pi-times"
          severity="secondary"
          text
          rounded
          aria-label="Close API key dialog"
          @click="closeApiKeyDialog"
        />
      </header>
      <label class="grid gap-1 text-sm font-medium text-[color:var(--app-text)]">
        API key
        <InputText
          v-model="apiKeyDraft"
          type="password"
          autocomplete="off"
          data-codex-remote-api-key
          @keydown.enter.prevent="loginRemoteApiKey"
        />
      </label>
      <p
        v-if="remoteError"
        class="m-0 text-sm text-red-700"
        data-codex-remote-api-key-error
      >
        {{ remoteError }}
      </p>
      <footer class="flex justify-end gap-2">
        <Button
          label="Cancel"
          severity="secondary"
          outlined
          @click="closeApiKeyDialog"
        />
        <Button
          label="Sign in"
          icon="pi pi-key"
          data-codex-remote-api-key-submit
          :loading="apiKeySaving"
          @click="loginRemoteApiKey"
        />
      </footer>
    </section>
  </div>
</template>
