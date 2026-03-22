<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Button from 'primevue/button'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import ScrollPanel from 'primevue/scrollpanel'
import Tag from 'primevue/tag'

import ChatComposer from './components/ChatComposer.vue'
import ChatTimeline from './components/ChatTimeline.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import {
  ApiError,
  apiDelete,
  apiGet,
  apiPost,
  apiPut,
  openPersistentEventStream,
  streamChat,
} from './lib/api'
import type {
  ChatActivity,
  ChatStreamDoneEvent,
  ChatStreamEvent,
  ChatStreamRequest,
  ConfigResponse,
  DeleteSessionResponse,
  EditableAllowedRoot,
  EditableMcpServer,
  HealthResponse,
  McpConfigResponse,
  SessionListResponse,
  SessionSummary,
  SessionTranscriptResponse,
  ShellActivityState,
  ShellEventEntry,
  ShellProcessSnapshot,
  ShellRawPayload,
  StoredMessage,
  UiChatMessage,
} from './types/api'

const SESSION_STORAGE_KEY = 'yier.active-session-id'
const route = useRoute()
const router = useRouter()
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
const sessionHistory = ref<SessionSummary[]>([])
const composerText = ref('')
const deletingSessionId = ref('')
const savingState = reactive({
  llm: false,
  roots: false,
  mcp: false,
  reloadingMcp: false,
})
const llmForm = reactive({
  baseUrl: '',
  model: '',
  apiKey: '',
})
const rootsDraft = ref<EditableAllowedRoot[]>([])
const mcpDraft = ref<EditableMcpServer[]>([])
const isSettingsRoute = computed(() => route.name === 'settings')
const isChatRoute = computed(() => route.name !== 'settings')
const defaultAllowedRoots = computed(() => health.value?.allowed_roots ?? [])
let closePersistentEventStream: (() => void) | null = null
const backgroundActivityIdsByToolCallId = new Map<string, string>()

const SHELL_TOOL_NAMES = new Set([
  'run_command',
  'start_background_command',
  'read_background_command',
  'wait_background_command',
  'stop_background_command',
  'send_background_command_input',
])

const BACKGROUND_SHELL_TOOL_NAMES = new Set([
  'start_background_command',
  'read_background_command',
  'wait_background_command',
  'stop_background_command',
  'send_background_command_input',
])

const sessionLabel = computed(() => {
  if (!activeSessionId.value) {
    return 'Not ready'
  }
  return activeSessionId.value.slice(0, 8)
})

const llmReady = computed(() => health.value?.llm.ready ?? false)
const frontendMode = computed(() => health.value?.frontend.mode ?? 'missing')
const canSend = computed(() => llmReady.value && !isSending.value && Boolean(activeSessionId.value))
const workspaceEyebrow = computed(() =>
  isSettingsRoute.value ? 'Configuration workspace' : 'Chat workspace',
)
const workspaceTitle = computed(() =>
  isSettingsRoute.value
    ? 'Adjust the assistant without leaving the main console'
    : 'One calm surface for code, files, and config',
)
const sessionHistoryCount = computed(() => sessionHistory.value.length)

watch(activeSessionId, (value) => {
  if (!value) {
    localStorage.removeItem(SESSION_STORAGE_KEY)
    return
  }
  localStorage.setItem(SESSION_STORAGE_KEY, value)
})

onMounted(async () => {
  startPersistentEvents()
  await bootstrap()
})

