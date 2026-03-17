<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'

import Button from 'primevue/button'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import Tag from 'primevue/tag'

import ChatComposer from './components/ChatComposer.vue'
import ChatTimeline from './components/ChatTimeline.vue'
import SettingsDrawer from './components/SettingsDrawer.vue'
import { ApiError, apiGet, apiPost, apiPut, streamChat } from './lib/api'
import type {
  ChatActivity,
  ChatStreamDoneEvent,
  ChatStreamEvent,
  ChatStreamRequest,
  ConfigResponse,
  EditableMcpServer,
  HealthResponse,
  McpConfigResponse,
  SessionTranscriptResponse,
  StoredMessage,
  UiChatMessage,
} from './types/api'

const SESSION_STORAGE_KEY = 'yier.active-session-id'
const SETTINGS_STORAGE_KEY = 'yier.settings-visible'

const settingsVisible = ref(localStorage.getItem(SETTINGS_STORAGE_KEY) === 'true')
const isBooting = ref(true)
const isSending = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const health = ref<HealthResponse | null>(null)
const config = ref<ConfigResponse | null>(null)
const mcpConfig = ref<McpConfigResponse | null>(null)
const activeSessionId = ref(localStorage.getItem(SESSION_STORAGE_KEY) ?? '')
const chatMessages = ref<UiChatMessage[]>([])
const activities = ref<ChatActivity[]>([])
const composerText = ref('')
const savingState = reactive({
  llm: false,
  mcp: false,
  reloadingMcp: false,
})
const llmForm = reactive({
  baseUrl: '',
  model: '',
  apiKey: '',
})
const mcpDraft = ref<EditableMcpServer[]>([])

const sessionLabel = computed(() => {
  if (!activeSessionId.value) {
    return 'Not ready'
  }
  return activeSessionId.value.slice(0, 8)
})

const llmReady = computed(() => health.value?.llm.ready ?? false)
const frontendMode = computed(() => health.value?.frontend.mode ?? 'missing')
const canSend = computed(() => llmReady.value && !isSending.value && Boolean(activeSessionId.value))

watch(settingsVisible, (value) => {
  localStorage.setItem(SETTINGS_STORAGE_KEY, String(value))
})

watch(activeSessionId, (value) => {
  if (!value) {
    localStorage.removeItem(SESSION_STORAGE_KEY)
    return
  }
  localStorage.setItem(SESSION_STORAGE_KEY, value)
})

onMounted(async () => {
  await bootstrap()
})

async function bootstrap() {
  isBooting.value = true
  errorMessage.value = ''
  try {
    await refreshDashboard()
    await ensureSession()
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
  } finally {
    isBooting.value = false
  }
}

async function refreshDashboard() {
  const [healthPayload, configPayload, mcpPayload] = await Promise.all([
    apiGet<HealthResponse>('/api/health'),
    apiGet<ConfigResponse>('/api/config'),
    apiGet<McpConfigResponse>('/api/config/mcp'),
  ])

  health.value = healthPayload
  config.value = configPayload
  mcpConfig.value = mcpPayload
  llmForm.baseUrl = configPayload.llm.base_url
  llmForm.model = configPayload.llm.model
  llmForm.apiKey = ''
  mcpDraft.value = toEditableMcpServers(mcpPayload)
}

async function ensureSession() {
  if (activeSessionId.value) {
    try {
      const transcript = await apiGet<SessionTranscriptResponse>(
        `/api/chat/sessions/${activeSessionId.value}`,
      )
      chatMessages.value = toUiMessages(transcript.messages)
      return
    } catch {
      activeSessionId.value = ''
    }
  }

  await startNewSession()
}

async function startNewSession() {
  const payload = await apiPost<{ session_id: string }>('/api/chat/sessions', {})
  activeSessionId.value = payload.session_id
  chatMessages.value = []
  activities.value = []
  successMessage.value = 'Started a fresh session.'
}

async function submitMessage() {
  const content = composerText.value.trim()
  if (!content || !canSend.value) {
    return
  }

  errorMessage.value = ''
  successMessage.value = ''
  isSending.value = true
  activities.value = []
  composerText.value = ''
  chatMessages.value.push(makeUiMessage('user', content))

  const body: ChatStreamRequest = {
    session_id: activeSessionId.value,
    message: content,
  }

  try {
    await streamChat(body, handleStreamEvent)
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
    activities.value.push({
      id: crypto.randomUUID(),
      title: 'Run failed',
      detail: toErrorMessage(error),
      state: 'error',
    })
  } finally {
    isSending.value = false
  }
}

