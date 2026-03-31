<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Button from 'primevue/button'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import ScrollPanel from 'primevue/scrollpanel'
import Select from 'primevue/select'
import Tag from 'primevue/tag'

import ChatComposer from './components/ChatComposer.vue'
import ChatTimeline from './components/ChatTimeline.vue'
import ChannelWorkspacePanel from './components/ChannelWorkspacePanel.vue'
import CodexSessionExplorer from './components/CodexSessionExplorer.vue'
import CodexWorkbar from './components/CodexWorkbar.vue'
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
  ApprovalDecision,
  ApprovalResponseRequest,
  ApprovalFormFieldState,
  ApprovalFormMode,
  BackendId,
  BackendRuntime,
  BackgroundCommandListRawPayload,
  ChannelAccountActionResponse,
  ChannelConfigResponse,
  ChannelLoginRequest,
  ChannelPlatformsResponse,
  ChannelWorkspaceResponse,
  ChatActivity,
  ChatApprovalRequestedEvent,
  ChatAssistantDeltaEvent,
  CodexWorkMode,
  CodexTurnTiming,
  CodexPairedEditorStateRequest,
  CodexWorkspaceResponse,
  ChatStreamDoneEvent,
  ChatStreamErrorEvent,
  ChatStreamEvent,
  ChatStreamRequest,
  ChatTurnAbortedEvent,
  ConfigResponse,
  CreateSessionRequest,
  DeleteSessionResponse,
  EditableAllowedRoot,
  EditableMcpServer,
  HealthResponse,
  LlmProvider,
  McpConfigResponse,
  OpenCodexSessionResponse,
  PendingApproval,
  SaveAppSettingsRequest,
  SessionListResponse,
  SessionSummary,
  SessionTranscriptResponse,
  ShellActivityState,
  ShellEventEntry,
  ShellProcessSnapshot,
  ShellRawPayload,
  StoredMessage,
  ToolActivityState,
  ToolDigestRawPayload,
  ToolRawPayload,
  UiChatMessage,
  WorkspaceSurface,
} from './types/api'

const SESSION_STORAGE_KEY = 'yier.active-session-id'
const WORKSPACE_SURFACE_STORAGE_KEY = 'yier.workspace-surface'
const CODEX_COMPACT_MEDIA_QUERY = '(max-width: 1023px)'
const LLM_PROVIDER_DEFAULTS: Record<
  Exclude<LlmProvider, ''>,
  { baseUrl: string; model: string }
> = {
  zai: {
    baseUrl: 'https://api.z.ai/api/paas/v4',
    model: 'glm-4.7-flash',
  },
  'zai-coding-plan': {
    baseUrl: 'https://api.z.ai/api/coding/paas/v4',
    model: 'glm-4.7-flash',
  },
}
const route = useRoute()
const router = useRouter()
const chatDockRef = ref<HTMLElement | null>(null)
const chatDockHeight = ref(0)
let chatDockResizeObserver: ResizeObserver | null = null

function updateChatDockHeight() {
  chatDockHeight.value = chatDockRef.value?.offsetHeight ?? 0
}

function normalizeWorkspaceSurface(value: string | null | undefined): WorkspaceSurface {
  if (value === 'codex' || value === 'yier' || value === 'claude') {
    return value
  }
  return 'yier'
}

function readCachedWorkspaceSurface(): WorkspaceSurface {
  return normalizeWorkspaceSurface(localStorage.getItem(WORKSPACE_SURFACE_STORAGE_KEY))
}

const isBooting = ref(true)
const isSending = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const health = ref<HealthResponse | null>(null)
const config = ref<ConfigResponse | null>(null)
const mcpConfig = ref<McpConfigResponse | null>(null)
const channelWorkspace = ref<ChannelWorkspaceResponse | null>(null)
const channelPlatforms = ref<ChannelPlatformsResponse | null>(null)
const channelConfig = ref<ChannelConfigResponse | null>(null)
const channelMonitorSessions = ref<SessionSummary[]>([])
const codexWorkspace = ref<CodexWorkspaceResponse | null>(null)
const channelLoginState = reactive({
  qrcodeUrl: '',
  accountId: '',
  status: '',
})
const activeSessionRuntime = ref<BackendRuntime | null>(null)
const activeSessionId = ref(localStorage.getItem(SESSION_STORAGE_KEY) ?? '')
const chatMessages = ref<UiChatMessage[]>([])
const activities = ref<ChatActivity[]>([])
const codexTurnTimings = ref<CodexTurnTiming[]>([])
const sessionHistory = ref<SessionSummary[]>([])
const activeCodexWorkMode = ref<CodexWorkMode>('build')
const isCodexCompactLayout = ref(false)
const isSidebarDrawerOpen = ref(false)
const isRuntimeSheetOpen = ref(false)
const composerText = ref('')
const composerSelectionStart = ref(0)
const composerSelectionEnd = ref(0)
const composerSelectionVersion = ref(0)
const deletingSessionId = ref('')
const savingState = reactive({
  app: false,
  llm: false,
  roots: false,
  mcp: false,
  reloadingMcp: false,
  codexMode: false,
  codexSandbox: false,
})
const appForm = reactive({
  defaultBackendId: 'yier' as BackendId,
  defaultProjectPath: '',
  channelBackendId: 'yier' as BackendId,
  channelProjectPath: '',
  channelAutoApproveCodexRequests: true,
  workspaceSurface: readCachedWorkspaceSurface(),
  codexLauncherCommand: '',
  codexModel: '',
  codexSandbox: 'workspace-write' as 'read-only' | 'workspace-write' | 'danger-full-access',
  codexApprovalPolicy: 'on-request' as 'untrusted' | 'on-failure' | 'on-request' | 'never',
  codexApprovalsReviewer: 'user' as 'user' | 'guardian_subagent',
  codexPersonality: 'friendly' as 'none' | 'friendly' | 'pragmatic',
  codexReasoningEffort: 'medium' as 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh',
  codexShowReasoningCards: false,
  codexServiceTier: '' as '' | 'fast' | 'flex',
})
const newSessionDraft = reactive({
  backendId: 'yier' as BackendId,
  projectPath: '',
})
const llmForm = reactive({
  provider: '' as LlmProvider,
  baseUrl: '',
  model: '',
  apiKey: '',
})
const lastCustomLlmForm = reactive({
  baseUrl: '',
  model: '',
})
const rootsDraft = ref<EditableAllowedRoot[]>([])
const mcpDraft = ref<EditableMcpServer[]>([])
const isSettingsRoute = computed(() => route.name === 'settings')
const isChannelRoute = computed(() => route.name === 'channel')
const isChatRoute = computed(() => route.name === 'chat')
const activeSession = computed(
  () =>
    sessionHistory.value.find((session) => session.session_id === activeSessionId.value) ?? null,
)
const backendOptions = computed(
  () =>
    config.value?.backends ?? [
      { id: 'yier' as BackendId, label: 'Yier Agent' },
      { id: 'codex' as BackendId, label: 'Codex App Server' },
    ],
)
const defaultAllowedRoots = computed(() => health.value?.allowed_roots ?? [])
let closePersistentEventStream: (() => void) | null = null
let pairedEditorSyncTimer: number | null = null
let codexSessionSyncTimer: number | null = null
let codexCompactMediaQuery: MediaQueryList | null = null
let latestSessionLoadRequestId = 0
let lastPairedEditorSyncSignature = ''
let nextTimelineSequence = 0
let currentStreamSequenceHint: number | null = null
const backgroundActivityIdsByToolCallId = new Map<string, string>()
let hydratingLlmForm = false