onBeforeUnmount(() => {
  closePersistentEventStream?.()
  closePersistentEventStream = null
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
  const [healthPayload, configPayload, mcpPayload, sessionsPayload] = await Promise.all([
    apiGet<HealthResponse>('/api/health'),
    apiGet<ConfigResponse>('/api/config'),
    apiGet<McpConfigResponse>('/api/config/mcp'),
    apiGet<SessionListResponse>('/api/chat/sessions'),
  ])

  health.value = healthPayload
  config.value = configPayload
  mcpConfig.value = mcpPayload
  sessionHistory.value = normalizeSessionSummaries(sessionsPayload)
  llmForm.baseUrl = configPayload.llm.base_url
  llmForm.model = configPayload.llm.model
  llmForm.apiKey = ''
  rootsDraft.value = toEditableAllowedRoots(configPayload.allowed_roots)
  mcpDraft.value = toEditableMcpServers(mcpPayload)
}

async function ensureSession() {
  if (activeSessionId.value) {
    try {
      const transcript = await apiGet<SessionTranscriptResponse>(
        `/api/chat/sessions/${activeSessionId.value}`,
      )
      chatMessages.value = toUiMessages(transcript.messages)
      activities.value = []
      backgroundActivityIdsByToolCallId.clear()
      replaySessionActivityEvents(
        Array.isArray(transcript.activity_events) ? transcript.activity_events : [],
      )
      return
    } catch {
      activeSessionId.value = ''
    }
  }

  await startNewSession(false)
}

async function startNewSession(navigateToChat = true) {
  const payload = await apiPost<{ session_id: string }>('/api/chat/sessions', {})
  activeSessionId.value = payload.session_id
  chatMessages.value = []
  activities.value = []
  backgroundActivityIdsByToolCallId.clear()
  await refreshSessionHistory()
  successMessage.value = 'Started a fresh session.'
  if (navigateToChat && !isChatRoute.value) {
    await router.push({ name: 'chat' })
  }
}

function handleNewChatClick() {
  void startNewSession()
}

async function submitMessage() {
  const content = composerText.value.trim()
  if (!content || !canSend.value) {
    return
  }

  errorMessage.value = ''
  successMessage.value = ''
  isSending.value = true
  activities.value = activities.value.filter(
    (item) => item.kind === 'background' && item.state === 'running',
  )
  composerText.value = ''
  chatMessages.value.push(makeUiMessage('user', content))

  const body: ChatStreamRequest = {
    session_id: activeSessionId.value,
    message: content,
  }

  try {
    await streamChat(body, handleStreamEvent)
    await refreshSessionHistory()
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
    activities.value.push(
      makeActivity({
        title: 'Run failed',
        detail: toErrorMessage(error),
        state: 'error',
        kind: 'status',
      }),
    )
  } finally {
    isSending.value = false
  }
}

async function refreshSessionHistory() {
  const payload = await apiGet<SessionListResponse>('/api/chat/sessions')
  sessionHistory.value = normalizeSessionSummaries(payload)
}

async function openSessionFromHistory(sessionId: string) {
  if (!sessionId || sessionId === activeSessionId.value) {
    if (!isChatRoute.value) {
      await router.push({ name: 'chat' })
    }
    return
  }

  activeSessionId.value = sessionId
  chatMessages.value = []
  activities.value = []
  backgroundActivityIdsByToolCallId.clear()
  await ensureSession()
  if (!isChatRoute.value) {
    await router.push({ name: 'chat' })
  }
}

async function deleteSessionFromHistory(sessionId: string) {
  deletingSessionId.value = sessionId
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await apiDelete<DeleteSessionResponse>(`/api/chat/sessions/${sessionId}`)
    if (!response.deleted) {
      throw new Error('Failed to delete session.')
    }

    await refreshSessionHistory()

    if (activeSessionId.value === sessionId) {
      const nextSessionId = sessionHistory.value[0]?.session_id
      if (nextSessionId) {
        await openSessionFromHistory(nextSessionId)
      } else {
        await startNewSession(false)
      }
    }

    successMessage.value = 'Session deleted.'
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
  } finally {
    deletingSessionId.value = ''
  }
}

function startPersistentEvents() {
  closePersistentEventStream?.()
  closePersistentEventStream = openPersistentEventStream((event) => {
    if (!isRelevantPersistentEvent(event)) {
      return
    }
    handleStreamEvent(event)
  })
}

function isRelevantPersistentEvent(event: ChatStreamEvent) {
  const eventSessionId = 'session_id' in event.data ? event.data.session_id : ''
  if (!eventSessionId) {
    return false
  }
  return eventSessionId === activeSessionId.value
}