function handleStreamEvent(event: ChatStreamEvent) {
  if (event.event === 'run_started') {
    activities.value.push({
      id: crypto.randomUUID(),
      title: 'Thinking',
      detail: 'Yier is preparing the next response.',
      state: 'running',
    })
    return
  }

  if (event.event === 'tool_call_start') {
    activities.value.push({
      id: event.data.tool_call_id,
      title: event.data.tool_name,
      detail: JSON.stringify(event.data.arguments),
      state: 'running',
    })
    return
  }

  if (event.event === 'tool_call_end') {
    const target = activities.value.find((item) => item.id === event.data.tool_call_id)
    if (target) {
      target.detail = event.data.result
      target.state = event.data.is_error ? 'error' : 'done'
      return
    }

    activities.value.push({
      id: event.data.tool_call_id,
      title: event.data.tool_name,
      detail: event.data.result,
      state: event.data.is_error ? 'error' : 'done',
    })
    return
  }

  if (event.event === 'reasoning') {
    activities.value.push({
      id: crypto.randomUUID(),
      title: 'Reasoning',
      detail: event.data.content,
      state: 'info',
    })
    return
  }

  if (event.event === 'assistant_message') {
    chatMessages.value.push(makeUiMessage('assistant', event.data.content))
    return
  }

  if (event.event === 'error') {
    errorMessage.value = event.data.message
    activities.value.push({
      id: crypto.randomUUID(),
      title: 'Error',
      detail: event.data.message,
      state: 'error',
    })
    return
  }

  const doneEvent = event as ChatStreamDoneEvent
  if (doneEvent.data.finish_reason === 'stop') {
    successMessage.value = 'Response ready.'
  }
}

async function saveLlmSettings() {
  savingState.llm = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    config.value = await apiPut<ConfigResponse>('/api/config/llm', {
      base_url: llmForm.baseUrl,
      model: llmForm.model,
      api_key: llmForm.apiKey,
    })
    llmForm.apiKey = ''
    health.value = await apiGet<HealthResponse>('/api/health')
    successMessage.value = 'LLM settings saved.'
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
  } finally {
    savingState.llm = false
  }
}

async function saveMcpSettings() {
  savingState.mcp = true
  errorMessage.value = ''
  successMessage.value = ''

  try {
    const payload = {
      mcp_servers: fromEditableMcpServers(mcpDraft.value),
    }
    mcpConfig.value = await apiPut<McpConfigResponse>('/api/config/mcp', payload)
    health.value = await apiGet<HealthResponse>('/api/health')
    config.value = await apiGet<ConfigResponse>('/api/config')
    mcpDraft.value = toEditableMcpServers(mcpConfig.value)
    successMessage.value = 'MCP configuration saved.'
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
  } finally {
    savingState.mcp = false
  }
}

async function reloadMcpSettings() {
  savingState.reloadingMcp = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    mcpConfig.value = await apiPost<McpConfigResponse>('/api/config/mcp/reload', {})
    health.value = await apiGet<HealthResponse>('/api/health')
    config.value = await apiGet<ConfigResponse>('/api/config')
    successMessage.value = 'MCP runtime reloaded.'
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
  } finally {
    savingState.reloadingMcp = false
  }
}

function addMcpServer() {
  mcpDraft.value.push({
    id: crypto.randomUUID(),
    name: `server-${mcpDraft.value.length + 1}`,
    type: 'stdio',
    enabled: true,
    status: '',
    command: '',
    url: '',
    argsText: '[]',
    envText: '{}',
    headersText: '{}',
  })
}

function removeMcpServer(serverId: string) {
  mcpDraft.value = mcpDraft.value.filter((item) => item.id !== serverId)
}

function openSettings() {
  settingsVisible.value = true
}

function toUiMessages(messages: StoredMessage[]): UiChatMessage[] {
  return messages
    .filter((message) => (message.role === 'user' || message.role === 'assistant') && message.content)
    .map((message) =>
      makeUiMessage(message.role === 'user' ? 'user' : 'assistant', message.content ?? ''),
    )
}

function makeUiMessage(role: 'user' | 'assistant', content: string): UiChatMessage {
  return {
    id: crypto.randomUUID(),
    role,
    content,
  }
}

function toEditableMcpServers(payload: McpConfigResponse): EditableMcpServer[] {
  return Object.entries(payload.mcp_servers).map(([name, server]) => ({
    id: crypto.randomUUID(),
    name,
    type: server.type,
    enabled: server.enabled ?? true,
    status: server.status ?? '',
    command: server.command ?? '',
    url: server.url ?? '',
    argsText: JSON.stringify(server.args ?? [], null, 2),
    envText: JSON.stringify(server.env ?? {}, null, 2),
    headersText: JSON.stringify(server.headers ?? {}, null, 2),
  }))
}