function createClientId() {
  const cryptoApi = globalThis.crypto
  if (typeof cryptoApi?.randomUUID === 'function') {
    return cryptoApi.randomUUID()
  }

  if (typeof cryptoApi?.getRandomValues === 'function') {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16))
    const versionByte = bytes[6] ?? 0
    const variantByte = bytes[8] ?? 0
    bytes[6] = (versionByte & 0x0f) | 0x40
    bytes[8] = (variantByte & 0x3f) | 0x80
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0'))
    return [
      hex.slice(0, 4).join(''),
      hex.slice(4, 6).join(''),
      hex.slice(6, 8).join(''),
      hex.slice(8, 10).join(''),
      hex.slice(10, 16).join(''),
    ].join('-')
  }

  return `id-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

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
const activeBackendId = computed<BackendId>(() =>
  activeSession.value?.backend_id ?? backendIdForWorkspaceSurface(appForm.workspaceSurface),
)
const activeBackendReady = computed(
  () => {
    const backendHealth = health.value?.backends?.[activeBackendId.value]
    if (backendHealth) {
      return backendHealth.ready
    }
    return activeBackendId.value === 'codex' ? Boolean(appForm.codexLauncherCommand.trim()) : llmReady.value
  },
)
const canSend = computed(() => activeBackendReady.value && !isSending.value && Boolean(activeSessionId.value))
const canSendToSession = computed(() => canSend.value && activeSession.value?.source !== 'channel')
const activeProjectPath = computed(() => activeSession.value?.project_path ?? newSessionDraft.projectPath)
const workspaceEyebrow = computed(() =>
  isSettingsRoute.value
    ? 'Configuration workspace'
    : isChannelRoute.value
      ? 'Channel workspace'
      : isCodexWorkspace.value
        ? 'Codex workspace'
        : 'Chat workspace',
)
const workspaceTitle = computed(() =>
  isSettingsRoute.value
    ? 'Adjust the assistant without leaving the main console'
    : isChannelRoute.value
      ? 'Multi-platform runtime, account status, and live channel sessions'
      : isCodexWorkspace.value
        ? 'Project-aware Codex sessions with mode and permission control'
        : 'One calm surface for code, files, and config',
)
const sessionHistoryCount = computed(() => sessionHistory.value.length)
const isCodexWorkspace = computed(() => isChatRoute.value && activeBackendId.value === 'codex')
const activeCodexProjects = computed(() => codexWorkspace.value?.projects ?? [])
const activeCodexPairedEditors = computed(() => codexWorkspace.value?.paired_editors ?? [])
const assistantLabel = computed(() => (activeBackendId.value === 'codex' ? 'Codex' : 'Yier'))
const showCodexMobileChrome = computed(
  () => isCodexWorkspace.value && isCodexCompactLayout.value,
)
const isMobileChatPage = computed(
  () => isChatRoute.value && isCodexCompactLayout.value,
)
const useFloatingChatDock = computed(
  () => isChatRoute.value,
)
const showSidebarDrawer = computed(
  () => showCodexMobileChrome.value && isSidebarDrawerOpen.value,
)
const showRuntimeSheet = computed(
  () => showCodexMobileChrome.value && isRuntimeSheetOpen.value,
)
const activeWorkspaceSurface = computed<WorkspaceSurface>(() => {
  if (activeSession.value?.backend_id === 'codex') {
    return 'codex'
  }
  if (activeSession.value?.backend_id === 'yier') {
    return 'yier'
  }
  return appForm.workspaceSurface
})
const workspaceSurfaceOptions: Array<{
  label: string
  value: WorkspaceSurface
  disabled: boolean
}> = [
  { label: 'Codex', value: 'codex', disabled: false },
  { label: 'Yier Agent', value: 'yier', disabled: false },
  { label: 'Claude Code', value: 'claude', disabled: true },
]
const workspaceSurfaceModel = computed<WorkspaceSurface>({
  get() {
    return activeWorkspaceSurface.value
  },
  set(value) {
    void switchWorkspaceSurface(value)
  },
})
const composerPlaceholder = computed(() =>
  activeBackendId.value === 'codex'
    ? activeCodexWorkMode.value === 'plan'
      ? 'Ask Codex anything'
      : 'Ask for follow-up changes'
    : 'Ask yier to inspect code, read files, or operate...',
)

function updateCodexCompactLayout(matches: boolean) {
  isCodexCompactLayout.value = matches
}

function handleCodexCompactLayoutChange(event: MediaQueryListEvent) {
  updateCodexCompactLayout(event.matches)
}

function setupCodexCompactLayoutWatcher() {
  codexCompactMediaQuery = window.matchMedia(CODEX_COMPACT_MEDIA_QUERY)
  updateCodexCompactLayout(codexCompactMediaQuery.matches)
  if (typeof codexCompactMediaQuery.addEventListener === 'function') {
    codexCompactMediaQuery.addEventListener('change', handleCodexCompactLayoutChange)
    return
  }
  codexCompactMediaQuery.addListener(handleCodexCompactLayoutChange)
}

function teardownCodexCompactLayoutWatcher() {
  if (!codexCompactMediaQuery) {
    return
  }
  if (typeof codexCompactMediaQuery.removeEventListener === 'function') {
    codexCompactMediaQuery.removeEventListener('change', handleCodexCompactLayoutChange)
  } else {
    codexCompactMediaQuery.removeListener(handleCodexCompactLayoutChange)
  }
  codexCompactMediaQuery = null
}

function closeCodexSheets() {
  isSidebarDrawerOpen.value = false
  isRuntimeSheetOpen.value = false
}

function openSidebarDrawer() {
  isRuntimeSheetOpen.value = false
  isSidebarDrawerOpen.value = true
}

function closeSidebarDrawer() {
  isSidebarDrawerOpen.value = false
}

function closeRuntimeSheet() {
  isRuntimeSheetOpen.value = false
}

function syncSheetScrollLock(locked: boolean) {
  if (typeof document === 'undefined') {
    return
  }
  document.body.classList.toggle('yier-sheet-lock', locked)
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    closeCodexSheets()
  }
}

watch(activeSessionId, (value) => {
  if (!value) {
    localStorage.removeItem(SESSION_STORAGE_KEY)
  } else {
    localStorage.setItem(SESSION_STORAGE_KEY, value)
  }
  schedulePairedEditorStateSync()
})

watch(composerText, () => {
  schedulePairedEditorStateSync()
})

watch(
  () => appForm.workspaceSurface,
  (value) => {
    localStorage.setItem(WORKSPACE_SURFACE_STORAGE_KEY, value)
  },
)

watch(
  () => llmForm.provider,
  (nextProvider, previousProvider) => {
    if (hydratingLlmForm || nextProvider === previousProvider) {
      return
    }

    if (previousProvider === '') {
      lastCustomLlmForm.baseUrl = llmForm.baseUrl
      lastCustomLlmForm.model = llmForm.model
    }

    if (nextProvider === '') {
      llmForm.baseUrl = lastCustomLlmForm.baseUrl
      llmForm.model = lastCustomLlmForm.model
      return
    }

    const defaults = LLM_PROVIDER_DEFAULTS[nextProvider]
    llmForm.baseUrl = defaults.baseUrl
    llmForm.model = defaults.model
  },
  { flush: 'sync' },
)

watch(showCodexMobileChrome, (visible) => {
  if (!visible) {
    closeCodexSheets()
  }
})

watch(
  () => showSidebarDrawer.value || showRuntimeSheet.value,
  (locked) => {
    syncSheetScrollLock(locked)
  },
  { immediate: true },
)

watch(
  chatDockRef,
  (element, previousElement) => {
    if (previousElement && chatDockResizeObserver) {
      chatDockResizeObserver.unobserve(previousElement)
    }

    if (!element) {
      chatDockHeight.value = 0
      return
    }

    if (!chatDockResizeObserver) {
      chatDockResizeObserver = new ResizeObserver(() => {
        updateChatDockHeight()
      })
    }

    chatDockResizeObserver.observe(element)
    updateChatDockHeight()
  },
  { flush: 'post' },
)

onMounted(async () => {
  setupCodexCompactLayoutWatcher()
  window.addEventListener('keydown', handleGlobalKeydown)
  startPersistentEvents()
  await bootstrap()
})

onBeforeUnmount(() => {
  syncSheetScrollLock(false)
  chatDockResizeObserver?.disconnect()
  chatDockResizeObserver = null
  window.removeEventListener('keydown', handleGlobalKeydown)
  teardownCodexCompactLayoutWatcher()
  closePersistentEventStream?.()
  closePersistentEventStream = null
  if (pairedEditorSyncTimer !== null) {
    window.clearTimeout(pairedEditorSyncTimer)
    pairedEditorSyncTimer = null
  }
  if (codexSessionSyncTimer !== null) {
    window.clearTimeout(codexSessionSyncTimer)
    codexSessionSyncTimer = null
  }
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
  const [
    healthPayload,
    configPayload,
    mcpPayload,
    sessionsPayload,
    codexWorkspacePayload,
    channelWorkspacePayload,
    channelPlatformsPayload,
    channelConfigPayload,
    channelMonitorSessionsPayload,
  ] = await Promise.all([
    apiGet<HealthResponse>('/api/health'),
    apiGet<ConfigResponse>('/api/config'),
    apiGet<McpConfigResponse>('/api/config/mcp'),
    apiGet<SessionListResponse>('/api/chat/sessions'),
    safeApiGet<CodexWorkspaceResponse>('/api/codex/workspace', { projects: [], paired_editors: [] }),
    safeApiGet<ChannelWorkspaceResponse>('/api/channel/workspace', { platforms: [], accounts: [] }),
    safeApiGet<ChannelPlatformsResponse>('/api/channel/platforms', { platforms: [] }),
    safeApiGet<ChannelConfigResponse>('/api/channel/config', {
      enabled_platforms: [],
      weixin: {},
    }),
    safeApiGet<SessionListResponse>('/api/channel/monitor/sessions', { sessions: [] }),
  ])

  health.value = healthPayload
  config.value = configPayload
  mcpConfig.value = mcpPayload
  channelWorkspace.value = channelWorkspacePayload
  channelPlatforms.value = channelPlatformsPayload
  channelConfig.value = channelConfigPayload
  channelMonitorSessions.value = normalizeSessionSummaries(channelMonitorSessionsPayload)
  codexWorkspace.value = codexWorkspacePayload
  sessionHistory.value = normalizeSessionSummaries(sessionsPayload)
  hydrateLlmForm(configPayload.llm)
  hydrateAppForm(configPayload)
  initializeNewSessionDraft(configPayload)
  rootsDraft.value = toEditableAllowedRoots(configPayload.allowed_roots)
  mcpDraft.value = toEditableMcpServers(mcpPayload)
}

async function ensureSession() {
  if (activeSessionId.value) {
    try {
      await loadSessionTranscript(activeSessionId.value)
      return
    } catch {
      activeSessionId.value = ''
    }
  }

  if (sessionHistory.value.length) {
    activeSessionId.value = sessionHistory.value[0]?.session_id ?? ''
    if (activeSessionId.value) {
      await ensureSession()
      return
    }
  }

  await startNewSession(false)
}

async function createSession(
  backendId: BackendId,
  projectPath: string,
  navigateToChat = true,
) {
  const payload = await apiPost<{ session_id: string }>('/api/chat/sessions', {
    backend_id: backendId,
    project_path: projectPath,
  } satisfies CreateSessionRequest)
  activeSessionId.value = payload.session_id
  chatMessages.value = []
  activities.value = []
  resetTimelineSequence()
  activeSessionRuntime.value = null
  activeCodexWorkMode.value = 'build'
  backgroundActivityIdsByToolCallId.clear()
  await refreshSessionHistory()
  successMessage.value = 'Started a fresh session.'
  if (navigateToChat && !isChatRoute.value) {
    await router.push({ name: 'chat' })
  }
}

async function startNewSession(navigateToChat = true) {
  await createSession(newSessionDraft.backendId, newSessionDraft.projectPath, navigateToChat)
}

function handleNewChatClick() {
  void startNewSession()
}

function latestChatSessionIdForBackend(backendId: BackendId) {
  return sessionHistory.value.find(
    (session) => session.source === 'chat' && session.backend_id === backendId,
  )?.session_id
}

function startNewCodexSession(projectPath: string) {
  const nextProjectPath = projectPath.trim() || activeProjectPath.value || newSessionDraft.projectPath
  void createSession('codex', nextProjectPath, true)
}

function handleCodexSessionStart(projectPath: string) {
  closeSidebarDrawer()
  startNewCodexSession(projectPath)
}

function backendIdForWorkspaceSurface(surface: WorkspaceSurface): BackendId {
  return surface === 'codex' ? 'codex' : 'yier'
}

function buildDefaultSessionDefaults() {
  return {
    default_backend_id: 'yier' as BackendId,
    default_project_path: defaultAllowedRoots.value[0] ?? '',
    channel_backend_id: 'yier' as BackendId,
    channel_project_path: defaultAllowedRoots.value[0] ?? '',
    channel_auto_approve_codex_requests: true,
    workspace_surface: 'yier' as WorkspaceSurface,
  }
}

function buildDefaultCodexConfig() {
  return {
    launcher_command: 'codex app-server --listen stdio://',
    model: '',
    sandbox: 'workspace-write' as const,
    approval_policy: 'on-request' as const,
    approvals_reviewer: 'user' as const,
    personality: 'friendly' as const,
    reasoning_effort: 'medium' as const,
    show_reasoning_cards: false,
    service_tier: '' as const,
  }
}

async function persistWorkspaceSurfacePreference(surface: WorkspaceSurface) {
  const persistedSessionDefaults = {
    ...buildDefaultSessionDefaults(),
    ...(config.value?.session_defaults ?? {}),
  }
  const persistedCodex = {
    ...buildDefaultCodexConfig(),
    ...(config.value?.codex ?? {}),
  }

  config.value = await apiPut<ConfigResponse>('/api/config/app', {
    session_defaults: {
      ...persistedSessionDefaults,
      workspace_surface: surface,
    },
    codex: persistedCodex,
  } satisfies SaveAppSettingsRequest)
  hydrateAppForm(config.value)
  initializeNewSessionDraft(config.value)
}

async function switchWorkspaceSurface(target: WorkspaceSurface) {
  errorMessage.value = ''
  successMessage.value = ''

  if (target === 'claude') {
    successMessage.value = 'Claude Code workspace is coming soon.'
    return
  }

  const previousWorkspaceSurface = appForm.workspaceSurface
  const previousDraftBackendId = newSessionDraft.backendId
  const backendId = backendIdForWorkspaceSurface(target)
  appForm.workspaceSurface = target
  newSessionDraft.backendId = backendId

  try {
    const existingSessionId = latestChatSessionIdForBackend(backendId)
    if (existingSessionId) {
      await openSessionFromHistory(existingSessionId)
    } else {
      const nextProjectPath = activeProjectPath.value || newSessionDraft.projectPath
      await createSession(backendId, nextProjectPath, true)
    }
  } catch (error) {
    appForm.workspaceSurface = previousWorkspaceSurface
    newSessionDraft.backendId = previousDraftBackendId
    throw error
  }

  try {
    await persistWorkspaceSurfacePreference(target)
  } catch (error) {
    errorMessage.value = `Switched workspace, but failed to save that preference: ${toErrorMessage(error)}`
  }
}

async function submitMessage() {
  const content = composerText.value.trim()
  if (!content || !canSendToSession.value) {
    return
  }

  errorMessage.value = ''
  successMessage.value = ''
  isSending.value = true
  activities.value = activities.value.filter(
    (item) => item.kind === 'background' && item.state === 'running',
  )
  composerText.value = ''
  composerSelectionStart.value = 0
  composerSelectionEnd.value = 0
  composerSelectionVersion.value += 1
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
  const [payload, codexWorkspacePayload] = await Promise.all([
    apiGet<SessionListResponse>('/api/chat/sessions'),
    safeApiGet<CodexWorkspaceResponse>('/api/codex/workspace', { projects: [], paired_editors: [] }),
  ])
  sessionHistory.value = normalizeSessionSummaries(payload)
  codexWorkspace.value = codexWorkspacePayload
}

async function loadSessionTranscript(sessionId: string) {
  const requestId = ++latestSessionLoadRequestId
  const transcript = await apiGet<SessionTranscriptResponse>(
    `/api/chat/sessions/${sessionId}`,
  )
  if (requestId !== latestSessionLoadRequestId || sessionId !== activeSessionId.value) {
    return
  }
  resetTimelineSequence()
  chatMessages.value = toUiMessages(transcript.messages)
  activities.value = []
  codexTurnTimings.value = Array.isArray(transcript.codex_turn_timings)
    ? transcript.codex_turn_timings
    : []
  activeSessionRuntime.value = transcript.backend_runtime ?? null
  activeCodexWorkMode.value = transcript.codex_work_mode ?? 'build'
  backgroundActivityIdsByToolCallId.clear()
  replaySessionActivityEvents(
    Array.isArray(transcript.activity_events) ? transcript.activity_events : [],
  )
  appendPendingApprovalActivities(transcript.pending_approvals ?? [])
}

async function openSessionFromHistory(sessionId: string) {
  closeCodexSheets()
  if (!sessionId || sessionId === activeSessionId.value) {
    if (!isChatRoute.value) {
      await router.push({ name: 'chat' })
    }
    return
  }

  activeSessionId.value = sessionId
  chatMessages.value = []
  activities.value = []
  codexTurnTimings.value = []
  resetTimelineSequence()
  activeSessionRuntime.value = null
  backgroundActivityIdsByToolCallId.clear()
  await ensureSession()
  if (!isChatRoute.value) {
    await router.push({ name: 'chat' })
  }
}

async function openCodexNativeSession(threadId: string) {
  if (!threadId) {
    return
  }

  closeSidebarDrawer()
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const payload = await apiPost<OpenCodexSessionResponse>('/api/codex/sessions/open', {
      thread_id: threadId,
    })
    await refreshSessionHistory()
    await openSessionFromHistory(payload.session_id)
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
  }
}

async function updateCodexWorkMode(mode: CodexWorkMode) {
  if (!activeSessionId.value || activeBackendId.value !== 'codex' || activeCodexWorkMode.value === mode) {
    return
  }

  savingState.codexMode = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    await apiPut<{ ok: boolean }>(`/api/chat/sessions/${activeSessionId.value}/codex-mode`, {
      codex_work_mode: mode,
    })
    activeCodexWorkMode.value = mode
    const activeSummary = sessionHistory.value.find((session) => session.session_id === activeSessionId.value)
    if (activeSummary) {
      activeSummary.codex_work_mode = mode
    }
    successMessage.value = `Codex mode switched to ${mode}.`
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
  } finally {
    savingState.codexMode = false
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

function handleComposerSelectionChange(payload: { start: number; end: number }) {
  composerSelectionStart.value = payload.start
  composerSelectionEnd.value = payload.end
  schedulePairedEditorStateSync()
}

function schedulePairedEditorStateSync() {
  if (pairedEditorSyncTimer !== null) {
    window.clearTimeout(pairedEditorSyncTimer)
  }
  pairedEditorSyncTimer = window.setTimeout(() => {
    pairedEditorSyncTimer = null
    void syncPairedEditorState()
  }, 80)
}

function scheduleCodexSessionSync(sessionId: string) {
  if (!sessionId || sessionId !== activeSessionId.value) {
    return
  }
  if (codexSessionSyncTimer !== null) {
    window.clearTimeout(codexSessionSyncTimer)
  }
  codexSessionSyncTimer = window.setTimeout(() => {
    codexSessionSyncTimer = null
    void loadSessionTranscript(sessionId)
  }, 40)
}

async function syncPairedEditorState() {
  const payload: CodexPairedEditorStateRequest = {
    session_id: activeSessionId.value,
    content: composerText.value,
    selection_start: composerSelectionStart.value,
    selection_end: composerSelectionEnd.value,
  }
  const signature = JSON.stringify(payload)
  if (signature === lastPairedEditorSyncSignature) {
    return
  }

  try {
    await apiPost<{ ok: boolean }>('/api/codex/paired-editor/state', payload)
    lastPairedEditorSyncSignature = signature
  } catch {
    lastPairedEditorSyncSignature = ''
  }
}

function isRelevantPersistentEvent(event: ChatStreamEvent) {
  if (event.event === 'codex_pairings_updated') {
    return true
  }
  if (
    event.event === 'channel_account_state' ||
    event.event === 'channel_login_qr' ||
    event.event === 'channel_error'
  ) {
    return true
  }
  if (event.event === 'channel_inbound_message' || event.event === 'channel_outbound_message') {
    return true
  }
  const eventSessionId = 'session_id' in event.data ? event.data.session_id : ''
  if (!eventSessionId) {
    return false
  }
  return eventSessionId === activeSessionId.value
}

function handleStreamEvent(event: ChatStreamEvent) {
  const previousTimelineSequenceHint = currentStreamSequenceHint
  currentStreamSequenceHint = normalizeTimelineSequence(
    isRecord(event.data) && 'sequence' in event.data ? event.data.sequence : null,
  )
  try {
  if (event.event === 'codex_pairings_updated') {
    codexWorkspace.value = {
      projects: codexWorkspace.value?.projects ?? [],
      paired_editors: event.data.paired_editors,
    }
    return
  }

  if (event.event === 'codex_paired_editor_update') {
    composerText.value = event.data.content
    composerSelectionStart.value = event.data.selection_start
    composerSelectionEnd.value = event.data.selection_end
    composerSelectionVersion.value += 1
    return
  }

  if (event.event === 'codex_session_updated') {
    scheduleCodexSessionSync(event.data.session_id)
    return
  }

  if (event.event === 'channel_account_state') {
    const accounts = channelWorkspace.value?.accounts ?? []
    const nextAccounts = [
      ...accounts.filter((item) => item.account_id !== event.data.account_id),
      event.data,
    ].sort((left, right) => left.account_id.localeCompare(right.account_id))
    if (channelWorkspace.value) {
      channelWorkspace.value = {
        ...channelWorkspace.value,
        accounts: nextAccounts,
      }
    }
    return
  }

  if (event.event === 'channel_login_qr') {
    channelLoginState.accountId = event.data.account_id
    channelLoginState.status = event.data.status
    if (typeof event.data.qrcode_url === 'string') {
      channelLoginState.qrcodeUrl = event.data.qrcode_url
    }
    return
  }

  if (event.event === 'channel_inbound_message' || event.event === 'channel_outbound_message') {
    void refreshSessionHistory()
    return
  }

  if (event.event === 'channel_error') {
    errorMessage.value = event.data.message
    return
  }

  if (event.event === 'run_started') {
    if (activeSessionRuntime.value) {
      activeSessionRuntime.value.status = 'active'
      activeSessionRuntime.value.detail = 'Codex is working on this turn.'
    }
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
      return
    }

    upsertActivity(
      event.data.tool_call_id,
      makeToolDigestActivity({
        toolCallId: event.data.tool_call_id,
        toolName: event.data.tool_name,
        argumentsValue: event.data.arguments,
      }),
    )
    return
  }

  if (event.event === 'tool_call_end') {
    if (isShellToolName(event.data.tool_name) && isShellStreamRawPayload(event.data.raw)) {
      handleShellToolEnd(
        event.data.tool_call_id,
        event.data.tool_name,
        event.data.raw,
        event.data.metadata ?? {},
        event.data.result,
        event.data.is_error,
      )
      return
    }

    if (isShellToolName(event.data.tool_name)) {
      handleShellToolEndFallback(
        event.data.tool_call_id,
        event.data.tool_name,
        event.data.metadata ?? {},
        event.data.result,
        event.data.is_error,
      )
      return
    }

    upsertActivity(
      event.data.tool_call_id,
      makeToolDigestActivity({
        toolCallId: event.data.tool_call_id,
        toolName: event.data.tool_name,
        argumentsValue: {},
        result: event.data.result,
        isError: event.data.is_error,
        metadata: event.data.metadata ?? {},
        raw: toToolDigestRawPayload(event.data.raw),
      }),
    )
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
      meta: [],
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
        events: [
          buildShellEvent(0, 'started', {
            command: event.data.command,
            cwd: event.data.cwd,
          }),
        ],
        latestEventIndex: 0,
      }),
      tool: null,
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
      meta: [],
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
      tool: null,
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
      meta: [],
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
      tool: null,
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
      tool: null,
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
      tool: null,
    })
    return
  }

  if (event.event === 'assistant_delta') {
    appendAssistantDelta(event.data)
    return
  }

  if (event.event === 'reasoning') {
    const activityId = event.data.item_id || `reasoning:${createClientId()}`
    upsertActivity(
      activityId,
      makeActivity({
        id: activityId,
        title: 'Reasoning',
        detail: event.data.content,
        state: 'info',
        kind: 'reasoning',
      }),
    )
    return
  }

  if (event.event === 'plan') {
    const activityId = event.data.item_id || `plan:${createClientId()}`
    upsertActivity(activityId, {
      id: activityId,
      kind: 'plan',
      title: 'Plan',
      detail: event.data.content,
      state: 'info',
      command: '',
      cwd: '',
      stdout: '',
      stderr: '',
      meta: [],
      shell: null,
      tool: null,
    })
    return
  }

  if (event.event === 'approval_requested') {
    if (activeSessionRuntime.value) {
      activeSessionRuntime.value.pending_approval_count += 1
      activeSessionRuntime.value.status = 'active'
    }
    upsertApprovalActivity(event.data)
    return
  }

  if (event.event === 'approval_resolved') {
    if (activeSessionRuntime.value) {
      activeSessionRuntime.value.pending_approval_count = Math.max(
        0,
        activeSessionRuntime.value.pending_approval_count - 1,
      )
    }
    resolveApprovalActivity(event.data.request_id, event.data.decision)
    return
  }

  if (event.event === 'assistant_message') {
    finalizeAssistantMessage(event.data)
    return
  }

  if (event.event === 'turn_completed') {
    if (activeSessionRuntime.value) {
      activeSessionRuntime.value.status = 'idle'
      activeSessionRuntime.value.detail = 'Turn completed.'
    }
    return
  }

  if (event.event === 'turn_aborted') {
    const abortedEvent = event as ChatTurnAbortedEvent
    isSending.value = false
    if (activeSessionRuntime.value) {
      activeSessionRuntime.value.status = abortedEvent.data.status || 'interrupted'
      activeSessionRuntime.value.detail = abortedEvent.data.reason || 'Turn interrupted.'
    }
    errorMessage.value = abortedEvent.data.reason || 'Codex turn was interrupted.'
    activities.value.push(
      makeActivity({
        id: abortedEvent.data.turn_id ? `turn:${abortedEvent.data.turn_id}:aborted` : createClientId(),
        title: 'Turn aborted',
        detail: abortedEvent.data.reason || 'Codex interrupted this turn before completion.',
        state: 'error',
        kind: 'status',
      }),
    )
    return
  }

  if (event.event === 'stream_error') {
    const streamErrorEvent = event as ChatStreamErrorEvent
    isSending.value = false
    if (activeSessionRuntime.value) {
      activeSessionRuntime.value.status = 'error'
      activeSessionRuntime.value.detail = streamErrorEvent.data.message
    }
    errorMessage.value = streamErrorEvent.data.message
    activities.value.push(
      makeActivity({
        id: streamErrorEvent.data.turn_id
          ? `turn:${streamErrorEvent.data.turn_id}:stream-error`
          : createClientId(),
        title: 'Stream error',
        detail: streamErrorEvent.data.message,
        state: 'error',
        kind: 'status',
        meta: [
          streamErrorEvent.data.code ? `code ${streamErrorEvent.data.code}` : '',
          streamErrorEvent.data.thread_id ? `thread ${streamErrorEvent.data.thread_id}` : '',
          streamErrorEvent.data.will_retry ? 'Codex will retry automatically.' : '',
        ].filter(Boolean),
      }),
    )
    return
  }

  if (event.event === 'error') {
    isSending.value = false
    if (activeSessionRuntime.value) {
      activeSessionRuntime.value.status = 'error'
      activeSessionRuntime.value.detail = event.data.message
    }
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
  isSending.value = false
  if (activeSessionRuntime.value) {
    activeSessionRuntime.value.status =
      doneEvent.data.finish_reason === 'stop' ? 'idle' : doneEvent.data.finish_reason
    if (doneEvent.data.finish_reason === 'stop') {
      activeSessionRuntime.value.detail = 'Turn completed.'
    } else if (!activeSessionRuntime.value.detail) {
      activeSessionRuntime.value.detail = `Turn finished with ${doneEvent.data.finish_reason}.`
    }
  }
  if (doneEvent.data.finish_reason === 'stop') {
    successMessage.value = 'Response ready.'
  }
  } finally {
    currentStreamSequenceHint = previousTimelineSequenceHint
  }
}

async function saveLlmSettings() {
  savingState.llm = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const normalizedLlmPayload = buildLlmSavePayload()
    config.value = await apiPut<ConfigResponse>('/api/config/llm', {
      provider: normalizedLlmPayload.provider,
      base_url: normalizedLlmPayload.base_url,
      model: normalizedLlmPayload.model,
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

async function saveAppSettings() {
  savingState.app = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    config.value = await apiPut<ConfigResponse>('/api/config/app', buildAppSettingsPayload())
    health.value = await apiGet<HealthResponse>('/api/health')
    hydrateAppForm(config.value)
    initializeNewSessionDraft(config.value)
    successMessage.value = 'Backend settings saved.'
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
  } finally {
    savingState.app = false
  }
}

async function saveCodexSandboxMode() {
  if (activeBackendId.value !== 'codex') {
    return
  }

  savingState.codexSandbox = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    config.value = await apiPut<ConfigResponse>('/api/config/app', buildAppSettingsPayload())
    health.value = await apiGet<HealthResponse>('/api/health')
    hydrateAppForm(config.value)
    successMessage.value = 'Codex permission mode saved.'
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
  } finally {
    savingState.codexSandbox = false
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
    id: createClientId(),
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
    id: createClientId(),
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

function openChannel() {
  void router.push({ name: 'channel' })
}

function openChat() {
  void router.push({ name: 'chat' })
}

async function loginWeixin() {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const payload = await apiPost<Record<string, unknown>>('/api/channel/accounts/weixin/login', {
      account_id: null,
    } satisfies ChannelLoginRequest)
    channelLoginState.qrcodeUrl = typeof payload.qrcode_url === 'string' ? payload.qrcode_url : ''
    channelLoginState.accountId = typeof payload.account_id === 'string' ? payload.account_id : ''
    channelLoginState.status = typeof payload.status === 'string' ? payload.status : 'waiting'
    await refreshDashboard()
    successMessage.value = 'Weixin login started.'
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
  }
}

async function startChannelAccount(accountId: string) {
  try {
    await apiPost<ChannelAccountActionResponse>(
      `/api/channel/accounts/weixin/${accountId}/start`,
      {},
    )
    await refreshDashboard()
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
  }
}

async function stopChannelAccount(accountId: string) {
  try {
    await apiPost<ChannelAccountActionResponse>(
      `/api/channel/accounts/weixin/${accountId}/stop`,
      {},
    )
    await refreshDashboard()
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
  }
}

function toUiMessages(messages: StoredMessage[]): UiChatMessage[] {
  return messages
    .filter(
      (message) => (message.role === 'user' || message.role === 'assistant') && message.content,
    )
    .map((message) =>
      makeUiMessage(
        message.role === 'user' ? 'user' : 'assistant',
        message.content ?? '',
        message.source,
        message.channel_meta ?? null,
        {
          sequence: message.sequence,
        },
      ),
    )
}

function makeUiMessage(
  role: 'user' | 'assistant',
  content: string,
  source: 'chat' | 'channel' = 'chat',
  channelMeta: UiChatMessage['channelMeta'] = null,
  options: { draftId?: string | null; sequence?: number | null } = {},
): UiChatMessage {
  return {
    id: createClientId(),
    role,
    content,
    sequence: reserveTimelineSequence(options.sequence),
    source,
    channelMeta,
    draftId: options.draftId ?? null,
  }
}

function appendAssistantDelta(event: ChatAssistantDeltaEvent['data']) {
  const existingDraft = chatMessages.value.find(
    (message) => message.role === 'assistant' && message.draftId === event.item_id,
  )
  if (existingDraft) {
    existingDraft.content += event.delta
    return
  }
  chatMessages.value.push(
    makeUiMessage(
      'assistant',
      event.delta,
      activeSession.value?.source ?? 'chat',
      activeSession.value?.channel_meta ?? null,
      {
        draftId: event.item_id,
      },
    ),
  )
}

function finalizeAssistantMessage(event: {
  content: string
  item_id?: string
}) {
  let preservedSequence: number | null = null
  if (event.item_id) {
    const matchedDraft = chatMessages.value.find((message) => message.draftId === event.item_id)
    preservedSequence = matchedDraft?.sequence ?? null
    chatMessages.value = chatMessages.value.filter((message) => message.draftId !== event.item_id)
  } else {
    const trailingDraftIndex = [...chatMessages.value]
      .reverse()
      .findIndex(
        (message) =>
          message.role === 'assistant' && Boolean(message.draftId) && message.content === event.content,
      )
    if (trailingDraftIndex >= 0) {
      const messageIndex = chatMessages.value.length - 1 - trailingDraftIndex
      preservedSequence = chatMessages.value[messageIndex]?.sequence ?? null
      chatMessages.value.splice(messageIndex, 1)
    }
  }
  chatMessages.value.push(
    makeUiMessage(
      'assistant',
      event.content,
      activeSession.value?.source ?? 'chat',
      activeSession.value?.channel_meta ?? null,
      {
        sequence: preservedSequence,
      },
    ),
  )
}

function normalizeTimelineSequence(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    return null
  }
  return Math.trunc(value)
}

function reserveTimelineSequence(explicit?: unknown): number {
  const sequence = normalizeTimelineSequence(explicit ?? currentStreamSequenceHint)
  if (sequence !== null) {
    nextTimelineSequence = Math.max(nextTimelineSequence, sequence + 1)
    return sequence
  }
  const nextSequence = nextTimelineSequence
  nextTimelineSequence += 1
  return nextSequence
}

function resetTimelineSequence() {
  nextTimelineSequence = 0
  currentStreamSequenceHint = null
}

function defaultApprovalResponseDraft(approval: PendingApproval) {
  const request = approval.payload.request
  if (!request || typeof request !== 'object' || Array.isArray(request)) {
    return ''
  }
  return (request as Record<string, unknown>).mode === 'form' ? '{}' : ''
}

function approvalRequestPayload(payload: Record<string, unknown>) {
  const request = payload.request
  if (!request || typeof request !== 'object' || Array.isArray(request)) {
    return null
  }
  return request as Record<string, unknown>
}

function approvalFormState(payload: Record<string, unknown>): {
  formMode: ApprovalFormMode
  formFields: ApprovalFormFieldState[]
  responseDraft: string
} {
  const request = approvalRequestPayload(payload)
  if (!request || request.mode !== 'form') {
    return {
      formMode: 'none',
      formFields: [],
      responseDraft: '',
    }
  }

  const requestedSchema = request.requestedSchema
  if (!requestedSchema || typeof requestedSchema !== 'object' || Array.isArray(requestedSchema)) {
    return {
      formMode: 'json',
      formFields: [],
      responseDraft: '{}',
    }
  }

  const schema = requestedSchema as Record<string, unknown>
  if (schema.type !== 'object') {
    return {
      formMode: 'json',
      formFields: [],
      responseDraft: '{}',
    }
  }

  const properties = schema.properties
  if (!properties || typeof properties !== 'object' || Array.isArray(properties)) {
    return {
      formMode: 'none',
      formFields: [],
      responseDraft: '',
    }
  }

  const required = new Set(
    Array.isArray(schema.required)
      ? schema.required.filter((item): item is string => typeof item === 'string')
      : [],
  )

  const formFields: ApprovalFormFieldState[] = []
  for (const [fieldId, rawSchema] of Object.entries(properties as Record<string, unknown>)) {
    if (!rawSchema || typeof rawSchema !== 'object' || Array.isArray(rawSchema)) {
      return {
        formMode: 'json',
        formFields: [],
        responseDraft: '{}',
      }
    }

    const field = approvalFieldState(fieldId, rawSchema as Record<string, unknown>, required.has(fieldId))
    if (!field) {
      return {
        formMode: 'json',
        formFields: [],
        responseDraft: '{}',
      }
    }
    formFields.push(field)
  }

  if (!formFields.length) {
    return {
      formMode: 'none',
      formFields: [],
      responseDraft: '',
    }
  }

  return {
    formMode: 'structured',
    formFields,
    responseDraft: '',
  }
}

function approvalFieldState(
  fieldId: string,
  schema: Record<string, unknown>,
  required: boolean,
): ApprovalFormFieldState | null {
  const label = typeof schema.title === 'string' && schema.title.trim() ? schema.title : fieldId
  const prompt =
    typeof schema.description === 'string' && schema.description.trim()
      ? schema.description
      : label

  if (schema.type === 'boolean') {
    return {
      id: fieldId,
      label,
      prompt,
      kind: 'boolean',
      required,
      value: typeof schema.default === 'boolean' ? schema.default : null,
    }
  }

  if (schema.type === 'number' || schema.type === 'integer') {
    return {
      id: fieldId,
      label,
      prompt,
      kind: 'number',
      required,
      value:
        typeof schema.default === 'number' && Number.isFinite(schema.default)
          ? String(schema.default)
          : '',
      min: typeof schema.minimum === 'number' ? schema.minimum : null,
      max: typeof schema.maximum === 'number' ? schema.maximum : null,
      integer: schema.type === 'integer',
    }
  }

  const legacyEnumOptions = approvalEnumOptions(schema)
  if (legacyEnumOptions) {
    return {
      id: fieldId,
      label,
      prompt,
      kind: 'select',
      required,
      value: typeof schema.default === 'string' ? schema.default : '',
      options: legacyEnumOptions,
    }
  }

  const multiSelectOptions = approvalMultiSelectOptions(schema)
  if (multiSelectOptions) {
    return {
      id: fieldId,
      label,
      prompt,
      kind: 'multiselect',
      required,
      value: Array.isArray(schema.default)
        ? schema.default.filter((item): item is string => typeof item === 'string')
        : [],
      options: multiSelectOptions,
    }
  }

  if (schema.type === 'string') {
    return {
      id: fieldId,
      label,
      prompt,
      kind: 'text',
      required,
      value: typeof schema.default === 'string' ? schema.default : '',
    }
  }

  return null
}

function approvalEnumOptions(schema: Record<string, unknown>) {
  if (Array.isArray(schema.enum) && schema.enum.every((item) => typeof item === 'string')) {
    const titles = Array.isArray(schema.enumNames) ? schema.enumNames : []
    return schema.enum.map((value, index) => ({
      value,
      label: typeof titles[index] === 'string' && titles[index].trim() ? titles[index] : value,
    }))
  }

  if (
    Array.isArray(schema.oneOf) &&
    schema.oneOf.every(
      (item) =>
        item &&
        typeof item === 'object' &&
        !Array.isArray(item) &&
        typeof (item as Record<string, unknown>).const === 'string',
    )
  ) {
    return schema.oneOf.map((item) => {
      const entry = item as Record<string, unknown>
      const value = String(entry.const)
      return {
        value,
        label: typeof entry.title === 'string' && entry.title.trim() ? entry.title : value,
      }
    })
  }

  return null
}

function approvalMultiSelectOptions(schema: Record<string, unknown>) {
  if (schema.type !== 'array' || !schema.items || typeof schema.items !== 'object' || Array.isArray(schema.items)) {
    return null
  }

  const items = schema.items as Record<string, unknown>
  if (Array.isArray(items.enum) && items.enum.every((item) => typeof item === 'string')) {
    return items.enum.map((value) => ({
      value,
      label: value,
    }))
  }

  if (
    Array.isArray(items.anyOf) &&
    items.anyOf.every(
      (item) =>
        item &&
        typeof item === 'object' &&
        !Array.isArray(item) &&
        typeof (item as Record<string, unknown>).const === 'string',
    )
  ) {
    return items.anyOf.map((item) => {
      const entry = item as Record<string, unknown>
      const value = String(entry.const)
      return {
        value,
        label: typeof entry.title === 'string' && entry.title.trim() ? entry.title : value,
      }
    })
  }

  return null
}

function approvalActivityId(requestId: string) {
  return `approval:${requestId}`
}

function upsertApprovalActivity(event: ChatApprovalRequestedEvent['data']) {
  const activityId = approvalActivityId(event.request_id)
  const formState = approvalFormState(event.payload)
  upsertActivity(
    activityId,
    makeActivity({
      id: activityId,
      title: event.title,
      detail: event.detail,
      state: 'queued',
      kind: 'approval',
      approval: {
        requestId: event.request_id,
        method: event.method,
        kind: event.kind,
        options: event.options,
        payload: event.payload,
        formMode: formState.formMode,
        formFields: formState.formFields,
        responseDraft:
          formState.formMode === 'json'
            ? formState.responseDraft
            : defaultApprovalResponseDraft({
                request_id: event.request_id,
                method: event.method,
                kind: event.kind,
                title: event.title,
                detail: event.detail,
                options: event.options,
                payload: event.payload,
              }),
        validationError: null,
        submittedDecision: null,
      },
    }),
  )
}

function appendPendingApprovalActivities(pendingApprovals: PendingApproval[] | null | undefined) {
  if (!Array.isArray(pendingApprovals)) {
    return
  }
  for (const approval of pendingApprovals) {
    upsertApprovalActivity({
      session_id: activeSessionId.value,
      request_id: approval.request_id,
      method: approval.method,
      kind: approval.kind,
      title: approval.title,
      detail: approval.detail,
      options: approval.options,
      payload: approval.payload,
    })
  }
}

function resolveApprovalActivity(requestId: string, decision: string) {
  upsertActivity(approvalActivityId(requestId), {
    id: approvalActivityId(requestId),
    kind: 'approval',
    title: 'Approval resolved',
    detail: `Decision: ${decision}.`,
    state: 'done',
    command: '',
    cwd: '',
    stdout: '',
    stderr: '',
    meta: [],
    shell: null,
    tool: null,
    approval: null,
  })
}

async function submitApprovalDecision(
  requestId: string,
  decision: ApprovalDecision,
  contentText: string,
) {
  errorMessage.value = ''
  let content: Record<string, unknown> | null | undefined = undefined
  if (contentText.trim()) {
    try {
      content = JSON.parse(contentText) as Record<string, unknown>
    } catch (error) {
      errorMessage.value = `Invalid JSON approval response: ${toErrorMessage(error)}`
      return
    }
  }
  try {
    await apiPost<{ ok: boolean }>(
      `/api/chat/sessions/${activeSessionId.value}/approvals/respond`,
      {
        request_id: requestId,
        decision,
        content,
      } satisfies ApprovalResponseRequest,
    )
    const activity = activities.value.find((item) => item.id === approvalActivityId(requestId))
    if (activity?.approval) {
      activity.approval.submittedDecision = decision
      activity.state = 'running'
      activity.detail = `Submitted ${decision}. Waiting for Codex to continue.`
    }
  } catch (error) {
    errorMessage.value = toErrorMessage(error)
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

function normalizeLlmProvider(value: unknown): LlmProvider {
  if (value === 'zai' || value === 'zai-coding-plan') {
    return value
  }
  return ''
}

function hydrateLlmForm(configPayload: ConfigResponse['llm']) {
  hydratingLlmForm = true
  llmForm.provider = normalizeLlmProvider(configPayload.provider)
  llmForm.baseUrl = configPayload.base_url || resolveProviderDefaultBaseUrl(llmForm.provider)
  llmForm.model = configPayload.model || resolveProviderDefaultModel(llmForm.provider)
  llmForm.apiKey = ''
  if (llmForm.provider === '') {
    lastCustomLlmForm.baseUrl = llmForm.baseUrl
    lastCustomLlmForm.model = llmForm.model
  }
  hydratingLlmForm = false
}

function hydrateAppForm(configPayload: ConfigResponse) {
  const sessionDefaults = {
    ...buildDefaultSessionDefaults(),
    ...(configPayload.session_defaults ?? {}),
  }
  const codex = {
    ...buildDefaultCodexConfig(),
    ...(configPayload.codex ?? {}),
  }
  const workspaceSurface = normalizeWorkspaceSurface(
    sessionDefaults.workspace_surface ?? readCachedWorkspaceSurface(),
  )

  appForm.defaultBackendId = sessionDefaults.default_backend_id
  appForm.defaultProjectPath = sessionDefaults.default_project_path
  appForm.channelBackendId = sessionDefaults.channel_backend_id
  appForm.channelProjectPath = sessionDefaults.channel_project_path
  appForm.channelAutoApproveCodexRequests = sessionDefaults.channel_auto_approve_codex_requests
  appForm.workspaceSurface = workspaceSurface
  appForm.codexLauncherCommand = codex.launcher_command
  appForm.codexModel = codex.model
  appForm.codexSandbox = codex.sandbox
  appForm.codexApprovalPolicy = codex.approval_policy
  appForm.codexApprovalsReviewer = codex.approvals_reviewer
  appForm.codexPersonality = codex.personality
  appForm.codexReasoningEffort = codex.reasoning_effort
  appForm.codexShowReasoningCards = codex.show_reasoning_cards
  appForm.codexServiceTier = codex.service_tier
}

function initializeNewSessionDraft(configPayload: ConfigResponse) {
  const workspaceSurface = normalizeWorkspaceSurface(
    configPayload.session_defaults?.workspace_surface ?? readCachedWorkspaceSurface(),
  )
  const defaultBackendId = backendIdForWorkspaceSurface(workspaceSurface)
  const defaultProjectPath =
    configPayload.session_defaults?.default_project_path ?? defaultAllowedRoots.value[0] ?? ''
  if (!activeSessionId.value) {
    newSessionDraft.backendId = defaultBackendId
  } else if (!newSessionDraft.backendId) {
    newSessionDraft.backendId = defaultBackendId
  }
  if (!newSessionDraft.projectPath.trim()) {
    newSessionDraft.projectPath = defaultProjectPath
  }
}

function resolveProviderDefaultBaseUrl(provider: LlmProvider) {
  if (!provider) {
    return ''
  }
  return LLM_PROVIDER_DEFAULTS[provider].baseUrl
}

function resolveProviderDefaultModel(provider: LlmProvider) {
  if (!provider) {
    return ''
  }
  return LLM_PROVIDER_DEFAULTS[provider].model
}

function buildLlmSavePayload() {
  const provider = llmForm.provider
  const trimmedBaseUrl = llmForm.baseUrl.trim()
  const trimmedModel = llmForm.model.trim()
  const defaultBaseUrl = resolveProviderDefaultBaseUrl(provider)

  return {
    provider,
    base_url: provider && trimmedBaseUrl === defaultBaseUrl ? '' : trimmedBaseUrl,
    model: trimmedModel,
  }
}

function buildAppSettingsPayload() {
  return {
    session_defaults: {
      default_backend_id: appForm.defaultBackendId,
      default_project_path: appForm.defaultProjectPath,
      channel_backend_id: appForm.channelBackendId,
      channel_project_path: appForm.channelProjectPath,
      channel_auto_approve_codex_requests: appForm.channelAutoApproveCodexRequests,
      workspace_surface: appForm.workspaceSurface,
    },
    codex: {
      launcher_command: appForm.codexLauncherCommand,
      model: appForm.codexModel,
      sandbox: appForm.codexSandbox,
      approval_policy: appForm.codexApprovalPolicy,
      approvals_reviewer: appForm.codexApprovalsReviewer,
      personality: appForm.codexPersonality,
      reasoning_effort: appForm.codexReasoningEffort,
      show_reasoning_cards: appForm.codexShowReasoningCards,
      service_tier: appForm.codexServiceTier,
    },
  } satisfies SaveAppSettingsRequest
}

function makeActivity(
  overrides: Partial<ChatActivity> & Pick<ChatActivity, 'title' | 'detail' | 'state' | 'kind'>,
): ChatActivity {
  return {
    id: overrides.id ?? createClientId(),
    sequence: reserveTimelineSequence(overrides.sequence),
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
    tool: overrides.tool ?? null,
    approval: overrides.approval ?? null,
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

function isBackgroundCommandListRawPayload(
  value: unknown,
): value is BackgroundCommandListRawPayload {
  return isRecord(value) && value.kind === 'background_command' && Array.isArray(value.sessions)
}

function isShellStreamRawPayload(value: unknown): value is ShellRawPayload {
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

function toToolRawPayload(value: unknown): ToolRawPayload | null {
  if (!isRecord(value) || typeof value.kind !== 'string') {
    return null
  }

  if (isShellStreamRawPayload(value) || isBackgroundCommandListRawPayload(value)) {
    return value
  }

  switch (value.kind) {
    case 'file_read':
    case 'file_list':
    case 'file_search':
    case 'file_write':
    case 'file_replace':
    case 'skill_load':
    case 'todo_list':
    case 'todo_update':
    case 'mcp_tool_result':
    case 'subagent_result':
      return value as unknown as ToolRawPayload
    default:
      return null
  }
}

function toToolDigestRawPayload(value: unknown): ToolDigestRawPayload | null {
  const raw = toToolRawPayload(value)
  if (!raw || isShellStreamRawPayload(raw)) {
    return null
  }
  return raw
}

function makeToolDigestActivity(options: {
  toolCallId: string
  toolName: string
  argumentsValue: Record<string, unknown>
  result?: string
  isError?: boolean
  metadata?: Record<string, unknown>
  raw?: ToolDigestRawPayload | null
  sequence?: number | null
}): ChatActivity {
  const metadata = options.metadata ?? {}
  const result = options.result ?? ''
  const isError = options.isError ?? false
  const raw = options.raw ?? null

  return {
    id: options.toolCallId,
    sequence: reserveTimelineSequence(options.sequence),
    kind: 'tool',
    title: toolDisplayTitle(options.toolName),
    detail: isError
      ? summarizeToolError(options.toolName, result, options.argumentsValue)
      : summarizeToolDigest(options.toolName, raw, metadata, result, options.argumentsValue),
    state: isError ? 'error' : options.result ? 'done' : 'running',
    command: '',
    cwd: '',
    stdout: '',
    stderr: '',
    meta: summarizeToolMeta(metadata, isError),
    shell: null,
    tool: makeToolActivityState({
      toolName: options.toolName,
      raw,
      metadata,
      argumentsValue: options.argumentsValue,
      result,
      isError,
    }),
  }
}

function makeToolActivityState(options: {
  toolName: string
  raw: ToolDigestRawPayload | null
  metadata: Record<string, unknown>
  argumentsValue: Record<string, unknown>
  result: string
  isError: boolean
}): ToolActivityState {
  return {
    tool_name: options.toolName,
    raw: options.raw,
    metadata: options.metadata,
    arguments: options.argumentsValue,
    result: options.result,
    is_error: options.isError,
  }
}

function toolDisplayTitle(toolName: string) {
  switch (toolName) {
    case 'file_change':
      return 'File changes'
    case 'skill_load':
      return 'Load skill'
    case 'read_file':
      return 'Read file'
    case 'list_files':
      return 'List files'
    case 'search_files':
      return 'Search files'
    case 'write_file':
      return 'Write file'
    case 'replace_in_file':
      return 'Replace in file'
    case 'todo_read':
      return 'Read todos'
    case 'todo_write':
      return 'Update todos'
    default:
      return toolName.replace(/_/g, ' ')
  }
}

function summarizeToolStart(toolName: string, argumentsValue: Record<string, unknown>) {
  switch (toolName) {
    case 'skill_load':
      return typeof argumentsValue.name === 'string'
        ? `Loading skill ${argumentsValue.name}.`
        : 'Loading skill.'
    case 'read_file':
      return typeof argumentsValue.path === 'string'
        ? `Reading ${displayNameForPath(argumentsValue.path)}.`
        : 'Reading file.'
    case 'list_files':
      return typeof argumentsValue.path === 'string'
        ? `Listing ${displayNameForPath(argumentsValue.path)}.`
        : 'Listing files.'
    case 'search_files':
      return typeof argumentsValue.pattern === 'string'
        ? `Searching for ${JSON.stringify(argumentsValue.pattern)}.`
        : 'Searching files.'
    case 'write_file':
      return typeof argumentsValue.path === 'string'
        ? `Writing ${displayNameForPath(argumentsValue.path)}.`
        : 'Writing file.'
    case 'replace_in_file':
      return typeof argumentsValue.path === 'string'
        ? `Updating ${displayNameForPath(argumentsValue.path)}.`
        : 'Updating file.'
    case 'todo_read':
      return 'Loading todos.'
    case 'todo_write':
      return 'Updating todos.'
    default:
      return `Running ${toolName}.`
  }
}

function summarizeToolDigest(
  toolName: string,
  raw: ToolDigestRawPayload | null,
  metadata: Record<string, unknown>,
  result: string,
  argumentsValue: Record<string, unknown>,
) {
  if (!raw) {
    return summarizeToolDigestFallback(toolName, result, metadata, argumentsValue)
  }

  if (isBackgroundCommandListRawPayload(raw)) {
    if (!raw.sessions.length) {
      return 'No background commands.'
    }
    const runningCount =
      typeof metadata.running_count === 'number'
        ? metadata.running_count
        : raw.sessions.filter((session) => session.state === 'running').length
    return `${raw.sessions.length} background command${raw.sessions.length === 1 ? '' : 's'}${runningCount ? `, ${runningCount} running` : ''}.`
  }

  switch (raw.kind) {
    case 'skill_load':
      return `Loaded skill ${raw.name} with ${raw.sampled_files.length} sampled file${raw.sampled_files.length === 1 ? '' : 's'}.`
    case 'file_read':
      return `Read ${displayNameForPath(raw.path)} lines ${raw.start_line}-${raw.end_line}${raw.truncated ? ' (truncated)' : ''}.`
    case 'file_list':
      return `Listed ${raw.entries.length} entr${raw.entries.length === 1 ? 'y' : 'ies'} in ${displayNameForPath(raw.path)}.`
    case 'file_search':
      return `Found ${raw.matches.length} match${raw.matches.length === 1 ? '' : 'es'} for ${JSON.stringify(raw.pattern)} in ${displayNameForPath(raw.path)}.`
    case 'file_write':
      return `${raw.append ? 'Appended' : 'Wrote'} ${raw.chars_written} chars to ${displayNameForPath(raw.path)}.`
    case 'file_replace':
      return `Updated ${displayNameForPath(raw.path)} with ${raw.replaced_count} replacement${raw.replaced_count === 1 ? '' : 's'}.`
    case 'todo_list':
    case 'todo_update':
      return `${raw.total} todo${raw.total === 1 ? '' : 's'}: ${formatTodoCounts(raw.status_counts)}.`
    case 'mcp_tool_result':
      return `${raw.tool_name} returned ${raw.contents.length} content item${raw.contents.length === 1 ? '' : 's'}.`
    case 'subagent_result':
      return `Sub-agent ${raw.sub_session_id} finished with ${raw.finish_reason}.`
    default:
      return result || summarizeToolStart(toolName, argumentsValue)
  }
}

function summarizeToolDigestFallback(
  toolName: string,
  result: string,
  metadata: Record<string, unknown>,
  argumentsValue: Record<string, unknown>,
) {
  if (toolName === 'file_change') {
    const changes = fileChangeMetadataEntries(metadata)
    const [firstChange] = changes
    if (firstChange) {
      const firstPath = displayNameForPath(firstChange.path)
      return changes.length === 1
        ? `${formatFileChangeKind(firstChange.kind)} ${firstPath}.`
        : `${formatFileChangeKind(firstChange.kind)} ${firstPath} and ${changes.length - 1} more file${changes.length === 2 ? '' : 's'}.`
    }
  }

  if (toolName === 'skill_load') {
    const skillName =
      extractSkillLoadName(result) ??
      stringMetadata(metadata.name) ??
      stringArgument(argumentsValue.name)
    const sampledFileCount = extractSkillLoadFileCount(result)
    if (skillName) {
      if (sampledFileCount !== null) {
        return `Loaded skill ${skillName} with ${sampledFileCount} sampled file${sampledFileCount === 1 ? '' : 's'}.`
      }
      return `Loaded skill ${skillName}.`
    }
  }

  if (toolName === 'read_file') {
    const filePath = extractReadFilePath(result) ?? stringArgument(argumentsValue.path)
    if (filePath) {
      return `Read ${displayNameForPath(filePath)}.`
    }
  }

  return result || summarizeToolStart(toolName, argumentsValue)
}

function summarizeToolError(
  toolName: string,
  result: string,
  argumentsValue: Record<string, unknown>,
) {
  if (!result) {
    return summarizeToolStart(toolName, argumentsValue)
  }

  const normalized = result.replace(/^Execution error:\s*/i, '')

  if (toolName === 'read_file') {
    const blockedPath = extractBlockedPath(normalized) ?? stringArgument(argumentsValue.path)
    if (normalized.startsWith('Path is outside allowed roots:')) {
      return blockedPath
        ? `Can't read ${displayNameForPath(blockedPath)} because it is outside allowed roots.`
        : 'This file is outside allowed roots.'
    }
    if (normalized.startsWith('File not found:')) {
      return blockedPath ? `File not found: ${displayNameForPath(blockedPath)}.` : 'File not found.'
    }
    if (normalized.startsWith('Path is not a file:')) {
      return blockedPath
        ? `${displayNameForPath(blockedPath)} is not a file.`
        : 'The selected path is not a file.'
    }
  }

  const fallbackPath = extractBlockedPath(normalized)
  if (normalized.startsWith('Path is outside allowed roots:') && fallbackPath) {
    return `${displayNameForPath(fallbackPath)} is outside allowed roots.`
  }

  return normalized
}