function handleStreamEvent(event: ChatStreamEvent) {
  if (event.event === 'run_started') {
    activities.value.push(
      makeActivity({
        title: 'Thinking',
        detail: 'Yier is preparing the next response.',
        state: 'running',
        kind: 'status',
      }),
    )
    return
  }

  if (event.event === 'tool_call_start') {
    if (isShellToolName(event.data.tool_name)) {
      handleShellToolStart(event.data.tool_call_id, event.data.tool_name, event.data.arguments)
      appendActivityMeta(
        resolveShellActivityId(event.data.tool_call_id, event.data.tool_name, event.data.arguments),
        `Iteration ${event.data.iteration}`,
      )
      return
    }

    upsertActivity(event.data.tool_call_id, {
      id: event.data.tool_call_id,
      kind: 'tool',
      title: event.data.tool_name,
      detail: formatToolArguments(event.data.arguments),
      state: 'running',
      command: '',
      cwd: '',
      stdout: '',
      stderr: '',
      meta: [`Iteration ${event.data.iteration}`],
      shell: null,
    })
    return
  }

  if (event.event === 'tool_call_end') {
    if (isShellToolName(event.data.tool_name) && isShellRawPayload(event.data.raw)) {
      handleShellToolEnd(
        event.data.tool_call_id,
        event.data.tool_name,
        event.data.raw,
        event.data.metadata ?? {},
        event.data.result,
        event.data.is_error,
        event.data.iteration,
      )
      return
    }

    upsertActivity(event.data.tool_call_id, {
      id: event.data.tool_call_id,
      kind: 'tool',
      title: event.data.tool_name,
      detail: event.data.result,
      state: event.data.is_error ? 'error' : 'done',
      command: '',
      cwd: '',
      stdout: '',
      stderr: '',
      meta: [`Iteration ${event.data.iteration}`],
      shell: null,
    })
    return
  }

  if (event.event === 'command_start') {
    upsertShellActivity(event.data.tool_call_id, {
      id: event.data.tool_call_id,
      kind: 'command',
      title: 'Shell command',
      detail: 'Streaming command output.',
      state: 'running',
      command: event.data.command,
      cwd: event.data.cwd,
      stdout: '',
      stderr: '',
      meta: [event.data.tool_name],
      shell: makeShellState({
        kind: 'shell_command',
        toolName: event.data.tool_name,
        toolCallId: event.data.tool_call_id,
        request: {
          command: event.data.command,
          cwd: event.data.cwd,
        },
        process: {
          session_id: null,
          state: 'running',
          exit_code: null,
          started_at: 0,
          finished_at: null,
          runtime_seconds: 0,
          timed_out: false,
        },
      }),
    })
    return
  }

  if (event.event === 'command_output') {
    appendActivityOutput(event.data.tool_call_id, event.data.stream, event.data.content)
    return
  }

  if (event.event === 'command_end') {
    upsertShellActivity(event.data.tool_call_id, {
      id: event.data.tool_call_id,
      kind: 'command',
      title: 'Shell command',
      detail: event.data.timed_out
        ? `Timed out with exit code ${event.data.exit_code}.`
        : `Finished with exit code ${event.data.exit_code}.`,
      state: event.data.exit_code === 0 && !event.data.timed_out ? 'done' : 'error',
      command: event.data.command,
      cwd: event.data.cwd,
      stdout: '',
      stderr: '',
      meta: [event.data.tool_name],
      shell: {
        kind: 'shell_command',
        tool_name: event.data.tool_name,
        tool_call_id: event.data.tool_call_id,
        session_id: null,
        request: {
          command: event.data.command,
          cwd: event.data.cwd,
        },
        process: {
          session_id: null,
          state: event.data.timed_out
            ? 'timed_out'
            : event.data.exit_code === 0
              ? 'completed'
              : 'failed',
          exit_code: event.data.exit_code,
          started_at: 0,
          finished_at: null,
          runtime_seconds: 0,
          timed_out: event.data.timed_out,
        },
        events: [],
        latest_event_index: null,
        streams: {
          stdout: { text: '', truncated: false },
          stderr: { text: '', truncated: false },
        },
        events_truncated: false,
        dropped_event_count: 0,
      },
    })
    return
  }

  if (event.event === 'background_command_started') {
    const activityId = getBackgroundActivityId(event.data.background_session_id)
    backgroundActivityIdsByToolCallId.set(event.data.tool_call_id, activityId)
    rekeyActivity(event.data.tool_call_id, activityId)
    upsertShellActivity(activityId, {
      id: activityId,
      kind: 'background',
      title: `Background ${event.data.background_session_id}`,
      detail: 'Background task is running.',
      state: 'running',
      command: event.data.command,
      cwd: event.data.cwd,
      stdout: '',
      stderr: '',
      meta: [event.data.tool_name],
      shell: makeShellState({
        kind: 'background_command',
        toolName: event.data.tool_name,
        toolCallId: event.data.tool_call_id,
        sessionId: event.data.background_session_id,
        request: {
          command: event.data.command,
          cwd: event.data.cwd,
        },
        process: {
          session_id: event.data.background_session_id,
          state: event.data.state,
          exit_code: null,
          started_at: 0,
          finished_at: null,
          runtime_seconds: 0,
          timed_out: false,
        },
      }),
    })
    return
  }

  if (event.event === 'background_command_output') {
    appendActivityOutput(
      getBackgroundActivityId(event.data.background_session_id),
      event.data.stream,
      event.data.content,
    )
    return
  }

  if (event.event === 'background_command_end') {
    const activityId = getBackgroundActivityId(event.data.background_session_id)
    upsertShellActivity(activityId, {
      id: activityId,
      kind: 'background',
      title: `Background ${event.data.background_session_id}`,
      detail:
        event.data.exit_code === null
          ? `Finished with state ${event.data.state}.`
          : `Finished with state ${event.data.state} and exit code ${event.data.exit_code}.`,
      state:
        event.data.state === 'completed'
          ? 'done'
          : event.data.state === 'running'
            ? 'running'
            : 'error',
      command: event.data.command,
      cwd: event.data.cwd,
      stdout: '',
      stderr: '',
      meta: [],
      shell: {
        kind: 'background_command',
        tool_name: 'background_command',
        tool_call_id: '',
        session_id: event.data.background_session_id,
        request: {
          command: event.data.command,
          cwd: event.data.cwd,
        },
        process: {
          session_id: event.data.background_session_id,
          state: event.data.state,
          exit_code: event.data.exit_code,
          started_at: 0,
          finished_at: null,
          runtime_seconds: 0,
          timed_out: false,
        },
        events: [],
        latest_event_index: null,
        streams: {
          stdout: { text: '', truncated: false },
          stderr: { text: '', truncated: false },
        },
        events_truncated: false,
        dropped_event_count: 0,
      },
    })
    return
  }

  if (event.event === 'background_followup_queued') {
    appendActivityMeta(
      getBackgroundActivityId(event.data.background_session_id),
      `Queued ${event.data.queue_id}: ${event.data.prompt}`,
    )
    return
  }

  if (event.event === 'background_followup_started') {
    activities.value.push(
      makeActivity({
        id: `followup:${event.data.queue_id}`,
        title: `Follow-up ${event.data.queue_id}`,
        detail: event.data.prompt,
        state: 'running',
        kind: 'status',
        meta: [`Waiting on ${event.data.background_session_id}`],
      }),
    )
    return
  }

  if (event.event === 'background_followup_finished') {
    upsertActivity(`followup:${event.data.queue_id}`, {
      id: `followup:${event.data.queue_id}`,
      kind: 'status',
      title: `Follow-up ${event.data.queue_id}`,
      detail: `Completed with finish reason ${event.data.finish_reason}.`,
      state: event.data.finish_reason === 'stop' ? 'done' : 'error',
      command: '',
      cwd: '',
      stdout: '',
      stderr: '',
      meta: [`Triggered by ${event.data.background_session_id}`],
      shell: null,
    })
    return
  }

  if (event.event === 'reasoning') {
    activities.value.push(
      makeActivity({
        title: 'Reasoning',
        detail: event.data.content,
        state: 'info',
        kind: 'reasoning',
      }),
    )
    return
  }

  if (event.event === 'assistant_message') {
    chatMessages.value.push(makeUiMessage('assistant', event.data.content))
    return
  }

  if (event.event === 'error') {
    errorMessage.value = event.data.message
    activities.value.push(
      makeActivity({
        title: 'Error',
        detail: event.data.message,
        state: 'error',
        kind: 'status',
      }),
    )
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

async function saveAllowedRoots() {
  savingState.roots = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    config.value = await apiPut<ConfigResponse>('/api/config/roots', {
      allowed_roots: rootsDraft.value.map((root) => root.path),
    })
    health.value = await apiGet<HealthResponse>('/api/health')
    rootsDraft.value = toEditableAllowedRoots(config.value.allowed_roots)
    successMessage.value = 'Allowed directories updated.'
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
  } finally {
    savingState.roots = false
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

function addAllowedRoot() {
  rootsDraft.value.push({
    id: crypto.randomUUID(),
    path: '',
  })
}

function removeAllowedRoot(rootId: string) {
  rootsDraft.value = rootsDraft.value.filter((item) => item.id !== rootId)
}

function resetAllowedRoots() {
  rootsDraft.value = toEditableAllowedRoots(defaultAllowedRoots.value)
}

function removeMcpServer(serverId: string) {
  mcpDraft.value = mcpDraft.value.filter((item) => item.id !== serverId)
}

function openSettings() {
  void router.push({ name: 'settings' })
}

function openChat() {
  void router.push({ name: 'chat' })
}

function toUiMessages(messages: StoredMessage[]): UiChatMessage[] {
  return messages
    .filter(
      (message) => (message.role === 'user' || message.role === 'assistant') && message.content,
    )
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

function replaySessionActivityEvents(activityEvents: SessionTranscriptResponse['activity_events']) {
  for (const event of activityEvents) {
    handleStreamEvent(event as ChatStreamEvent)
  }
}

function formatSessionUpdatedAt(timestamp: number) {
  if (!timestamp) {
    return ''
  }
  return new Date(timestamp * 1000).toLocaleString()
}

function normalizeSessionSummaries(payload: Partial<SessionListResponse> | null | undefined) {
  return Array.isArray(payload?.sessions) ? payload.sessions : []
}

function makeActivity(
  overrides: Partial<ChatActivity> & Pick<ChatActivity, 'title' | 'detail' | 'state' | 'kind'>,
): ChatActivity {
  return {
    id: overrides.id ?? crypto.randomUUID(),
    kind: overrides.kind,
    title: overrides.title,
    detail: overrides.detail,
    state: overrides.state,
    command: overrides.command ?? '',
    cwd: overrides.cwd ?? '',
    stdout: overrides.stdout ?? '',
    stderr: overrides.stderr ?? '',
    meta: overrides.meta ?? [],
    shell: overrides.shell ?? null,
  }
}

function isShellToolName(toolName: string) {
  return SHELL_TOOL_NAMES.has(toolName)
}

function getBackgroundActivityId(sessionId: string) {
  return `bg:${sessionId}`
}

function resolveShellActivityId(
  toolCallId: string,
  toolName: string,
  argumentsValue: Record<string, unknown>,
) {
  const registeredActivityId = backgroundActivityIdsByToolCallId.get(toolCallId)
  if (registeredActivityId) {
    return registeredActivityId
  }

  if (BACKGROUND_SHELL_TOOL_NAMES.has(toolName) && typeof argumentsValue.session_id === 'string') {
    return getBackgroundActivityId(argumentsValue.session_id)
  }

  return toolCallId
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isShellRawPayload(value: unknown): value is ShellRawPayload {
  if (!isRecord(value)) {
    return false
  }

  if (value.kind !== 'shell_command' && value.kind !== 'background_command') {
    return false
  }

  return (
    isRecord(value.request) &&
    isRecord(value.process) &&
    isRecord(value.streams) &&
    Array.isArray(value.events)
  )
}

function makeShellState(options: {
  kind: ShellActivityState['kind']
  toolName: string
  toolCallId: string
  request?: Record<string, unknown>
  sessionId?: string | null
  process?: ShellProcessSnapshot | null
}): ShellActivityState {
  return {
    kind: options.kind,
    tool_name: options.toolName,
    tool_call_id: options.toolCallId,
    session_id: options.sessionId ?? null,
    request: options.request ?? {},
    process: options.process ?? null,
    events: [],
    latest_event_index: null,
    streams: {
      stdout: { text: '', truncated: false },
      stderr: { text: '', truncated: false },
    },
    events_truncated: false,
    dropped_event_count: 0,
  }
}

function normalizeShellRaw(
  raw: ShellRawPayload,
  toolName: string,
  toolCallId: string,
): ShellActivityState {
  return {
    kind: raw.kind,
    tool_name: toolName,
    tool_call_id: toolCallId,
    session_id: raw.process.session_id,
    request: raw.request,
    process: raw.process,
    events: sortShellEvents(raw.events),
    latest_event_index: raw.latest_event_index,
    streams: raw.streams,
    events_truncated: raw.events_truncated,
    dropped_event_count: raw.dropped_event_count,
  }
}

function sortShellEvents(events: ShellEventEntry[]) {
  return [...events].sort((left, right) => left.index - right.index)
}

function mergeShellEvents(current: ShellEventEntry[], incoming: ShellEventEntry[]) {
  const merged = new Map<number, ShellEventEntry>()
  for (const event of [...current, ...incoming]) {
    merged.set(event.index, event)
  }
  return sortShellEvents([...merged.values()])
}

function mergeShellState(
  current: ShellActivityState | null,
  incoming: ShellActivityState | null,
): ShellActivityState | null {
  if (!incoming) {
    return current
  }
  if (!current) {
    return incoming
  }

  return {
    kind: incoming.kind,
    tool_name: incoming.tool_name || current.tool_name,
    tool_call_id: incoming.tool_call_id || current.tool_call_id,
    session_id: incoming.session_id ?? current.session_id,
    request: {
      ...current.request,
      ...incoming.request,
    },
    process: incoming.process ?? current.process,
    events: mergeShellEvents(current.events, incoming.events),
    latest_event_index: incoming.latest_event_index ?? current.latest_event_index,
    streams: incoming.streams,
    events_truncated: current.events_truncated || incoming.events_truncated,
    dropped_event_count: Math.max(current.dropped_event_count, incoming.dropped_event_count),
  }
}

function upsertActivity(activityId: string, nextValue: ChatActivity) {
  const target = activities.value.find((item) => item.id === activityId)
  if (!target) {
    activities.value.push(nextValue)
    return
  }

  target.kind = nextValue.kind
  target.title = nextValue.title
  target.detail = nextValue.detail
  target.state = nextValue.state
  target.command = nextValue.command || target.command
  target.cwd = nextValue.cwd || target.cwd
  target.stdout = nextValue.stdout || target.stdout
  target.stderr = nextValue.stderr || target.stderr
  if (nextValue.meta.length) {
    target.meta = dedupeMeta([...target.meta, ...nextValue.meta])
  }
  target.shell = nextValue.shell
}

function upsertShellActivity(activityId: string, nextValue: ChatActivity) {
  const target = activities.value.find((item) => item.id === activityId)
  if (!target) {
    activities.value.push(nextValue)
    return
  }

  target.kind = nextValue.kind
  target.title = nextValue.title
  target.detail = nextValue.detail
  target.state = nextValue.state
  target.command = nextValue.command || target.command
  target.cwd = nextValue.cwd || target.cwd
  target.stdout = nextValue.stdout || target.stdout
  target.stderr = nextValue.stderr || target.stderr
  if (nextValue.meta.length) {
    target.meta = dedupeMeta([...target.meta, ...nextValue.meta])
  }
  target.shell = mergeShellState(target.shell, nextValue.shell)
}

function rekeyActivity(sourceId: string, targetId: string) {
  if (sourceId === targetId) {
    return
  }

  const sourceIndex = activities.value.findIndex((item) => item.id === sourceId)
  if (sourceIndex === -1) {
    return
  }

  const source = activities.value[sourceIndex]
  if (!source) {
    return
  }
  const targetExists = activities.value.some((item) => item.id === targetId)
  if (!targetExists) {
    source.id = targetId
    return
  }

  const movedActivity: ChatActivity = {
    ...source,
    id: targetId,
  }
  upsertShellActivity(targetId, movedActivity)
  activities.value.splice(sourceIndex, 1)
}

function appendActivityOutput(activityId: string, stream: 'stdout' | 'stderr', content: string) {
  const target = activities.value.find((item) => item.id === activityId)
  if (!target) {
    return
  }

  if (stream === 'stdout') {
    target.stdout += content
    if (target.shell) {
      target.shell.streams.stdout = {
        ...target.shell.streams.stdout,
        text: target.stdout,
      }
    }
    return
  }
  target.stderr += content
  if (target.shell) {
    target.shell.streams.stderr = {
      ...target.shell.streams.stderr,
      text: target.stderr,
    }
  }
}

function appendActivityMeta(activityId: string, note: string) {
  const target = activities.value.find((item) => item.id === activityId)
  if (!target) {
    return
  }
  target.meta = dedupeMeta([...target.meta, note])
}

function dedupeMeta(values: string[]) {
  return [...new Set(values.filter((value) => value.trim()))]
}

function formatToolArguments(argumentsValue: Record<string, unknown>) {
  return JSON.stringify(argumentsValue, null, 2)
}

function activityStateFromShell(
  process: ShellProcessSnapshot | null,
  isError = false,
): ChatActivity['state'] {
  if (!process) {
    return isError ? 'error' : 'running'
  }
  if (process.state === 'running' || process.state === 'stopping') {
    return 'running'
  }
  if (process.state === 'completed' && !process.timed_out && process.exit_code === 0 && !isError) {
    return 'done'
  }
  return 'error'
}

function shellDetailFromProcess(process: ShellProcessSnapshot | null, fallback: string) {
  if (!process) {
    return fallback
  }
  if (process.state === 'running') {
    return 'Running.'
  }
  if (process.timed_out) {
    return `Timed out with exit code ${process.exit_code ?? 'unknown'}.`
  }
  if (process.exit_code === null) {
    return `Finished with state ${process.state}.`
  }
  return `Finished with state ${process.state} and exit code ${process.exit_code}.`
}

function shellTitle(kind: ShellActivityState['kind'], sessionId: string | null) {
  if (kind === 'background_command') {
    return sessionId ? `Background ${sessionId}` : 'Background command'
  }
  return 'Shell command'
}

function shellCommandFromRequest(request: Record<string, unknown>, fallback: string) {
  return typeof request.command === 'string' ? request.command : fallback
}

function shellCwdFromRequest(request: Record<string, unknown>, fallback: string) {
  return typeof request.cwd === 'string' ? request.cwd : fallback
}

function handleShellToolStart(
  toolCallId: string,
  toolName: string,
  argumentsValue: Record<string, unknown>,
) {
  const activityId = resolveShellActivityId(toolCallId, toolName, argumentsValue)
  const isBackground = BACKGROUND_SHELL_TOOL_NAMES.has(toolName)
  const sessionId = typeof argumentsValue.session_id === 'string' ? argumentsValue.session_id : null
  upsertShellActivity(activityId, {
    id: activityId,
    kind: isBackground ? 'background' : 'command',
    title: shellTitle(isBackground ? 'background_command' : 'shell_command', sessionId),
    detail: isBackground ? 'Background command update.' : 'Preparing shell command.',
    state: 'running',
    command: typeof argumentsValue.command === 'string' ? argumentsValue.command : '',
    cwd: typeof argumentsValue.cwd === 'string' ? argumentsValue.cwd : '',
    stdout: '',
    stderr: '',
    meta: [toolName],
    shell: makeShellState({
      kind: isBackground ? 'background_command' : 'shell_command',
      toolName,
      toolCallId,
      request: argumentsValue,
      sessionId,
    }),
  })
}

function handleShellToolEnd(
  toolCallId: string,
  toolName: string,
  raw: ShellRawPayload,
  metadata: Record<string, unknown>,
  result: string,
  isError: boolean,
  iteration: number,
) {
  const shell = normalizeShellRaw(raw, toolName, toolCallId)
  const activityId =
    raw.kind === 'background_command' && raw.process.session_id
      ? getBackgroundActivityId(raw.process.session_id)
      : (backgroundActivityIdsByToolCallId.get(toolCallId) ?? toolCallId)

  if (raw.kind === 'background_command' && raw.process.session_id) {
    backgroundActivityIdsByToolCallId.set(toolCallId, activityId)
    rekeyActivity(toolCallId, activityId)
  }

  const meta = [toolName, `Iteration ${iteration}`]
  if (metadata.truncated === true) {
    meta.push('Output truncated')
  }

  upsertShellActivity(activityId, {
    id: activityId,
    kind: raw.kind === 'background_command' ? 'background' : 'command',
    title: shellTitle(raw.kind, raw.process.session_id),
    detail: shellDetailFromProcess(shell.process, result),
    state: activityStateFromShell(shell.process, isError),
    command: shellCommandFromRequest(raw.request, ''),
    cwd: shellCwdFromRequest(raw.request, ''),
    stdout: raw.streams.stdout.text,
    stderr: raw.streams.stderr.text,
    meta,
    shell,
  })
}

function toEditableAllowedRoots(paths: string[]): EditableAllowedRoot[] {
  return paths.map((path) => ({
    id: crypto.randomUUID(),
    path,
  }))
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
            :severity="
              frontendMode === 'proxy' ? 'info' : frontendMode === 'static' ? 'success' : 'warn'
            "
            rounded
          />
        </div>
        <div class="side-card-row">
          <span class="side-card-label">LLM</span>
          <Tag
            :value="llmReady ? 'Ready' : 'Needs setup'"
            :severity="llmReady ? 'success' : 'warn'"
            rounded
          />
        </div>
      </div>

      <div class="rail-actions">
        <Button label="New Chat" icon="pi pi-plus" fluid @click="handleNewChatClick" />
      </div>

      <div class="side-card side-card--history">
        <div class="side-card-row">
          <p class="side-card-label">Recent sessions</p>
          <Tag :value="String(sessionHistoryCount)" severity="secondary" rounded />
        </div>

        <ScrollPanel v-if="sessionHistory.length" class="session-history-scroll">
          <div class="session-history-list">
            <div
              v-for="session in sessionHistory"
              :key="session.session_id"
              class="session-history-item"
              :class="{ 'session-history-item--active': session.session_id === activeSessionId }"
            >
              <button
                type="button"
                class="session-history-main"
                @click="openSessionFromHistory(session.session_id)"
              >
                <div class="session-history-copy">
                  <p class="session-history-title">{{ session.title }}</p>
                  <p v-if="session.preview" class="session-history-preview">
                    {{ session.preview }}
                  </p>
                  <p class="session-history-meta">
                    {{ formatSessionUpdatedAt(session.updated_at) }}
                    <span v-if="session.message_count"> · {{ session.message_count }} msgs</span>
                  </p>
                </div>
              </button>
              <Button
                icon="pi pi-trash"
                class="session-history-delete"
                text
                rounded
                severity="secondary"
                size="small"
                :loading="deletingSessionId === session.session_id"
                @click.stop="deleteSessionFromHistory(session.session_id)"
              />
            </div>
          </div>
        </ScrollPanel>

        <p v-else class="side-card-empty">No saved sessions yet.</p>
      </div>

      <div class="side-card side-card--nav">
        <Button
          label="Chat"
          icon="pi pi-comment"
          fluid
          :outlined="!isChatRoute"
          :severity="isChatRoute ? undefined : 'secondary'"
          @click="openChat"
        />
        <Button
          label="Settings"
          icon="pi pi-sliders-h"
          fluid
          :outlined="!isSettingsRoute"
          :severity="isSettingsRoute ? undefined : 'secondary'"
          @click="openSettings"
        />
      </div>

      <div class="side-card side-card--muted">
        <p class="side-card-label">Allowed roots</p>
        <ul class="root-list">
          <li v-for="root in config?.allowed_roots ?? []" :key="root">{{ root }}</li>
        </ul>
      </div>
    </aside>

    <main class="workspace-panel">
      <header class="workspace-header">
        <div>
          <p class="eyebrow">{{ workspaceEyebrow }}</p>
          <h2>{{ workspaceTitle }}</h2>
        </div>
        <Button
          :label="isChatRoute ? 'Settings' : 'Back to Chat'"
          :icon="isChatRoute ? 'pi pi-sliders-h' : 'pi pi-comments'"
          severity="secondary"
          text
          @click="isChatRoute ? openSettings() : openChat()"
        />
      </header>

      <Message v-if="errorMessage" severity="error" class="status-banner">{{
        errorMessage
      }}</Message>
      <Message v-else-if="successMessage" severity="success" class="status-banner">
        {{ successMessage }}
      </Message>

      <section v-if="isBooting" class="loading-state">
        <ProgressSpinner stroke-width="4" />
        <p>Preparing your local workspace…</p>
      </section>

      <template v-else>
        <section v-if="!llmReady && isChatRoute" class="empty-state">
          <p class="eyebrow">Setup needed</p>
          <h3>Configure the LLM connection before sending messages.</h3>
          <p>
            Add `base_url`, `api_key`, and `model` in Settings. Your values stay on this machine in
            `~/.yier/web/settings.json`.
          </p>
          <Button label="Open Settings" icon="pi pi-cog" @click="openSettings" />
        </section>

        <section v-else class="workspace-content">
          <template v-if="isChatRoute">
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
          <SettingsPanel
            v-else
            :health="health"
            :config="config"
            :mcp-config="mcpConfig"
            :llm-form="llmForm"
            :roots-draft="rootsDraft"
            :mcp-draft="mcpDraft"
            :saving-llm="savingState.llm"
            :saving-roots="savingState.roots"
            :saving-mcp="savingState.mcp"
            :reloading-mcp="savingState.reloadingMcp"
            @save-llm="saveLlmSettings"
            @save-roots="saveAllowedRoots"
            @reset-roots="resetAllowedRoots"
            @add-root="addAllowedRoot"
            @remove-root="removeAllowedRoot"
            @save-mcp="saveMcpSettings"
            @reload-mcp="reloadMcpSettings"
            @add-mcp="addMcpServer"
            @remove-mcp="removeMcpServer"
          />
        </section>
      </template>
    </main>
  </div>
</template>