function fromEditableMcpServers(servers: EditableMcpServer[]) {
  const result: Record<string, Record<string, unknown>> = {}
  for (const server of servers) {
    const name = server.name.trim()
    if (!name) {
      throw new Error('Every MCP server needs a name.')
    }

    if (server.type === 'stdio') {
      result[name] = {
        type: 'stdio',
        enabled: server.enabled,
        status: server.status || undefined,
        command: server.command.trim(),
        args: parseJsonText(server.argsText, 'array'),
        env: parseJsonText(server.envText, 'object'),
      }
      continue
    }

    result[name] = {
      type: server.type,
      enabled: server.enabled,
      status: server.status || undefined,
      url: server.url.trim(),
      headers: parseJsonText(server.headersText, 'object'),
    }
  }
  return result
}

function parseJsonText(value: string, expected: 'array' | 'object') {
  const trimmed = value.trim()
  if (!trimmed) {
    return expected === 'array' ? [] : {}
  }

  const parsed = JSON.parse(trimmed)
  const isArray = Array.isArray(parsed)
  if (expected === 'array' && !isArray) {
    throw new Error('Expected a JSON array.')
  }
  if (expected === 'object' && (isArray || typeof parsed !== 'object' || parsed === null)) {
    throw new Error('Expected a JSON object.')
  }
  return parsed
}

function toErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    return error.message
  }
  if (error instanceof Error) {
    return error.message
  }
  return 'Something went wrong.'
}
</script>

<template>
  <div class="app-shell">
    <aside class="side-rail">
      <div class="brand-panel">
        <p class="eyebrow">Local-first assistant</p>
        <h1>yier</h1>
        <p class="brand-copy">
          Chat with your local agent, manage MCP connections, and keep everything anchored to your
          own machine.
        </p>
      </div>

      <div class="side-card">
        <div class="side-card-row">
          <span class="side-card-label">Session</span>
          <Tag :value="sessionLabel" rounded />
        </div>
        <div class="side-card-row">
          <span class="side-card-label">Frontend</span>
          <Tag
            :value="frontendMode"
            :severity="frontendMode === 'proxy' ? 'info' : frontendMode === 'static' ? 'success' : 'warn'"
            rounded
          />
        </div>
        <div class="side-card-row">
          <span class="side-card-label">LLM</span>
          <Tag :value="llmReady ? 'Ready' : 'Needs setup'" :severity="llmReady ? 'success' : 'warn'" rounded />
        </div>
      </div>

      <div class="rail-actions">
        <Button label="New Chat" icon="pi pi-plus" fluid @click="startNewSession" />
        <Button
          label="Settings"
          icon="pi pi-cog"
          fluid
          severity="secondary"
          outlined
          @click="openSettings"
        />
      </div>

      <div class="side-card side-card--muted">
        <p class="side-card-label">Allowed roots</p>
        <ul class="root-list">
          <li v-for="root in health?.allowed_roots ?? []" :key="root">{{ root }}</li>
        </ul>
      </div>
    </aside>

    <main class="workspace-panel">
      <header class="workspace-header">
        <div>
          <p class="eyebrow">Chat workspace</p>
          <h2>One calm surface for code, files, and config</h2>
        </div>
        <Button
          label="Settings"
          icon="pi pi-sliders-h"
          severity="secondary"
          text
          @click="openSettings"
        />
      </header>

      <Message v-if="errorMessage" severity="error" class="status-banner">{{ errorMessage }}</Message>
      <Message v-else-if="successMessage" severity="success" class="status-banner">
        {{ successMessage }}
      </Message>

      <section v-if="isBooting" class="loading-state">
        <ProgressSpinner stroke-width="4" />
        <p>Preparing your local workspace…</p>
      </section>

      <template v-else>
        <section v-if="!llmReady" class="empty-state">
          <p class="eyebrow">Setup needed</p>
          <h3>Configure the LLM connection before sending messages.</h3>
          <p>
            Add `base_url`, `api_key`, and `model` in Settings. Your values stay on this machine in
            `~/.yier/web/settings.json`.
          </p>
          <Button label="Open Settings" icon="pi pi-cog" @click="openSettings" />
        </section>

        <template v-else>
          <ChatTimeline
            :messages="chatMessages"
            :activities="activities"
            :is-sending="isSending"
            :session-label="sessionLabel"
          />
          <ChatComposer
            v-model="composerText"
            :disabled="!canSend"
            :is-sending="isSending"
            @submit="submitMessage"
          />
        </template>
      </template>
    </main>

    <SettingsDrawer
      :visible="settingsVisible"
      :health="health"
      :config="config"
      :mcp-config="mcpConfig"
      :llm-form="llmForm"
      :mcp-draft="mcpDraft"
      :saving-llm="savingState.llm"
      :saving-mcp="savingState.mcp"
      :reloading-mcp="savingState.reloadingMcp"
      @close="settingsVisible = false"
      @save-llm="saveLlmSettings"
      @save-mcp="saveMcpSettings"
      @reload-mcp="reloadMcpSettings"
      @add-mcp="addMcpServer"
      @remove-mcp="removeMcpServer"
    />
  </div>
</template>