function summarizeToolMeta(metadata: Record<string, unknown>, isError: boolean) {
  if (isError) {
    return []
  }

  const notes: string[] = []
  if (metadata.truncated === true) {
    notes.push('Truncated')
  }
  return notes
}

function fileChangeMetadataEntries(metadata: Record<string, unknown>) {
  const value = metadata.changes
  if (!Array.isArray(value)) {
    return []
  }
  return value.filter((item): item is { path: string; kind: { type: string; move_path: string | null } } => {
    if (!isRecord(item) || typeof item.path !== 'string' || !isRecord(item.kind)) {
      return false
    }
    return typeof item.kind.type === 'string'
  })
}

function formatFileChangeKind(kind: { type: string; move_path: string | null }) {
  switch (kind.type) {
    case 'create':
      return 'Created'
    case 'delete':
      return 'Deleted'
    case 'move':
      return 'Moved'
    case 'update':
      return 'Updated'
    default:
      return 'Changed'
  }
}

function displayNameForPath(path: string) {
  const parts = path.split('/').filter(Boolean)
  if (!parts.length) {
    return path
  }
  return parts[parts.length - 1] ?? path
}

function stringArgument(value: unknown) {
  return typeof value === 'string' ? value : null
}

function stringMetadata(value: unknown) {
  return typeof value === 'string' ? value : null
}

function extractSkillLoadName(result: string) {
  const match = result.match(/<skill_content name="([^"]+)">/)
  return match?.[1] ?? null
}

function extractSkillLoadFileCount(result: string) {
  const matches = result.match(/<file>/g)
  return matches ? matches.length : null
}

function extractReadFilePath(result: string) {
  const match = result.match(/^File:\s+(.+)$/m)
  return match?.[1]?.trim() ?? null
}

function extractBlockedPath(result: string) {
  const match = result.match(
    /(?:Path is outside allowed roots:|File not found:|Path is not a file:)\s+(.+?)(?:\. Allowed roots:|$)/,
  )
  return match?.[1]?.trim() ?? null
}

function formatTodoCounts(counts: { pending: number; in_progress: number; completed: number }) {
  return `${counts.completed} completed, ${counts.in_progress} in progress, ${counts.pending} pending`
}

function makeShellState(options: {
  kind: ShellActivityState['kind']
  toolName: string
  toolCallId: string
  request?: Record<string, unknown>
  sessionId?: string | null
  process?: ShellProcessSnapshot | null
  events?: ShellEventEntry[]
  latestEventIndex?: number | null
}): ShellActivityState {
  return {
    kind: options.kind,
    tool_name: options.toolName,
    tool_call_id: options.toolCallId,
    session_id: options.sessionId ?? null,
    request: options.request ?? {},
    process: options.process ?? null,
    events: options.events ?? [],
    latest_event_index: options.latestEventIndex ?? null,
    streams: {
      stdout: { text: '', truncated: false },
      stderr: { text: '', truncated: false },
    },
    events_truncated: false,
    dropped_event_count: 0,
  }
}

function buildShellEvent(
  index: number,
  type: ShellEventEntry['type'],
  payload: Omit<ShellEventEntry, 'index' | 'timestamp' | 'type'> = {},
): ShellEventEntry {
  return {
    index,
    timestamp: Date.now() / 1000,
    type,
    ...payload,
  }
}

function nextShellEventIndex(shell: ShellActivityState | null) {
  return shell?.latest_event_index === null || shell?.latest_event_index === undefined
    ? 0
    : shell.latest_event_index + 1
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
    activities.value.push({
      ...nextValue,
      sequence: reserveTimelineSequence(nextValue.sequence),
    })
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
  target.tool = nextValue.tool
  target.approval = nextValue.approval ?? target.approval
}

function upsertShellActivity(activityId: string, nextValue: ChatActivity) {
  const target = activities.value.find((item) => item.id === activityId)
  if (!target) {
    activities.value.push({
      ...nextValue,
      sequence: reserveTimelineSequence(nextValue.sequence),
    })
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
  target.tool = nextValue.tool ?? target.tool
  target.approval = nextValue.approval ?? target.approval
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
      const nextIndex = nextShellEventIndex(target.shell)
      target.shell.streams.stdout = {
        ...target.shell.streams.stdout,
        text: target.stdout,
      }
      target.shell.events = mergeShellEvents(target.shell.events, [
        buildShellEvent(nextIndex, 'stdout', {
          text: content,
          stream: 'stdout',
        }),
      ])
      target.shell.latest_event_index = nextIndex
    }
    return
  }
  target.stderr += content
  if (target.shell) {
    const nextIndex = nextShellEventIndex(target.shell)
    target.shell.streams.stderr = {
      ...target.shell.streams.stderr,
      text: target.stderr,
    }
    target.shell.events = mergeShellEvents(target.shell.events, [
      buildShellEvent(nextIndex, 'stderr', {
        text: content,
        stream: 'stderr',
      }),
    ])
    target.shell.latest_event_index = nextIndex
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
    return 'Timed out.'
  }
  if (process.exit_code === 0) {
    return 'Completed successfully.'
  }
  if (process.exit_code === null) {
    return `State: ${process.state}.`
  }
  return `Failed with exit code ${process.exit_code}.`
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
    detail: isBackground ? 'Waiting for background output.' : 'Preparing shell command.',
    state: 'running',
    command: typeof argumentsValue.command === 'string' ? argumentsValue.command : '',
    cwd: typeof argumentsValue.cwd === 'string' ? argumentsValue.cwd : '',
    stdout: '',
    stderr: '',
    meta: [],
    shell: makeShellState({
      kind: isBackground ? 'background_command' : 'shell_command',
      toolName,
      toolCallId,
      request: argumentsValue,
      sessionId,
    }),
    tool: null,
  })
}

function handleShellToolEnd(
  toolCallId: string,
  toolName: string,
  raw: ShellRawPayload,
  metadata: Record<string, unknown>,
  result: string,
  isError: boolean,
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

  const meta: string[] = []
  if (metadata.truncated === true) {
    meta.push('Truncated')
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
    tool: null,
  })
}

function handleShellToolEndFallback(
  toolCallId: string,
  toolName: string,
  metadata: Record<string, unknown>,
  result: string,
  isError: boolean,
) {
  const activityId = backgroundActivityIdsByToolCallId.get(toolCallId) ?? toolCallId
  const existing = activities.value.find((item) => item.id === activityId)
  const shell = existing?.shell
  const kind =
    shell?.kind ??
    (BACKGROUND_SHELL_TOOL_NAMES.has(toolName) ? 'background_command' : 'shell_command')
  const sessionId =
    typeof metadata.session_id === 'string' ? metadata.session_id : (shell?.session_id ?? null)
  const exitCode =
    typeof metadata.exit_code === 'number'
      ? metadata.exit_code
      : (shell?.process?.exit_code ?? null)
  const timedOut = metadata.timed_out === true || shell?.process?.timed_out === true
  const state =
    typeof metadata.state === 'string'
      ? metadata.state
      : timedOut
        ? 'timed_out'
        : exitCode === 0
          ? 'completed'
          : exitCode === null
            ? (shell?.process?.state ?? 'running')
            : 'failed'

  const nextEvents: ShellEventEntry[] = []
  if (state !== 'running' && shell) {
    const stateIndex = nextShellEventIndex(shell)
    nextEvents.push(buildShellEvent(stateIndex, 'state_changed', { state }))
    nextEvents.push(
      buildShellEvent(stateIndex + 1, 'exit', {
        state,
        exit_code: exitCode ?? undefined,
        timed_out: timedOut,
      }),
    )
  }

  upsertShellActivity(activityId, {
    id: activityId,
    kind: kind === 'background_command' ? 'background' : 'command',
    title: shellTitle(kind, sessionId),
    detail: isError ? result : '',
    state: isError
      ? 'error'
      : activityStateFromShell(
          {
            session_id: sessionId,
            state,
            exit_code: exitCode,
            started_at: shell?.process?.started_at ?? 0,
            finished_at: state === 'running' ? null : Date.now() / 1000,
            runtime_seconds: shell?.process?.runtime_seconds ?? 0,
            timed_out: timedOut,
          },
          isError,
        ),
    command:
      typeof metadata.command === 'string'
        ? metadata.command
        : (existing?.command ?? shellCommandFromRequest(shell?.request ?? {}, '')),
    cwd:
      typeof metadata.cwd === 'string'
        ? metadata.cwd
        : (existing?.cwd ?? shellCwdFromRequest(shell?.request ?? {}, '')),
    stdout: existing?.stdout ?? '',
    stderr: existing?.stderr ?? '',
    meta: metadata.truncated === true ? ['Truncated'] : [],
    shell: makeShellState({
      kind,
      toolName,
      toolCallId,
      request: {
        ...(shell?.request ?? {}),
        ...(typeof metadata.command === 'string' ? { command: metadata.command } : {}),
        ...(typeof metadata.cwd === 'string' ? { cwd: metadata.cwd } : {}),
      },
      sessionId,
      process: {
        session_id: sessionId,
        state,
        exit_code: exitCode,
        started_at: shell?.process?.started_at ?? 0,
        finished_at: state === 'running' ? null : Date.now() / 1000,
        runtime_seconds: shell?.process?.runtime_seconds ?? 0,
        timed_out: timedOut,
      },
      events: nextEvents,
      latestEventIndex: nextEvents.length
        ? (nextEvents[nextEvents.length - 1]?.index ?? shell?.latest_event_index ?? null)
        : (shell?.latest_event_index ?? null),
    }),
    tool: null,
  })
}

function toEditableAllowedRoots(paths: string[]): EditableAllowedRoot[] {
  return paths.map((path) => ({
    id: createClientId(),
    path,
  }))
}

function toEditableMcpServers(payload: McpConfigResponse): EditableMcpServer[] {
  return Object.entries(payload.mcp_servers).map(([name, server]) => ({
    id: createClientId(),
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

async function safeApiGet<T>(path: string, fallback: T): Promise<T> {
  try {
    return await apiGet<T>(path)
  } catch {
    return fallback
  }
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
  <div
    class="grid h-screen grid-cols-[minmax(17rem,20rem)_minmax(0,1fr)] gap-6 overflow-hidden p-6"
    :class="
      isMobileChatPage
        ? 'max-[1023px]:h-[100dvh] max-[1023px]:grid-cols-1 max-[1023px]:gap-0 max-[1023px]:overflow-hidden max-[1023px]:p-0'
        : 'max-[1023px]:h-auto max-[1023px]:grid-cols-1 max-[1023px]:gap-4 max-[1023px]:overflow-visible max-[1023px]:p-4 max-sm:gap-3 max-sm:p-3'
    "
  >
    <aside
      v-if="!showCodexMobileChrome"
      class="flex min-h-0 flex-col gap-4 overflow-hidden max-[1023px]:order-2 max-[1023px]:gap-3 max-[1023px]:overflow-visible"
    >
      <div
        v-if="!isCodexWorkspace"
        class="relative overflow-hidden rounded-[1.6rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-[1.4rem] shadow-[var(--app-shadow)] backdrop-blur-[14px] after:pointer-events-none after:absolute after:top-[-3rem] after:right-[-3rem] after:h-40 after:w-40 after:rounded-full after:bg-[radial-gradient(circle,rgba(21,94,99,0.16),transparent_70%)] after:content-['']"
      >
        <p class="eyebrow">Local-first assistant</p>
        <h1 class="m-0 font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-[2.8rem] leading-none font-semibold">
          yier
        </h1>
        <p class="mt-3 mb-0 leading-[1.6] text-[color:var(--app-text-soft)]">
          Chat with your local agent, manage MCP connections, and keep everything anchored to your
          own machine.
        </p>
      </div>

      <div
        v-if="!isCodexWorkspace"
        class="rounded-[1.3rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
      >
        <div class="flex items-center justify-between gap-4">
          <span class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">Session</span>
          <Tag :value="sessionLabel" rounded />
        </div>
        <div class="mt-[0.85rem] flex items-center justify-between gap-4">
          <span class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">Frontend</span>
          <Tag
            :value="frontendMode"
            :severity="
              frontendMode === 'proxy' ? 'info' : frontendMode === 'static' ? 'success' : 'warn'
            "
            rounded
          />
        </div>
        <div class="mt-[0.85rem] flex items-center justify-between gap-4">
          <span class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">LLM</span>
          <Tag
            :value="llmReady ? 'Ready' : 'Needs setup'"
            :severity="llmReady ? 'success' : 'warn'"
            rounded
          />
        </div>
        <div class="mt-[0.85rem] flex items-center justify-between gap-4">
          <span class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">Backend</span>
          <Tag
            :value="activeBackendId"
            :severity="activeBackendReady ? 'success' : 'warn'"
            rounded
          />
        </div>
      </div>

      <div v-if="!isBooting && !isCodexWorkspace" class="grid gap-3">
        <select
          v-model="newSessionDraft.backendId"
          class="min-h-11 rounded-2xl border border-[color:var(--app-border)] bg-[rgba(255,252,245,0.92)] px-4 text-[color:var(--app-text)]"
        >
          <option v-for="backend in backendOptions" :key="backend.id" :value="backend.id">
            {{ backend.label }}
          </option>
        </select>
        <input
          v-model="newSessionDraft.projectPath"
          class="min-h-11 rounded-2xl border border-[color:var(--app-border)] bg-[rgba(255,252,245,0.92)] px-4 text-[color:var(--app-text)]"
          type="text"
          placeholder="Project path for the next session"
        />
        <Button label="New Chat" icon="pi pi-plus" fluid @click="handleNewChatClick" />
      </div>

      <CodexSessionExplorer
        v-if="!isBooting && isCodexWorkspace && !isCodexCompactLayout"
        :projects="activeCodexProjects"
        :active-session-id="activeSessionId"
        :active-session-status="activeSessionRuntime?.status ?? null"
        :active-project-path="activeProjectPath"
        @open-session="openCodexNativeSession"
        @start-session="handleCodexSessionStart"
      />
      <div
        v-else-if="!isBooting"
        class="flex min-h-0 flex-1 flex-col gap-[0.85rem] overflow-hidden rounded-[1.3rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
      >
        <div class="flex items-center justify-between gap-4">
          <p class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">Recent sessions</p>
          <Tag :value="String(sessionHistoryCount)" severity="secondary" rounded />
        </div>

        <ScrollPanel v-if="sessionHistory.length" class="min-h-0 flex-1">
          <div class="grid gap-[0.65rem] pr-[0.35rem]">
            <div
              v-for="session in sessionHistory"
              :key="session.session_id"
              class="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-2xl border border-[rgba(34,66,72,0.08)] bg-[rgba(255,250,242,0.72)] p-1 transition duration-150 hover:border-[rgba(21,94,99,0.18)] hover:bg-[rgba(255,250,242,0.92)] hover:-translate-y-px"
              :class="{
                'border-[rgba(21,94,99,0.28)] bg-[rgba(222,241,239,0.52)] shadow-[inset_0_0_0_1px_rgba(21,94,99,0.08)]':
                  session.session_id === activeSessionId,
              }"
            >
              <button
                type="button"
                class="rounded-[0.8rem] border-0 bg-transparent px-[0.65rem] py-[0.55rem] text-left text-inherit transition hover:bg-[rgba(21,94,99,0.06)] focus-visible:outline-2 focus-visible:outline-[rgba(21,94,99,0.38)]"
                @click="openSessionFromHistory(session.session_id)"
              >
                <div class="min-w-0">
                  <p class="m-0 font-bold leading-[1.35]">{{ session.title }}</p>
                  <p
                    v-if="session.preview"
                    class="mt-[0.18rem] mb-0 break-words text-[0.88rem] leading-[1.45] text-[color:var(--app-text-soft)]"
                  >
                    {{ session.preview }}
                  </p>
                  <p class="mt-[0.28rem] mb-0 text-[0.76rem] text-[color:var(--app-text-soft)]">
                    <span>{{ session.source }}</span>
                    <span> · {{ session.backend_id }}</span>
                    <span v-if="session.project_path"> · {{ displayNameForPath(session.project_path) }}</span>
                    <span v-if="session.channel_meta?.platform">
                      · {{ session.channel_meta.platform }}</span
                    >
                    <span v-if="session.channel_meta?.peer_id">
                      · {{ session.channel_meta.peer_id }}</span
                    >
                    <span> · </span>
                    {{ formatSessionUpdatedAt(session.updated_at) }}
                    <span v-if="session.message_count"> · {{ session.message_count }} msgs</span>
                  </p>
                </div>
              </button>
              <Button
                icon="pi pi-trash"
                class="shrink-0"
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

        <p v-else class="m-0 leading-[1.6] text-[color:var(--app-text-soft)]">No saved sessions yet.</p>
      </div>

      <div
        v-if="!isBooting && !isCodexWorkspace"
        class="grid gap-3 rounded-[1.3rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
      >
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
        <Button
          label="Channel"
          icon="pi pi-share-alt"
          fluid
          :outlined="!isChannelRoute"
          :severity="isChannelRoute ? undefined : 'secondary'"
          @click="openChannel"
        />
      </div>

      <div
        class="grid gap-3 rounded-[1.3rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
      >
        <p class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">Workspaces</p>
        <Select
          v-if="!isBooting"
          v-model="workspaceSurfaceModel"
          :options="workspaceSurfaceOptions"
          option-label="label"
          option-value="value"
          option-disabled="disabled"
          class="w-full"
          size="small"
          aria-label="Switch workspace"
        />
        <div
          v-else
          class="flex min-h-[2.65rem] items-center rounded-2xl bg-[rgba(248,243,234,0.7)] px-[0.95rem] py-[0.78rem] text-[0.93rem] text-[color:var(--app-text-soft)]"
        >
          Loading workspace…
        </div>
      </div>

      <div
        v-if="!isBooting && !isCodexWorkspace"
        class="rounded-[1.3rem] border border-[color:var(--app-border)] bg-[rgba(247,243,232,0.88)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
      >
        <p class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">Allowed roots</p>
        <ul class="mt-3 ml-4 p-0 leading-[1.5] text-[color:var(--app-text-soft)]">
          <li v-for="root in config?.allowed_roots ?? []" :key="root">{{ root }}</li>
        </ul>
      </div>
    </aside>

    <main
      class="flex min-h-0 flex-col gap-4 overflow-hidden rounded-[1.8rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-[1.15rem] shadow-[var(--app-shadow)] backdrop-blur-[14px]"
      :class="
        isMobileChatPage
          ? 'max-[1023px]:h-full max-[1023px]:min-h-0 max-[1023px]:gap-3 max-[1023px]:overflow-hidden max-[1023px]:rounded-none max-[1023px]:border-0 max-[1023px]:bg-transparent max-[1023px]:p-0 max-[1023px]:shadow-none max-[1023px]:backdrop-blur-none'
          : 'max-[1023px]:min-h-auto max-[1023px]:gap-3 max-[1023px]:overflow-visible max-[1023px]:rounded-[1.45rem] max-[1023px]:p-4 max-sm:p-3'
      "
    >
      <header
        v-if="isMobileChatPage"
        class="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-2.5 rounded-[1.15rem] border border-[rgba(34,66,72,0.08)] bg-[rgba(255,252,245,0.72)] px-2.5 py-2 shadow-[0_14px_28px_rgba(31,54,58,0.08)] backdrop-blur-[14px]"
      >
        <button
          v-if="showCodexMobileChrome"
          type="button"
          class="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-[0.95rem] border border-[rgba(34,66,72,0.08)] bg-[rgba(255,255,255,0.72)] text-[color:var(--app-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.48)] transition hover:bg-white"
          aria-label="Open project menu"
          @click="openSidebarDrawer"
        >
          <i class="pi pi-bars text-[0.98rem]"></i>
        </button>
        <div class="min-w-0 text-center">
          <p class="m-0 text-[0.68rem] font-bold uppercase tracking-[0.18em] text-[color:var(--app-text-soft)]">
            Workspace
          </p>
          <div class="mt-1 inline-flex max-w-full items-center gap-2 rounded-full bg-[rgba(21,94,99,0.08)] px-3 py-1.25 text-[color:var(--app-accent-deep)] shadow-[inset_0_1px_0_rgba(255,255,255,0.38)]">
            <span class="h-2 w-2 shrink-0 rounded-full bg-[color:var(--app-accent)]"></span>
            <span class="truncate font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-[1.05rem] font-semibold leading-none">
              Codex
            </span>
          </div>
        </div>
        <button
          type="button"
          class="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-[0.95rem] border border-[rgba(34,66,72,0.08)] bg-[rgba(255,255,255,0.72)] text-[color:var(--app-text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.48)] transition hover:bg-white"
          aria-label="Open settings"
          @click="openSettings"
        >
          <i class="pi pi-cog text-[0.98rem]"></i>
        </button>
      </header>

      <header
        v-else
        class="flex items-start justify-between gap-4 max-[1023px]:flex-col max-[1023px]:items-stretch"
      >
        <div>
          <p class="eyebrow">{{ workspaceEyebrow }}</p>
          <h2 class="m-0 font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-[clamp(1.6rem,2vw,2.4rem)] leading-[1.1] font-semibold">
            {{ workspaceTitle }}
          </h2>
        </div>
        <div class="flex items-center gap-3 max-[1023px]:flex-col max-[1023px]:items-stretch">
          <div
            v-if="isCodexWorkspace"
            class="inline-flex rounded-full border border-[color:var(--app-border)] bg-[rgba(248,243,233,0.9)] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.6)]"
          >
            <button
              type="button"
              class="min-w-[4.8rem] cursor-pointer rounded-full bg-transparent px-[0.9rem] py-[0.55rem] font-bold text-[color:var(--app-text-soft)] transition disabled:cursor-default disabled:opacity-65"
              :class="{
                'bg-[color:var(--app-accent)] text-[#f7f5ef]': activeCodexWorkMode === 'plan',
              }"
              :disabled="savingState.codexMode"
              @click="updateCodexWorkMode('plan')"
            >
              Plan
            </button>
            <button
              type="button"
              class="min-w-[4.8rem] cursor-pointer rounded-full bg-transparent px-[0.9rem] py-[0.55rem] font-bold text-[color:var(--app-text-soft)] transition disabled:cursor-default disabled:opacity-65"
              :class="{
                'bg-[color:var(--app-accent)] text-[#f7f5ef]': activeCodexWorkMode === 'build',
              }"
              :disabled="savingState.codexMode"
              @click="updateCodexWorkMode('build')"
            >
              Build
            </button>
          </div>
          <Button
            :label="isChatRoute ? 'Settings' : 'Back to Chat'"
            :icon="isChatRoute ? 'pi pi-sliders-h' : 'pi pi-comments'"
            severity="secondary"
            text
            :aria-label="isChatRoute ? 'Open settings' : 'Back to chat'"
            @click="isChatRoute ? openSettings() : openChat()"
          />
        </div>
      </header>

      <Message v-if="errorMessage" severity="error" class="m-0">{{
        errorMessage
      }}</Message>
      <Message v-else-if="successMessage" severity="success" class="m-0">
        {{ successMessage }}
      </Message>

      <section
        v-if="isBooting"
        class="grid min-h-72 place-items-center justify-items-center gap-3 rounded-3xl border border-dashed border-[color:var(--app-border-strong)] bg-[rgba(255,252,245,0.7)] p-8 text-center"
      >
        <ProgressSpinner stroke-width="4" />
        <p>Preparing your local workspace…</p>
      </section>

      <template v-else>
        <section
          v-if="!activeBackendReady && isChatRoute"
          class="grid min-h-72 place-items-center justify-items-center gap-3 rounded-3xl border border-dashed border-[color:var(--app-border-strong)] bg-[rgba(255,252,245,0.7)] p-8 text-center"
        >
          <p class="eyebrow">Setup needed</p>
          <h3 class="m-0 font-['Iowan_Old_Style','Palatino_Linotype',Palatino,serif] text-2xl font-semibold">
            {{
              activeBackendId === 'codex'
                ? 'Configure the Codex backend before sending messages.'
                : 'Configure the LLM connection before sending messages.'
            }}
          </h3>
          <p class="m-0 max-w-3xl text-[color:var(--app-text-soft)]">
            {{
              activeBackendId === 'codex'
                ? 'Set the Codex launcher command in Settings and make sure the executable is on your PATH.'
                : 'Add a provider or `base_url`, plus `api_key` and `model`, in Settings. Your values stay on this machine in `~/.yier/web/settings.json`.'
            }}
          </p>
          <Button label="Open Settings" icon="pi pi-cog" @click="openSettings" />
        </section>

        <section
          v-else
          class="flex min-h-0 flex-1 flex-col overflow-hidden"
          :class="{
            'grid grid-cols-[minmax(0,1fr)_20rem] gap-4 items-stretch':
              isCodexWorkspace && !isCodexCompactLayout,
            'max-[1023px]:min-h-0 max-[1023px]:overflow-hidden': isMobileChatPage,
            'max-[1023px]:overflow-visible': !isMobileChatPage,
          }"
        >
          <template v-if="isChatRoute">
            <div
              v-if="useFloatingChatDock"
              class="relative min-h-0 flex-1"
            >
              <div class="absolute inset-0 min-h-0">
                <ChatTimeline
                  :messages="chatMessages"
                  :activities="activities"
                  :turn-timings="codexTurnTimings"
                  :is-sending="isSending"
                  :session-label="sessionLabel"
                :session-runtime="activeSessionRuntime"
                :project-path="activeProjectPath"
                  :assistant-label="assistantLabel"
                  :bottom-inset="chatDockHeight"
                  :compact-header="isMobileChatPage"
                  :show-reasoning-cards="appForm.codexShowReasoningCards"
                  @approval-action="submitApprovalDecision"
              />
              </div>
              <div
                ref="chatDockRef"
                data-chat-dock
                class="pointer-events-none absolute inset-x-0 bottom-0 z-10 p-4 max-sm:p-3"
              >
                <div class="pointer-events-auto flex flex-col gap-3">
                  <Message
                    v-if="activeSession?.source === 'channel'"
                    severity="info"
                    class="m-0"
                  >
                    This session comes from
                    {{ activeSession.channel_meta?.platform ?? 'channel' }} and is read-only in
                    the chat workspace.
                  </Message>
                  <ChatComposer
                    v-model="composerText"
                    :disabled="!canSendToSession"
                    :is-sending="isSending"
                    :model-label="isCodexWorkspace ? appForm.codexModel : ''"
                    :reasoning-label="isCodexWorkspace ? appForm.codexReasoningEffort : ''"
                    :placeholder="composerPlaceholder"
                    :sandbox="isCodexWorkspace ? appForm.codexSandbox : null"
                    :saving-sandbox="savingState.codexSandbox"
                    :selection-start="composerSelectionStart"
                    :selection-end="composerSelectionEnd"
                    :selection-version="composerSelectionVersion"
                    @update-sandbox="appForm.codexSandbox = $event"
                    @save-sandbox="saveCodexSandboxMode"
                    @selection-change="handleComposerSelectionChange"
                    @submit="submitMessage"
                  />
                </div>
              </div>
            </div>
            <CodexWorkbar
              v-if="isCodexWorkspace && !isCodexCompactLayout"
              :runtime="activeSessionRuntime"
              :project-path="activeProjectPath"
              :paired-editors="activeCodexPairedEditors"
            />
          </template>
          <ChannelWorkspacePanel
            v-else-if="isChannelRoute"
            :workspace="channelWorkspace"
            :platforms="channelPlatforms"
            :config="channelConfig"
            :monitor-sessions="channelMonitorSessions"
            :login-state="channelLoginState"
            @login-weixin="loginWeixin"
            @start-account="startChannelAccount"
            @stop-account="stopChannelAccount"
            @open-session="openSessionFromHistory"
          />
          <SettingsPanel
            v-else
            :health="health"
            :config="config"
            :mcp-config="mcpConfig"
            :backend-options="backendOptions"
            :llm-form="llmForm"
            :app-form="appForm"
            :roots-draft="rootsDraft"
            :mcp-draft="mcpDraft"
            :saving-app="savingState.app"
            :saving-llm="savingState.llm"
            :saving-roots="savingState.roots"
            :saving-mcp="savingState.mcp"
            :reloading-mcp="savingState.reloadingMcp"
            @save-llm="saveLlmSettings"
            @save-app="saveAppSettings"
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

  <Teleport to="body">
    <Transition name="sheet-fade">
      <div
        v-if="showSidebarDrawer"
        class="sheet-overlay"
        @click="closeSidebarDrawer"
      ></div>
    </Transition>
    <Transition name="drawer-slide">
      <aside
        v-if="showSidebarDrawer"
        class="drawer-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Codex sidebar"
      >
        <div class="flex items-center justify-between gap-3 border-b border-[color:var(--app-border)] px-4 py-4">
          <div>
            <p class="eyebrow">Codex workspace</p>
            <h4>Sidebar</h4>
          </div>
          <Button
            icon="pi pi-times"
            rounded
            text
            severity="secondary"
            aria-label="Close sidebar"
            @click="closeSidebarDrawer"
          />
        </div>

        <div class="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
          <CodexSessionExplorer
            :projects="activeCodexProjects"
            :active-session-id="activeSessionId"
            :active-session-status="activeSessionRuntime?.status ?? null"
            :active-project-path="activeProjectPath"
            @open-session="openCodexNativeSession"
            @start-session="handleCodexSessionStart"
          />

          <div
            class="grid gap-3 rounded-[1.3rem] border border-[color:var(--app-border)] bg-[color:var(--app-panel)] p-4 shadow-[var(--app-shadow)] backdrop-blur-[14px]"
          >
            <p class="m-0 text-[0.92rem] text-[color:var(--app-text-soft)]">Workspaces</p>
            <Select
              v-model="workspaceSurfaceModel"
              :options="workspaceSurfaceOptions"
              option-label="label"
              option-value="value"
              option-disabled="disabled"
              class="w-full"
              size="small"
              aria-label="Switch workspace"
            />
          </div>
        </div>
      </aside>
    </Transition>
  </Teleport>

  <Teleport to="body">
    <Transition name="sheet-fade">
      <div
        v-if="showRuntimeSheet"
        class="sheet-overlay"
        @click="closeRuntimeSheet"
      ></div>
    </Transition>
    <Transition name="sheet-slide">
      <section
        v-if="showRuntimeSheet"
        class="sheet-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Codex runtime"
      >
        <CodexWorkbar
          surface="sheet"
          closeable
          :runtime="activeSessionRuntime"
          :project-path="activeProjectPath"
          :paired-editors="activeCodexPairedEditors"
          @close="closeRuntimeSheet"
        />
      </section>
    </Transition>
  </Teleport>
</template>
