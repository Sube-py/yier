import {
  computed,
  inject,
  onBeforeUnmount,
  onMounted,
  proxyRefs,
  provide,
  reactive,
  ref,
  type InjectionKey,
  watch,
} from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  ApiError,
  apiDelete,
  apiGet,
  apiPost,
  apiPostForm,
  apiPut,
  openPersistentEventStream,
  streamChat,
} from '../lib/api'
import type {
  ActivityHistory,
  ApprovalDecision,
  ApprovalFormFieldState,
  ApprovalFormMode,
  ApprovalResponseRequest,
  AttachmentUploadResponse,
  BackendId,
  BackendRuntime,
  BackgroundCommandListRawPayload,
  ChannelAccountActionResponse,
  ChannelConfigResponse,
  ChannelLoginRequest,
  ChannelPlatformsResponse,
  ChannelWorkspaceResponse,
  ChatActivity,
  ChatAssistantDeltaEvent,
  ChatStreamDoneEvent,
  ChatStreamErrorEvent,
  ChatStreamEvent,
  ChatStreamRequest,
  CodexInputItem,
  CodexTurnControlResponse,
  ComposerAttachmentState,
  ChatTurnAbortedEvent,
  CodexPairedEditorStateRequest,
  CodexGoalLoopAction,
  CodexGoalLoopActionRequest,
  CodexGoalLoopResponse,
  CodexGoalLoopState,
  CodexTurnTiming,
  CodexWorkMode,
  CodexWorkspaceResponse,
  ConfigResponse,
  CreateSessionRequest,
  DeleteSessionResponse,
  EditableAllowedRoot,
  EditableMcpServer,
  HealthResponse,
  LlmProvider,
  McpConfigResponse,
  MessageAttachment,
  OpenCodexSessionResponse,
  PendingRequest,
  SaveAppSettingsRequest,
  SessionActivityPageResponse,
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
  UpdateCodexGoalLoopRequest,
  UpdateCodexSessionModeRequest,
  WorkspaceSurface,
} from '../types/api'

const SESSION_STORAGE_KEY = 'yier.active-session-id'
const WORKSPACE_SURFACE_STORAGE_KEY = 'yier.workspace-surface'
const CODEX_ADVANCED_MODE_FAB_KEY = 'yier.codex-advanced-mode-fab'
const CODEX_COMPACT_MEDIA_QUERY = '(max-width: 1023px)'
const SESSION_ACTIVITY_PAGE_SIZE = 120
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

function normalizeWorkspaceSurface(value: string | null | undefined): WorkspaceSurface {
  if (value === 'codex' || value === 'yier' || value === 'claude') {
    return value
  }
  return 'yier'
}

function readCachedWorkspaceSurface(): WorkspaceSurface {
  return normalizeWorkspaceSurface(localStorage.getItem(WORKSPACE_SURFACE_STORAGE_KEY))
}

type CodexAdvancedMode = 'ralph-loop'
type CodexAdvancedModeFabEdge = 'left' | 'right'

function normalizeCodexAdvancedMode(value: string | null | undefined): CodexAdvancedMode {
  return value === 'ralph-loop' ? value : 'ralph-loop'
}

function clampCodexAdvancedModeFabTop(value: number) {
  return Math.min(Math.max(Math.round(value), 96), 640)
}

function readCachedCodexAdvancedModeFab(): {
  edge: CodexAdvancedModeFabEdge
  top: number
} {
  try {
    const raw = localStorage.getItem(CODEX_ADVANCED_MODE_FAB_KEY)
    if (!raw) {
      return {
        edge: 'right' as CodexAdvancedModeFabEdge,
        top: 196,
      }
    }
    const parsed = JSON.parse(raw) as {
      edge?: CodexAdvancedModeFabEdge
      top?: number
    }
    return {
      edge: parsed.edge === 'left' ? 'left' : 'right',
      top: clampCodexAdvancedModeFabTop(parsed.top ?? 196),
    }
  } catch {
    return {
      edge: 'right' as CodexAdvancedModeFabEdge,
      top: 196,
    }
  }
}

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

const CODEX_BACKGROUND_TOOL_NAMES = new Set([
  'start_codex_background_session',
  'resume_codex_background_session',
])

interface QueuedComposerFollowup {
  id: string
  message: string
  createdAt: number
}

function createWorkspaceApp() {
  const route = useRoute()
  const router = useRouter()

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
  const openingSessionId = ref('')
  const chatMessages = ref<UiChatMessage[]>([])
  const activities = ref<ChatActivity[]>([])
  const codexTurnTimings = ref<CodexTurnTiming[]>([])
  const isHydratingOlderActivity = ref(false)
  const sessionHistory = ref<SessionSummary[]>([])
  const activeCodexWorkMode = ref<CodexWorkMode>('build')
  const activeCodexGoalLoop = ref<CodexGoalLoopState | null>(null)
  const isCodexCompactLayout = ref(false)
  const isSidebarDrawerOpen = ref(false)
  const isRuntimeSheetOpen = ref(false)
  const isCodexAdvancedModeOpen = ref(false)
  const selectedCodexAdvancedMode = ref<CodexAdvancedMode>('ralph-loop')
  const cachedCodexAdvancedModeFab = readCachedCodexAdvancedModeFab()
  const codexAdvancedModeFabEdge = ref<CodexAdvancedModeFabEdge>(cachedCodexAdvancedModeFab.edge)
  const codexAdvancedModeFabTop = ref(cachedCodexAdvancedModeFab.top)
  const composerText = ref('')
  const composerAttachments = ref<ComposerAttachmentState[]>([])
  const queuedComposerFollowups = ref<QueuedComposerFollowup[]>([])
  const steerText = ref('')
  const isSteering = ref(false)
  const steeringQueuedComposerFollowupId = ref('')
  const isInterrupting = ref(false)
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
    codexGoalLoop: false,
  })
  const codexGoalLoopDraft = reactive({
    goal: '',
    definitionOfDone: '',
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
  const codexSessionHistory = computed<SessionSummary[]>(() =>
    normalizeCodexSessionSummaries(codexWorkspace.value),
  )
  const activeSession = computed(
    () => findSessionSummary(activeSessionId.value) ?? null,
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
  let isReplayingSessionActivityEvents = false
  let isFlushingQueuedComposerFollowups = false
  const backgroundActivityIdsByToolCallId = new Map<string, string>()
  const loadedTranscriptMessagesRaw = ref<StoredMessage[]>([])
  const loadedActivityEventsRaw = ref<SessionTranscriptResponse['activity_events']>([])
  const loadedPendingRequests = ref<PendingRequest[]>([])
  const activityHistoryMeta = ref<ActivityHistory | null>(null)
  let hydratingLlmForm = false

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
  const activeBackendReady = computed(() => {
    const backendHealth = health.value?.backends?.[activeBackendId.value]
    if (backendHealth) {
      return backendHealth.ready
    }
    return activeBackendId.value === 'codex'
      ? Boolean(appForm.codexLauncherCommand.trim())
      : llmReady.value
  })
  const canCompose = computed(
    () =>
      activeBackendReady.value &&
      !openingSessionId.value &&
      Boolean(activeSessionId.value),
  )
  const canSend = computed(() => canCompose.value && !isSending.value)
  const canComposeToSession = computed(
    () => canCompose.value && activeSession.value?.source !== 'channel',
  )
  const canSendToSession = computed(
    () => canSend.value && activeSession.value?.source !== 'channel',
  )
  const isSwitchingSession = computed(() => Boolean(openingSessionId.value))
  const showQueuedComposerFollowupsPanel = computed(
    () => isCodexWorkspace.value && queuedComposerFollowups.value.length > 0,
  )
  const activeProjectPath = computed(
    () => activeSession.value?.project_path ?? newSessionDraft.projectPath,
  )
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
      ? 'Workspace settings'
      : isChannelRoute.value
        ? 'Multi-platform runtime, account status, and live channel sessions'
        : isCodexWorkspace.value
          ? 'Project-aware Codex sessions with mode and permission control'
          : 'One calm surface for code, files, and config',
  )
  const workspaceSessionHistory = computed(() =>
    activeWorkspaceSurface.value === 'codex' ? codexSessionHistory.value : sessionHistory.value,
  )
  const sessionHistoryCount = computed(() => workspaceSessionHistory.value.length)
  const sidebarSessionHistory = computed(() =>
    isCodexWorkspace.value
      ? codexSessionHistory.value
      : sessionHistory.value.filter((session) => session.backend_id !== 'codex'),
  )
  const sidebarSessionHistoryCount = computed(() => sidebarSessionHistory.value.length)
  const isCodexWorkspace = computed(
    () => isChatRoute.value && activeBackendId.value === 'codex',
  )
  const activeCodexProjects = computed(() => codexWorkspace.value?.projects ?? [])
  const activeCodexPairedEditors = computed(
    () => codexWorkspace.value?.paired_editors ?? [],
  )
  const assistantLabel = computed(() =>
    activeBackendId.value === 'codex' ? 'Codex' : 'Yier',
  )
  const showMobileWorkspaceChrome = computed(
    () => isChatRoute.value && isCodexCompactLayout.value,
  )
  const showCodexMobileChrome = computed(
    () => isCodexWorkspace.value && showMobileWorkspaceChrome.value,
  )
  const isMobileChatPage = computed(
    () => showMobileWorkspaceChrome.value,
  )
  const showSidebarDrawer = computed(
    () => showMobileWorkspaceChrome.value && isSidebarDrawerOpen.value,
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
      : 'Ask yier to inspect code, read files...',
  )
  const composerPendingRequest = computed(() => loadedPendingRequests.value[0] ?? null)
  const composerUserInputRequest = computed(() =>
    loadedPendingRequests.value.find((request) => request.kind === 'user_input') ?? null,
  )
  const composerImplementPlanRequest = computed(() =>
    loadedPendingRequests.value.find((request) => request.kind === 'plan_implementation') ?? null,
  )
  const hasCodexGoalLoop = computed(
    () => isCodexWorkspace.value && activeCodexGoalLoop.value !== null,
  )
  const codexGoalLoopSummary = computed(() => {
    const goal = activeCodexGoalLoop.value?.goal?.trim() ?? ''
    if (!goal) {
      return 'Set a goal and definition of done to keep Codex iterating until it finishes.'
    }
    return goal
  })
  const codexGoalLoopShortStatus = computed(() => {
    const status = activeCodexGoalLoop.value?.status ?? 'idle'
    const labels: Record<string, string> = {
      idle: 'Idle',
      running: 'Running',
      paused: 'Paused',
      blocked: 'Blocked',
      completed: 'Done',
      failed: 'Failed',
    }
    return labels[status] ?? 'Idle'
  })
  const isCodexGoalLoopRunning = computed(
    () => activeCodexGoalLoop.value?.status === 'running',
  )
  const showCodexAdvancedModePanel = computed(
    () => isCodexWorkspace.value && !isCodexCompactLayout.value && isCodexAdvancedModeOpen.value,
  )
  const showCodexAdvancedModeSheet = computed(
    () => showCodexMobileChrome.value && isCodexAdvancedModeOpen.value,
  )
  const showCodexAdvancedModeFab = computed(() => isCodexWorkspace.value)
  const showCodexAdvancedModeOverlay = computed(
    () => showCodexAdvancedModePanel.value || showCodexAdvancedModeSheet.value,
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
    isCodexAdvancedModeOpen.value = false
  }

  function openSidebarDrawer() {
    isCodexAdvancedModeOpen.value = false
    isRuntimeSheetOpen.value = false
    isSidebarDrawerOpen.value = true
  }

  function closeSidebarDrawer() {
    isSidebarDrawerOpen.value = false
  }

  function openRuntimeSheet() {
    isCodexAdvancedModeOpen.value = false
    isSidebarDrawerOpen.value = false
    isRuntimeSheetOpen.value = true
  }

  function closeRuntimeSheet() {
    isRuntimeSheetOpen.value = false
  }

  function openCodexAdvancedMode() {
    if (!isCodexWorkspace.value) {
      return
    }
    isSidebarDrawerOpen.value = false
    isRuntimeSheetOpen.value = false
    isCodexAdvancedModeOpen.value = true
  }

  function closeCodexAdvancedMode() {
    isCodexAdvancedModeOpen.value = false
  }

  function toggleCodexAdvancedMode() {
    if (isCodexAdvancedModeOpen.value) {
      closeCodexAdvancedMode()
      return
    }
    openCodexAdvancedMode()
  }

  function selectCodexAdvancedMode(mode: CodexAdvancedMode) {
    selectedCodexAdvancedMode.value = normalizeCodexAdvancedMode(mode)
  }

  function setCodexAdvancedModeFabPosition(
    edge: CodexAdvancedModeFabEdge,
    top: number,
    options: { persist?: boolean } = {},
  ) {
    const normalizedEdge = edge === 'left' ? 'left' : 'right'
    const normalizedTop = clampCodexAdvancedModeFabTop(top)
    codexAdvancedModeFabEdge.value = normalizedEdge
    codexAdvancedModeFabTop.value = normalizedTop
    if (options.persist === false) {
      return
    }
    localStorage.setItem(
      CODEX_ADVANCED_MODE_FAB_KEY,
      JSON.stringify({
        edge: normalizedEdge,
        top: normalizedTop,
      }),
    )
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

  watch(showMobileWorkspaceChrome, (visible) => {
    if (!visible) {
      closeCodexSheets()
    }
  })

  watch(isCodexWorkspace, (value) => {
    if (!value) {
      closeCodexSheets()
    }
  })

  watch(
    () =>
      showSidebarDrawer.value ||
      showRuntimeSheet.value ||
      showCodexAdvancedModeOverlay.value,
    (locked) => {
      syncSheetScrollLock(locked)
    },
    { immediate: true },
  )

  onMounted(async () => {
    setupCodexCompactLayoutWatcher()
    window.addEventListener('keydown', handleGlobalKeydown)
    startPersistentEvents()
    await bootstrap()
  })

  onBeforeUnmount(() => {
    syncSheetScrollLock(false)
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
    const prefersCodexSurface = appForm.workspaceSurface === 'codex'
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
      prefersCodexSurface
        ? Promise.resolve({ sessions: [] } satisfies SessionListResponse)
        : apiGet<SessionListResponse>('/api/chat/sessions'),
      prefersCodexSurface
        ? safeApiGet<CodexWorkspaceResponse>('/api/codex/workspace', {
          projects: [],
          paired_editors: [],
        })
        : Promise.resolve({
          projects: [],
          paired_editors: [],
        } satisfies CodexWorkspaceResponse),
      safeApiGet<ChannelWorkspaceResponse>('/api/channel/workspace', {
        platforms: [],
        accounts: [],
      }),
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
    sessionHistory.value = normalizeChatSessionSummaries(sessionsPayload)
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

    if (workspaceSessionHistory.value.length) {
      activeSessionId.value = workspaceSessionHistory.value[0]?.session_id ?? ''
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
    closeCodexSheets()
    const payload =
      backendId === 'codex'
        ? await apiPost<{ session_id: string }>('/api/codex/sessions', {
          project_path: projectPath,
        })
        : await apiPost<{ session_id: string }>('/api/chat/sessions', {
          backend_id: backendId,
          project_path: projectPath,
        } satisfies CreateSessionRequest)
    activeSessionId.value = payload.session_id
    chatMessages.value = []
    activities.value = []
    resetLoadedTranscriptState()
    resetTimelineSequence()
    activeSessionRuntime.value = null
    activeCodexWorkMode.value = 'build'
    backgroundActivityIdsByToolCallId.clear()
    clearQueuedComposerFollowups()
    resetComposerDraft()
    await refreshSessionHistory()
    successMessage.value = 'Started a fresh session.'
    if (navigateToChat && !isChatRoute.value) {
      await router.push({ name: 'chat' })
    }
    return payload.session_id
  }

  async function startNewSession(navigateToChat = true) {
    await createSession(newSessionDraft.backendId, newSessionDraft.projectPath, navigateToChat)
  }

  function latestSessionIdForBackend(backendId: BackendId) {
    if (backendId === 'codex') {
      return codexSessionHistory.value.find((session) => session.source === 'chat')?.session_id
    }
    return sessionHistory.value.find(
      (session) => session.source === 'chat' && session.backend_id === backendId,
    )?.session_id
  }

  function startNewCodexSession(projectPath: string) {
    const nextProjectPath =
      projectPath.trim() || activeProjectPath.value || newSessionDraft.projectPath
    void createSession('codex', nextProjectPath, true)
  }

  async function refreshSessionHistory() {
    if (activeWorkspaceSurface.value === 'codex') {
      codexWorkspace.value = await safeApiGet<CodexWorkspaceResponse>('/api/codex/workspace', {
        projects: [],
        paired_editors: [],
      })
      return
    }
    sessionHistory.value = normalizeChatSessionSummaries(
      await apiGet<SessionListResponse>('/api/chat/sessions'),
    )
  }

  function nextYierChatProjectPath() {
    return (
      appForm.defaultProjectPath.trim() ||
      defaultAllowedRoots.value[0] ||
      activeSession.value?.project_path ||
      newSessionDraft.projectPath
    )
  }

  function handleNewChatClick() {
    void createSession('yier', nextYierChatProjectPath(), true)
  }

  async function runChatMessage(
    sessionId: string,
    content: string,
    options: {
      finalOnly?: boolean
      attachmentIds?: string[]
      attachments?: MessageAttachment[]
    } = {},
  ) {
    latestSessionLoadRequestId += 1
    isHydratingOlderActivity.value = false
    activities.value = activities.value.filter(
      (item) => item.kind === 'background' && item.state === 'running',
    )
    chatMessages.value.push(
      makeUiMessage('user', content.trim(), activeSession.value?.source ?? 'chat', activeSession.value?.channel_meta ?? null, {
        attachments: options.attachments ?? [],
      }),
    )

    const body: ChatStreamRequest = {
      session_id: sessionId,
      message: content || null,
    }
    if (options.attachmentIds?.length) {
      body.attachment_ids = options.attachmentIds
    }

    if (options.finalOnly) {
      let latestAssistantMessage = ''
      let assistantDeltaBuffer = ''

      await streamChat(body, (event) => {
        if (event.event === 'assistant_delta') {
          assistantDeltaBuffer += event.data.delta
          return
        }
        if (event.event === 'assistant_message') {
          latestAssistantMessage = event.data.content
          return
        }
        if (event.event === 'turn_aborted') {
          throw new Error(event.data.reason || 'Codex turn was interrupted.')
        }
        if (event.event === 'stream_error' || event.event === 'error') {
          throw new Error(event.data.message)
        }
      })

      const finalContent = latestAssistantMessage || assistantDeltaBuffer
      if (finalContent.trim()) {
        chatMessages.value.push(makeUiMessage('assistant', finalContent))
      }
      successMessage.value = 'Final answer ready.'
      await refreshSessionHistory()
      return
    }

    await streamChat(body, handleStreamEvent)
    await refreshSessionHistory()
  }

  function resetComposerDraft() {
    composerText.value = ''
    clearComposerAttachments()
    composerSelectionStart.value = 0
    composerSelectionEnd.value = 0
    composerSelectionVersion.value += 1
  }

  function clearQueuedComposerFollowups() {
    queuedComposerFollowups.value = []
    steeringQueuedComposerFollowupId.value = ''
  }

  function enqueueComposerFollowup(message: string) {
    queuedComposerFollowups.value = [
      ...queuedComposerFollowups.value,
      {
        id: createClientId(),
        message,
        createdAt: Date.now(),
      },
    ]
    resetComposerDraft()
    successMessage.value = 'Queued as the next follow-up.'
  }

  async function sendComposerMessage(
    content: string,
    options: { attachmentIds?: string[]; attachments?: MessageAttachment[] } = {},
  ) {
    isSending.value = true
    errorMessage.value = ''
    successMessage.value = ''

    try {
      if (await handleCodexSlashCommand(content)) {
        return true
      }
      resetComposerDraft()
      await runChatMessage(activeSessionId.value, content, options)
      return true
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
      return false
    } finally {
      isSending.value = false
    }
  }

  async function flushQueuedComposerFollowups() {
    if (
      isFlushingQueuedComposerFollowups ||
      isSending.value ||
      !canComposeToSession.value ||
      !activeSessionId.value
    ) {
      return
    }

    isFlushingQueuedComposerFollowups = true
    try {
      while (
        !isSending.value &&
        canComposeToSession.value &&
        Boolean(activeSessionId.value) &&
        queuedComposerFollowups.value.length
      ) {
        const [nextFollowup, ...rest] = queuedComposerFollowups.value
        if (!nextFollowup) {
          break
        }
        queuedComposerFollowups.value = rest
        const sent = await sendComposerMessage(nextFollowup.message)
        if (!sent) {
          break
        }
      }
    } finally {
      isFlushingQueuedComposerFollowups = false
    }
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
      if (target === 'codex') {
        await loadCodexWorkspaceSnapshot()
      } else {
        sessionHistory.value = normalizeChatSessionSummaries(
          await apiGet<SessionListResponse>('/api/chat/sessions'),
        )
      }
      const existingSessionId = latestSessionIdForBackend(backendId)
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

  type CodexSlashProjectRef = {
    projectIndex: number
    project: CodexWorkspaceResponse['projects'][number]
  }

  type CodexSlashSessionRef = CodexSlashProjectRef & {
    sessionIndex: number
    session: CodexWorkspaceResponse['projects'][number]['sessions'][number]
  }

  type CodexSlashParsedCommand =
    | { kind: 'project_list' }
    | { kind: 'session_list'; projectIndex: number }
    | { kind: 'open_latest'; projectIndex: number }
    | { kind: 'open_session'; projectIndex: number; sessionIndex: number; prompt: string }
    | { kind: 'new_session'; projectIndex: number; prompt: string }

  function normalizeSlashCommandInput(value: string) {
    return value.trim()
  }

  async function loadCodexWorkspaceSnapshot() {
    const payload = await safeApiGet<CodexWorkspaceResponse>('/api/codex/workspace', {
      projects: [],
      paired_editors: [],
    })
    codexWorkspace.value = payload
    return payload
  }

  function buildCodexSlashProjectRefs(workspace: CodexWorkspaceResponse): CodexSlashProjectRef[] {
    return workspace.projects.map((project, index) => ({
      projectIndex: index + 1,
      project,
    }))
  }

  function buildCodexSlashSessionRefs(workspace: CodexWorkspaceResponse): CodexSlashSessionRef[] {
    return buildCodexSlashProjectRefs(workspace).flatMap((entry) =>
      entry.project.sessions.map((session, sessionIndex) => ({
        ...entry,
        sessionIndex: sessionIndex + 1,
        session,
      })),
    )
  }

  function formatCodexProjectListing(workspace: CodexWorkspaceResponse) {
    const projects = buildCodexSlashProjectRefs(workspace)
    if (!projects.length) {
      return [
        '## Codex Projects',
        '',
        'No active Codex projects were found.',
        '',
        '### Quick Start',
        '',
        '- `/codex list` show all projects',
        '- `/codex 1 list` show sessions in project 1',
        '- `/codex 1` continue the latest session in project 1',
        '- `/codex 1 new` start a fresh session in project 1',
        '- `/codex 1 new fix the failing tests` start a fresh session and send the first prompt',
        '- `/codex 1 2 summarize the current state` continue a session and return only the final answer',
      ].join('\n')
    }

    const lines = [
      '## Codex Projects',
      '',
      '### Quick Start',
      '',
      '- `/codex list` show all projects',
      '- `/codex 1 list` show sessions in project 1',
      '- `/codex 1` continue the latest session in project 1',
      '- `/codex 1 new` start a fresh session in project 1',
      '- `/codex 1 new fix the failing tests` start a fresh session and send the first prompt',
      '- `/codex 1 2 summarize the current state` continue a session and return only the final answer',
      '',
      '### Projects',
      '',
      '| ID | Project | Sessions | Path | Sessions Command | New Session |',
      '| --- | --- | --- | --- | --- | --- |',
    ]

    for (const entry of projects) {
      lines.push(
        `| ${entry.projectIndex} | ${entry.project.project} | ${entry.project.session_count} | \`${entry.project.project_path}\` | \`/codex ${entry.projectIndex} list\` | \`/codex ${entry.projectIndex} new\` |`,
      )
    }

    return lines.join('\n')
  }

  function formatCodexProjectSessions(projectRef: CodexSlashProjectRef) {
    const lines = [
      `## Project ${projectRef.projectIndex}: ${projectRef.project.project}`,
      '',
      `- Path: \`${projectRef.project.project_path}\``,
      `- Sessions: ${projectRef.project.session_count}`,
      `- Continue latest: \`/codex ${projectRef.projectIndex}\``,
      `- Start new: \`/codex ${projectRef.projectIndex} new\``,
      `- Start new with prompt: \`/codex ${projectRef.projectIndex} new your prompt\``,
      `- Continue a session with prompt: \`/codex ${projectRef.projectIndex} 1 your prompt\``,
      '',
      '### Sessions',
    ]

    if (!projectRef.project.sessions.length) {
      lines.push('', 'No active sessions in this project yet.')
      return lines.join('\n')
    }

    lines.push('', '| Command | Session |', '| --- | --- |')
    projectRef.project.sessions.forEach((session, sessionIndex) => {
      const title = session.title.trim() || session.preview.trim() || session.thread_id
      lines.push(
        `| \`/codex ${projectRef.projectIndex} ${sessionIndex + 1}\` | ${title} |`,
      )
    })
    return lines.join('\n')
  }

  function parseCodexSlashCommand(content: string): CodexSlashParsedCommand | null {
    if (content === '/' || content === '/codex' || content === '/codex list' || content === '/codex ls') {
      return { kind: 'project_list' }
    }

    const projectListMatch = /^\/codex\s+(?<project>\d+)\s+(?:list|ls)$/i.exec(content)
    if (projectListMatch) {
      return {
        kind: 'session_list',
        projectIndex: Number.parseInt(projectListMatch.groups?.project ?? '', 10),
      }
    }

    const newSessionMatch = /^\/codex\s+(?<project>\d+)\s+new(?:\s+(?<prompt>.+))?$/i.exec(content)
    if (newSessionMatch) {
      return {
        kind: 'new_session',
        projectIndex: Number.parseInt(newSessionMatch.groups?.project ?? '', 10),
        prompt: (newSessionMatch.groups?.prompt ?? '').trim(),
      }
    }

    const openSessionMatch = /^\/codex\s+(?<project>\d+)\s+(?<session>\d+)(?:\s+(?<prompt>.+))?$/i.exec(
      content,
    )
    if (openSessionMatch) {
      return {
        kind: 'open_session',
        projectIndex: Number.parseInt(openSessionMatch.groups?.project ?? '', 10),
        sessionIndex: Number.parseInt(openSessionMatch.groups?.session ?? '', 10),
        prompt: (openSessionMatch.groups?.prompt ?? '').trim(),
      }
    }

    const latestMatch = /^\/codex\s+(?<project>\d+)$/i.exec(content)
    if (latestMatch) {
      return {
        kind: 'open_latest',
        projectIndex: Number.parseInt(latestMatch.groups?.project ?? '', 10),
      }
    }

    return null
  }

  function resolveCodexSlashProject(
    workspace: CodexWorkspaceResponse,
    projectIndex: number,
  ): CodexSlashProjectRef | null {
    return (
      buildCodexSlashProjectRefs(workspace).find((entry) => entry.projectIndex === projectIndex) ??
      null
    )
  }

  function resolveCodexSlashSession(
    workspace: CodexWorkspaceResponse,
    projectIndex: number,
    sessionIndex: number,
  ): CodexSlashSessionRef | null {
    return (
      buildCodexSlashSessionRefs(workspace).find(
        (entry) => entry.projectIndex === projectIndex && entry.sessionIndex === sessionIndex,
      ) ?? null
    )
  }

  function pushLocalAssistantMessage(content: string) {
    chatMessages.value.push(makeUiMessage('assistant', content))
  }

  async function handleCodexSlashCommand(rawContent: string) {
    const content = normalizeSlashCommandInput(rawContent)
    if (!content.startsWith('/')) {
      return false
    }

    const command = parseCodexSlashCommand(content)
    if (!command) {
      return false
    }

    errorMessage.value = ''
    successMessage.value = ''

    try {
      const workspace = await loadCodexWorkspaceSnapshot()
      if (command.kind === 'project_list') {
        composerText.value = ''
        composerSelectionStart.value = 0
        composerSelectionEnd.value = 0
        composerSelectionVersion.value += 1
        pushLocalAssistantMessage(formatCodexProjectListing(workspace))
        return true
      }

      const projectRef = resolveCodexSlashProject(workspace, command.projectIndex)
      if (!projectRef) {
        pushLocalAssistantMessage(
          `Project ${command.projectIndex} was not found. Use \`/codex list\` to refresh the project list.`,
        )
        return true
      }

      composerText.value = ''
      composerSelectionStart.value = 0
      composerSelectionEnd.value = 0
      composerSelectionVersion.value += 1

      if (command.kind === 'session_list') {
        pushLocalAssistantMessage(formatCodexProjectSessions(projectRef))
        return true
      }

      if (command.kind === 'new_session') {
        const sessionId = await createSession('codex', projectRef.project.project_path, true)
        if (command.prompt) {
          await runChatMessage(sessionId, command.prompt, { finalOnly: true })
          successMessage.value = `Started a fresh Codex session in ${projectRef.project.project} and returned the final answer.`
          return true
        }
        successMessage.value = `Started a fresh Codex session in ${projectRef.project.project}.`
        return true
      }

      if (command.kind === 'open_session') {
        if (!Number.isInteger(command.sessionIndex) || command.sessionIndex <= 0) {
          pushLocalAssistantMessage(
            `Invalid session number. Use \`/codex ${command.projectIndex} list\` to inspect this project.`,
          )
          return true
        }
        const sessionRef = resolveCodexSlashSession(workspace, command.projectIndex, command.sessionIndex)
        if (!sessionRef) {
          pushLocalAssistantMessage(
            `Session ${command.projectIndex}.${command.sessionIndex} was not found. Use \`/codex ${command.projectIndex} list\` to refresh this project.`,
          )
          return true
        }
        const opened = await openCodexNativeSession(sessionRef.session.thread_id)
        if (!opened) {
          return true
        }
        if (command.prompt) {
          await runChatMessage(sessionRef.session.thread_id, command.prompt, { finalOnly: true })
          successMessage.value = `Opened Codex session ${command.projectIndex}.${command.sessionIndex} and returned the final answer.`
          return true
        }
        successMessage.value = `Opened Codex session ${command.projectIndex}.${command.sessionIndex}.`
        return true
      }

      const latestSession = projectRef.project.sessions[0]
      if (!latestSession) {
        pushLocalAssistantMessage(
          `Project ${command.projectIndex} has no active sessions yet. Use \`/codex ${command.projectIndex} new\` to start one.`,
        )
        return true
      }

      const opened = await openCodexNativeSession(latestSession.thread_id)
      if (!opened) {
        return true
      }
      successMessage.value = `Opened the latest Codex session in ${projectRef.project.project}.`
      return true
    } catch (error) {
      errorMessage.value = toErrorMessage(error)
      return true
    }
  }

  async function submitMessage() {
    const content = composerText.value.trim()
    const readyAttachments = composerAttachments.value.filter((attachment) => attachment.status === 'ready')
    const hasUploadingAttachment = composerAttachments.value.some(
      (attachment) => attachment.status === 'uploading',
    )
    if (hasUploadingAttachment) {
      errorMessage.value = 'Please wait for attachments to finish uploading.'
      return
    }
    if ((!content && !readyAttachments.length) || !canComposeToSession.value) {
      return
    }

    if (isSending.value) {
      if (readyAttachments.length) {
        errorMessage.value = 'Attachments can only be sent with the active prompt, not queued follow-ups.'
        return
      }
      if (content.startsWith('/codex')) {
        errorMessage.value = 'Slash commands cannot be queued while a Codex turn is running.'
        return
      }
      enqueueComposerFollowup(content)
      return
    }

    await sendComposerMessage(content, {
      attachmentIds: readyAttachments.map((attachment) => attachment.id),
      attachments: readyAttachments.map(toMessageAttachment),
    })
    await flushQueuedComposerFollowups()
  }

  async function uploadComposerFiles(files: File[] | FileList) {
    const sessionId = activeSessionId.value
    if (!isCodexWorkspace.value || !sessionId || activeSession.value?.source === 'channel') {
      errorMessage.value = 'Attachments are available for interactive Codex sessions.'
      return
    }
    const fileList = Array.from(files)
    for (const file of fileList) {
      const localId = createClientId()
      const pending: ComposerAttachmentState = {
        local_id: localId,
        id: localId,
        name: file.name,
        mime_type: file.type || 'application/octet-stream',
        size: file.size,
        kind: file.type.startsWith('image/') ? 'image' : 'binary',
        preview_url: file.type.startsWith('image/') ? URL.createObjectURL(file) : null,
        input_items: [],
        status: 'uploading',
        error: null,
        file,
      }
      composerAttachments.value = [...composerAttachments.value, pending]
      await uploadComposerAttachment(sessionId, localId, file)
    }
  }

  async function uploadComposerAttachment(sessionId: string, localId: string, file: File) {
    composerAttachments.value = composerAttachments.value.map((attachment) =>
      attachment.local_id === localId
        ? {
          ...attachment,
          status: 'uploading',
          error: null,
        }
        : attachment,
    )
    const formData = new FormData()
    formData.append('file', file, file.name)
    try {
      const uploaded = await apiPostForm<AttachmentUploadResponse>(
        buildSessionAttachmentPath(sessionId),
        formData,
      )
      const existing = composerAttachments.value.find((attachment) => attachment.local_id === localId)
      revokeAttachmentPreview(existing)
      composerAttachments.value = composerAttachments.value.map((attachment) =>
        attachment.local_id === localId
          ? {
            ...uploaded,
            local_id: localId,
            status: 'ready',
            error: null,
            file: null,
          }
          : attachment,
      )
    } catch (error) {
      composerAttachments.value = composerAttachments.value.map((attachment) =>
        attachment.local_id === localId
          ? {
            ...attachment,
            status: 'error',
            error: toErrorMessage(error),
            file,
          }
          : attachment,
      )
    }
  }

  function removeComposerAttachment(localId: string) {
    const existing = composerAttachments.value.find((attachment) => attachment.local_id === localId)
    revokeAttachmentPreview(existing)
    composerAttachments.value = composerAttachments.value.filter(
      (attachment) => attachment.local_id !== localId,
    )
  }

  async function retryComposerAttachment(localId: string) {
    const sessionId = activeSessionId.value
    const attachment = composerAttachments.value.find((item) => item.local_id === localId)
    if (!sessionId || !attachment?.file || attachment.status === 'uploading') {
      return
    }
    await uploadComposerAttachment(sessionId, localId, attachment.file)
  }

  function revokeAttachmentPreview(attachment: ComposerAttachmentState | undefined) {
    if (attachment?.preview_url?.startsWith('blob:')) {
      URL.revokeObjectURL(attachment.preview_url)
    }
  }

  function clearComposerAttachments() {
    for (const attachment of composerAttachments.value) {
      revokeAttachmentPreview(attachment)
    }
    composerAttachments.value = []
  }

  async function submitCodexSteer() {
    const message = steerText.value.trim()
    if (!message || !activeSessionId.value || isSteering.value) {
      return false
    }
    isSteering.value = true
    errorMessage.value = ''
    try {
      await apiPost<CodexTurnControlResponse>(
        buildCodexTurnControlPath(activeSessionId.value, 'steer'),
        {
          message,
        },
      )
      steerText.value = ''
      successMessage.value = 'Steer instruction sent to the active Codex turn.'
      return true
    } catch (error) {
      errorMessage.value = toErrorMessage(error)
      return false
    } finally {
      isSteering.value = false
    }
  }

  async function submitQueuedComposerFollowupSteer(followupId: string) {
    const followup = queuedComposerFollowups.value.find((item) => item.id === followupId)
    if (!followup || !activeSessionId.value || isSteering.value) {
      return
    }

    steeringQueuedComposerFollowupId.value = followupId
    steerText.value = followup.message
    const steered = await submitCodexSteer()
    if (steered) {
      queuedComposerFollowups.value = queuedComposerFollowups.value.filter(
        (item) => item.id !== followupId,
      )
    }
    steeringQueuedComposerFollowupId.value = ''
  }

  function removeQueuedComposerFollowup(followupId: string) {
    queuedComposerFollowups.value = queuedComposerFollowups.value.filter(
      (item) => item.id !== followupId,
    )
    if (steeringQueuedComposerFollowupId.value === followupId) {
      steeringQueuedComposerFollowupId.value = ''
    }
  }

  async function interruptCodexTurn() {
    if (!activeSessionId.value || isInterrupting.value) {
      return
    }
    isInterrupting.value = true
    errorMessage.value = ''
    try {
      await apiPost<CodexTurnControlResponse>(
        buildCodexTurnControlPath(activeSessionId.value, 'interrupt'),
        {},
      )
      successMessage.value = 'Interrupt request sent to Codex.'
    } catch (error) {
      errorMessage.value = toErrorMessage(error)
    } finally {
      isInterrupting.value = false
    }
  }

  function normalizeActivityHistory(
    history: ActivityHistory | null | undefined,
    returnedCount: number,
  ): ActivityHistory {
    return {
      total_count: typeof history?.total_count === 'number' ? history.total_count : returnedCount,
      returned_count:
        typeof history?.returned_count === 'number' ? history.returned_count : returnedCount,
      next_before:
        typeof history?.next_before === 'number' ? history.next_before : (history?.next_before ?? null),
    }
  }

  function resetLoadedTranscriptState() {
    loadedTranscriptMessagesRaw.value = []
    loadedActivityEventsRaw.value = []
    loadedPendingRequests.value = []
    activityHistoryMeta.value = null
    isHydratingOlderActivity.value = false
  }

  function rebuildLoadedSessionTimeline() {
    resetTimelineSequence()
    chatMessages.value = toUiMessages(loadedTranscriptMessagesRaw.value)
    activities.value = []
    backgroundActivityIdsByToolCallId.clear()
    replaySessionActivityEvents(loadedActivityEventsRaw.value)
  }

  async function hydrateOlderActivityEvents(
    sessionId: string,
    requestId: number,
  ) {
    if (activityHistoryMeta.value?.next_before === null || activityHistoryMeta.value === null) {
      isHydratingOlderActivity.value = false
      return
    }

    isHydratingOlderActivity.value = true

    try {
      while (
        requestId === latestSessionLoadRequestId &&
        sessionId === activeSessionId.value &&
        activityHistoryMeta.value?.next_before !== null
      ) {
        const before = activityHistoryMeta.value?.next_before
        if (typeof before !== 'number') {
          break
        }

        const page = await apiGet<SessionActivityPageResponse>(
          buildSessionActivityEventsPath(sessionId, before),
        )
        if (requestId !== latestSessionLoadRequestId || sessionId !== activeSessionId.value) {
          return
        }

        const olderEvents = Array.isArray(page.activity_events) ? page.activity_events : []
        loadedActivityEventsRaw.value = [...olderEvents, ...loadedActivityEventsRaw.value]
        activityHistoryMeta.value = normalizeActivityHistory(
          page.activity_history,
          olderEvents.length,
        )
        rebuildLoadedSessionTimeline()
      }
    } finally {
      if (requestId === latestSessionLoadRequestId && sessionId === activeSessionId.value) {
        isHydratingOlderActivity.value = false
      }
    }
  }

  function sessionBackendId(sessionId: string): BackendId {
    return findSessionSummary(sessionId)?.backend_id ?? 'yier'
  }

  function buildSessionTranscriptPath(sessionId: string): string {
    const basePath = buildSessionBasePath(sessionId)
    return `${basePath}?activity_limit=${SESSION_ACTIVITY_PAGE_SIZE}`
  }

  function buildSessionActivityEventsPath(sessionId: string, before: number): string {
    const basePath = buildSessionBasePath(sessionId)
    return `${basePath}?before=${before}&limit=${SESSION_ACTIVITY_PAGE_SIZE}`
  }

  function buildSessionBasePath(sessionId: string): string {
    return sessionBackendId(sessionId) === 'codex'
      ? `/api/codex/sessions/${sessionId}`
      : `/api/chat/sessions/${sessionId}`
  }

  function buildSessionApprovalPath(sessionId: string): string {
    return `${buildSessionBasePath(sessionId)}/approvals/respond`
  }

  function buildSessionPendingRequestPath(sessionId: string): string {
    return buildSessionApprovalPath(sessionId)
  }

  function buildSessionAttachmentPath(sessionId: string): string {
    return `/api/chat/sessions/${encodeURIComponent(sessionId)}/attachments`
  }

  function buildCodexTurnControlPath(sessionId: string, action: 'steer' | 'interrupt'): string {
    return `/api/codex/sessions/${encodeURIComponent(sessionId)}/turns/${action}`
  }

  function buildSessionDeletePath(sessionId: string): string {
    return buildSessionBasePath(sessionId)
  }

  function buildSessionModePath(sessionId: string): string {
    return sessionBackendId(sessionId) === 'codex'
      ? `${buildSessionBasePath(sessionId)}/mode`
      : `${buildSessionBasePath(sessionId)}/codex-mode`
  }

  function buildSessionGoalLoopPath(sessionId: string): string {
    return sessionBackendId(sessionId) === 'codex'
      ? `${buildSessionBasePath(sessionId)}/goal-loop`
      : `${buildSessionBasePath(sessionId)}/codex-goal-loop`
  }

  function buildSessionGoalLoopActionPath(sessionId: string): string {
    return `${buildSessionGoalLoopPath(sessionId)}/actions`
  }

  function syncIsSendingFromRuntime(runtime: BackendRuntime | null | undefined) {
    if (runtime?.status === 'active') {
      if (!isSending.value) {
        isSending.value = true
      }
    } else if (isSending.value && !isFlushingQueuedComposerFollowups) {
      isSending.value = false
    }
  }

  async function loadSessionTranscript(sessionId: string) {
    const requestId = ++latestSessionLoadRequestId
    const transcript = await apiGet<SessionTranscriptResponse>(
      buildSessionTranscriptPath(sessionId),
    )
    if (requestId !== latestSessionLoadRequestId || sessionId !== activeSessionId.value) {
      return
    }
    loadedTranscriptMessagesRaw.value = Array.isArray(transcript.messages) ? transcript.messages : []
    loadedActivityEventsRaw.value = Array.isArray(transcript.activity_events)
      ? transcript.activity_events
      : []
    loadedPendingRequests.value = transcript.pending_requests ?? transcript.pending_approvals ?? []
    activityHistoryMeta.value = normalizeActivityHistory(
      transcript.activity_history,
      loadedActivityEventsRaw.value.length,
    )
    codexTurnTimings.value = Array.isArray(transcript.codex_turn_timings)
      ? transcript.codex_turn_timings
      : []
    activeSessionRuntime.value = transcript.backend_runtime ?? null
    syncIsSendingFromRuntime(transcript.backend_runtime)
    activeCodexWorkMode.value = transcript.codex_work_mode ?? 'build'
    activeCodexGoalLoop.value = normalizeCodexGoalLoopState(transcript.codex_goal_loop)
    hydrateCodexGoalLoopDraft(activeCodexGoalLoop.value)
    rebuildLoadedSessionTimeline()
    void hydrateOlderActivityEvents(sessionId, requestId)
  }

  function resetActiveSessionView() {
    chatMessages.value = []
    activities.value = []
    resetLoadedTranscriptState()
    codexTurnTimings.value = []
    resetTimelineSequence()
    activeSessionRuntime.value = null
    activeCodexGoalLoop.value = null
    hydrateCodexGoalLoopDraft(null)
    backgroundActivityIdsByToolCallId.clear()
    clearQueuedComposerFollowups()
  }

  async function switchToSession(sessionId: string) {
    activeSessionId.value = sessionId
    resetActiveSessionView()
    await ensureSession()
    if (!isChatRoute.value) {
      await router.push({ name: 'chat' })
    }
  }

  async function openSessionFromHistory(sessionId: string) {
    closeCodexSheets()
    if (!sessionId || sessionId === activeSessionId.value) {
      if (!isChatRoute.value) {
        await router.push({ name: 'chat' })
      }
      return
    }

    if (openingSessionId.value) {
      return
    }

    openingSessionId.value = sessionId
    try {
      await switchToSession(sessionId)
    } finally {
      if (openingSessionId.value === sessionId) {
        openingSessionId.value = ''
      }
    }
  }

  async function openCodexNativeSession(threadId: string) {
    if (!threadId) {
      return false
    }

    closeSidebarDrawer()

    if (threadId === activeSessionId.value) {
      await openSessionFromHistory(threadId)
      return true
    }

    if (openingSessionId.value) {
      return false
    }

    errorMessage.value = ''
    successMessage.value = ''
    openingSessionId.value = threadId
    try {
      const payload = await apiPost<OpenCodexSessionResponse>('/api/codex/sessions/open', {
        thread_id: threadId,
      })
      await refreshSessionHistory()
      await switchToSession(payload.session_id)
      return true
    } catch (error) {
      errorMessage.value = toErrorMessage(error)
      return false
    } finally {
      if (openingSessionId.value === threadId) {
        openingSessionId.value = ''
      }
    }
  }

  async function updateCodexWorkMode(mode: CodexWorkMode) {
    if (
      !activeSessionId.value ||
      !activeBackendReady.value ||
      activeCodexWorkMode.value === mode
    ) {
      return
    }

    savingState.codexMode = true
    errorMessage.value = ''
    successMessage.value = ''
    try {
      await apiPut<{ ok: boolean }>(
        buildSessionModePath(activeSessionId.value),
        {
          codex_work_mode: mode,
        } satisfies UpdateCodexSessionModeRequest,
      )
      activeCodexWorkMode.value = mode
      const activeSummary = findSessionSummary(activeSessionId.value)
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

  function applyCodexGoalLoopState(
    sessionId: string,
    state: CodexGoalLoopState | null,
    options: { syncDraft?: boolean } = {},
  ) {
    const syncDraft = options.syncDraft ?? false
    const normalized = normalizeCodexGoalLoopState(state)
    if (sessionId === activeSessionId.value) {
      activeCodexGoalLoop.value = normalized
      if (syncDraft) {
        hydrateCodexGoalLoopDraft(normalized)
      }
    }

    const sessionSummary = findSessionSummary(sessionId)
    if (sessionSummary) {
      sessionSummary.codex_goal_loop = normalized
    }

    const threadId =
      activeSessionRuntime.value?.thread_id && sessionId === activeSessionId.value
        ? activeSessionRuntime.value.thread_id
        : sessionId
    if (!threadId || !codexWorkspace.value) {
      return
    }
    codexWorkspace.value = {
      ...codexWorkspace.value,
      projects: codexWorkspace.value.projects.map((project) => ({
        ...project,
        sessions: project.sessions.map((session) =>
          session.thread_id === threadId
            ? {
              ...session,
              codex_goal_loop: normalized,
            }
            : session,
        ),
      })),
    }
  }

  function applyCodexGoalLoopEventState(
    sessionId: string,
    payload: {
      status: string
      goal: string
      iteration_count: number
      max_iterations: number
      last_reason: string
    },
  ) {
    const currentState =
      (sessionId === activeSessionId.value ? activeCodexGoalLoop.value : null) ??
      findSessionSummary(sessionId)?.codex_goal_loop ??
      null
    const baseState = normalizeCodexGoalLoopState(currentState) ?? {
      status: 'idle',
      goal: '',
      definition_of_done: '',
      iteration_count: 0,
      max_iterations: 8,
      consecutive_failures: 0,
      max_consecutive_failures: 2,
      last_reason: '',
      last_background_session_id: null,
      started_at: null,
      updated_at: null,
      completed_at: null,
    }
    applyCodexGoalLoopState(sessionId, {
      ...baseState,
      status: payload.status as CodexGoalLoopState['status'],
      goal: payload.goal || baseState.goal,
      iteration_count: payload.iteration_count,
      max_iterations: payload.max_iterations,
      last_reason: payload.last_reason,
      updated_at: Date.now() / 1000,
    })
  }

  async function saveCodexGoalLoop() {
    if (!activeSessionId.value || activeBackendId.value !== 'codex') {
      return
    }

    savingState.codexGoalLoop = true
    errorMessage.value = ''
    successMessage.value = ''
    try {
      const response = await apiPut<CodexGoalLoopResponse>(
        buildSessionGoalLoopPath(activeSessionId.value),
        {
          goal: codexGoalLoopDraft.goal.trim(),
          definition_of_done: codexGoalLoopDraft.definitionOfDone.trim(),
        } satisfies UpdateCodexGoalLoopRequest,
      )
      applyCodexGoalLoopState(activeSessionId.value, response.codex_goal_loop, { syncDraft: true })
      successMessage.value = 'Goal updated.'
    } catch (error) {
      errorMessage.value = toErrorMessage(error)
    } finally {
      savingState.codexGoalLoop = false
    }
  }

  async function runCodexGoalLoopAction(action: CodexGoalLoopAction) {
    if (!activeSessionId.value || activeBackendId.value !== 'codex') {
      return
    }

    savingState.codexGoalLoop = true
    errorMessage.value = ''
    successMessage.value = ''
    try {
      const response = await apiPost<CodexGoalLoopResponse>(
        buildSessionGoalLoopActionPath(activeSessionId.value),
        {
          action,
        } satisfies CodexGoalLoopActionRequest,
      )
      applyCodexGoalLoopState(activeSessionId.value, response.codex_goal_loop, {
        syncDraft: action === 'clear' || action === 'complete',
      })
      if (action === 'start' || action === 'resume') {
        activeCodexWorkMode.value = 'build'
      }
      const actionLabels: Record<CodexGoalLoopAction, string> = {
        start: 'started',
        pause: 'paused',
        resume: 'resumed',
        complete: 'completed',
        clear: 'cleared',
      }
      successMessage.value = `Goal loop ${actionLabels[action]}.`
    } catch (error) {
      errorMessage.value = toErrorMessage(error)
    } finally {
      savingState.codexGoalLoop = false
    }
  }

  async function deleteSessionFromHistory(sessionId: string) {
    deletingSessionId.value = sessionId
    errorMessage.value = ''
    successMessage.value = ''
    try {
      const response = await apiDelete<DeleteSessionResponse>(buildSessionDeletePath(sessionId))
      if (!response.deleted) {
        throw new Error('Failed to delete session.')
      }

      await refreshSessionHistory()

      if (activeSessionId.value === sessionId) {
        const nextSessionId = workspaceSessionHistory.value[0]?.session_id
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
    if (
      event.event === 'channel_inbound_message' ||
      event.event === 'channel_outbound_message'
    ) {
      return true
    }
    const eventSessionId = 'session_id' in event.data ? event.data.session_id : ''
    if (!eventSessionId) {
      return false
    }
    return eventSessionId === activeSessionId.value
  }

  function isPersistedActivityEvent(eventName: ChatStreamEvent['event']) {
    return (
      eventName === 'tool_call_start' ||
      eventName === 'tool_call_end' ||
      eventName === 'command_start' ||
      eventName === 'command_output' ||
      eventName === 'command_end' ||
      eventName === 'background_command_started' ||
      eventName === 'background_command_output' ||
      eventName === 'background_command_end' ||
      eventName === 'background_followup_queued' ||
      eventName === 'background_followup_started' ||
      eventName === 'background_followup_finished' ||
      eventName === 'goal_loop_started' ||
      eventName === 'goal_loop_iteration_started' ||
      eventName === 'goal_loop_iteration_finished' ||
      eventName === 'goal_loop_paused' ||
      eventName === 'goal_loop_blocked' ||
      eventName === 'goal_loop_completed' ||
      eventName === 'goal_loop_budget_exhausted' ||
      eventName === 'reasoning' ||
      eventName === 'plan' ||
      eventName === 'approval_requested' ||
      eventName === 'approval_resolved' ||
      eventName === 'turn_aborted' ||
      eventName === 'stream_error'
    )
  }

  function handleStreamEvent(event: ChatStreamEvent) {
    const previousTimelineSequenceHint = currentStreamSequenceHint
    currentStreamSequenceHint = normalizeTimelineSequence(
      isRecord(event.data) && 'sequence' in event.data ? event.data.sequence : null,
    )
    try {
      if (
        !isReplayingSessionActivityEvents &&
        isPersistedActivityEvent(event.event) &&
        ('session_id' in event.data ? event.data.session_id : '') === activeSessionId.value
      ) {
        const persistedEventData = isRecord(event.data) ? { ...event.data } : {}
        loadedActivityEventsRaw.value = [
          ...loadedActivityEventsRaw.value,
          {
            event: event.event,
            data: persistedEventData,
          },
        ]
      }

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

      if (
        event.event === 'channel_inbound_message' ||
        event.event === 'channel_outbound_message'
      ) {
        void refreshSessionHistory()
        return
      }

      if (event.event === 'channel_error') {
        errorMessage.value = event.data.message
        return
      }

      if (
        event.event === 'goal_loop_started' ||
        event.event === 'goal_loop_iteration_started' ||
        event.event === 'goal_loop_iteration_finished' ||
        event.event === 'goal_loop_paused' ||
        event.event === 'goal_loop_blocked' ||
        event.event === 'goal_loop_completed' ||
        event.event === 'goal_loop_budget_exhausted'
      ) {
        applyCodexGoalLoopEventState(event.data.session_id, event.data)
        const titleByEvent: Record<typeof event.event, string> = {
          goal_loop_started: 'Goal loop started',
          goal_loop_iteration_started: 'Goal loop iteration started',
          goal_loop_iteration_finished: 'Goal loop iteration finished',
          goal_loop_paused: 'Goal loop paused',
          goal_loop_blocked: 'Goal loop blocked',
          goal_loop_completed: 'Goal loop completed',
          goal_loop_budget_exhausted: 'Goal loop budget exhausted',
        }
        const stateByEvent: Record<typeof event.event, ChatActivity['state']> = {
          goal_loop_started: 'running',
          goal_loop_iteration_started: 'running',
          goal_loop_iteration_finished: 'done',
          goal_loop_paused: 'info',
          goal_loop_blocked: 'error',
          goal_loop_completed: 'done',
          goal_loop_budget_exhausted: 'error',
        }
        upsertActivity(`goal-loop:${event.event}:${event.data.iteration_count}`, {
          id: `goal-loop:${event.event}:${event.data.iteration_count}`,
          kind: 'status',
          title: titleByEvent[event.event],
          detail: event.data.last_reason || event.data.goal || 'Codex updated the goal loop state.',
          state: stateByEvent[event.event],
          command: '',
          cwd: '',
          stdout: '',
          stderr: '',
          meta: [
            event.data.goal ? `Goal: ${event.data.goal}` : '',
            `Iteration ${event.data.iteration_count}/${event.data.max_iterations}`,
          ].filter(Boolean),
          shell: null,
          tool: null,
        })
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
          handleShellToolStart(
            event.data.tool_call_id,
            event.data.tool_name,
            event.data.arguments,
          )
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
          command: normalizeShellCommand(event.data.command),
          cwd: event.data.cwd,
          stdout: '',
          stderr: '',
          meta: [],
          shell: makeShellState({
            kind: 'shell_command',
            toolName: event.data.tool_name,
            toolCallId: event.data.tool_call_id,
            request: {
              command: normalizeShellCommand(event.data.command),
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
                command: normalizeShellCommand(event.data.command),
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
          command: normalizeShellCommand(event.data.command),
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
              command: normalizeShellCommand(event.data.command),
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
        const backgroundToolName =
          typeof event.data.tool_name === 'string' ? event.data.tool_name : null
        upsertShellActivity(activityId, {
          id: activityId,
          kind: 'background',
          title: shellTitle(
            'background_command',
            event.data.background_session_id,
            { background_tool_name: backgroundToolName },
            backgroundToolName,
          ),
          detail:
            backgroundToolName && CODEX_BACKGROUND_TOOL_NAMES.has(backgroundToolName)
              ? '思考中'
              : 'Background task is running.',
          state: 'running',
          command: normalizeShellCommand(event.data.command),
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
              command: normalizeShellCommand(event.data.command),
              cwd: event.data.cwd,
              background_tool_name: backgroundToolName,
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
        const existing = activities.value.find((item) => item.id === activityId)
        const backgroundToolName = codexBackgroundToolName(
          existing?.shell?.request,
          existing?.shell?.tool_name,
        )
        upsertShellActivity(activityId, {
          id: activityId,
          kind: 'background',
          title: shellTitle(
            'background_command',
            event.data.background_session_id,
            existing?.shell?.request,
            backgroundToolName,
          ),
          detail: codexBackgroundDetail(
            {
              session_id: event.data.background_session_id,
              state: event.data.state,
              exit_code: event.data.exit_code,
              started_at: existing?.shell?.process?.started_at ?? 0,
              finished_at: Date.now() / 1000,
              runtime_seconds: existing?.shell?.process?.runtime_seconds ?? 0,
              timed_out: false,
            },
            existing?.stdout ?? '',
            backgroundToolName,
            event.data.exit_code === null
              ? `Finished with state ${event.data.state}.`
              : `Finished with state ${event.data.state} and exit code ${event.data.exit_code}.`,
          ),
          state:
            event.data.state === 'completed'
              ? 'done'
              : event.data.state === 'running'
                ? 'running'
                : 'error',
          command: normalizeShellCommand(event.data.command),
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
              command: normalizeShellCommand(event.data.command),
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
        if (!isReplayingSessionActivityEvents) {
          loadedPendingRequests.value = [
            ...loadedPendingRequests.value.filter(
              (request) => request.request_id !== event.data.request_id,
            ),
            event.data,
          ]
        }
        if (activeSessionRuntime.value) {
          activeSessionRuntime.value.pending_request_count += 1
          activeSessionRuntime.value.pending_approval_count += 1
          activeSessionRuntime.value.status = 'active'
        }
        return
      }

      if (event.event === 'approval_resolved') {
        if (!isReplayingSessionActivityEvents) {
          loadedPendingRequests.value = loadedPendingRequests.value.filter(
            (request) => request.request_id !== event.data.request_id,
          )
        }
        if (activeSessionRuntime.value) {
          activeSessionRuntime.value.pending_request_count = Math.max(
            0,
            activeSessionRuntime.value.pending_request_count - 1,
          )
          activeSessionRuntime.value.pending_approval_count = Math.max(
            0,
            activeSessionRuntime.value.pending_approval_count - 1,
          )
        }
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
            id: abortedEvent.data.turn_id
              ? `turn:${abortedEvent.data.turn_id}:aborted`
              : createClientId(),
            title: 'Turn aborted',
            detail:
              abortedEvent.data.reason || 'Codex interrupted this turn before completion.',
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
      const payload = await apiPost<Record<string, unknown>>(
        '/api/channel/accounts/weixin/login',
        {
          account_id: null,
        } satisfies ChannelLoginRequest,
      )
      channelLoginState.qrcodeUrl =
        typeof payload.qrcode_url === 'string' ? payload.qrcode_url : ''
      channelLoginState.accountId =
        typeof payload.account_id === 'string' ? payload.account_id : ''
      channelLoginState.status =
        typeof payload.status === 'string' ? payload.status : 'waiting'
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
        (message) =>
          (message.role === 'user' || message.role === 'assistant') &&
          (Boolean(message.content) || (message.attachments?.length ?? 0) > 0),
      )
      .map((message) =>
        makeUiMessage(
          message.role === 'user' ? 'user' : 'assistant',
          message.content ?? '',
          message.source,
          message.channel_meta ?? null,
          {
            sequence: message.sequence,
            attachments: message.attachments ?? [],
          },
        ),
      )
  }

  function makeUiMessage(
    role: 'user' | 'assistant',
    content: string,
    source: 'chat' | 'channel' = 'chat',
    channelMeta: UiChatMessage['channelMeta'] = null,
    options: {
      draftId?: string | null
      sequence?: number | null
      attachments?: MessageAttachment[]
    } = {},
  ): UiChatMessage {
    return {
      id: createClientId(),
      role,
      content,
      sequence: reserveTimelineSequence(options.sequence),
      source,
      channelMeta,
      draftId: options.draftId ?? null,
      attachments: normalizeMessageAttachments(options.attachments),
    }
  }

  function normalizeMessageAttachments(
    attachments: MessageAttachment[] | null | undefined,
  ): MessageAttachment[] {
    if (!Array.isArray(attachments)) {
      return []
    }
    return attachments
      .filter((attachment) => attachment && typeof attachment.name === 'string')
      .map((attachment) => ({
        id: attachment.id ?? null,
        name: attachment.name,
        mime_type: attachment.mime_type || 'application/octet-stream',
        size: typeof attachment.size === 'number' ? attachment.size : null,
        kind:
          attachment.kind === 'image' || attachment.kind === 'text' || attachment.kind === 'binary'
            ? attachment.kind
            : 'binary',
        preview_url: attachment.preview_url ?? null,
        content_url: attachment.content_url ?? null,
        path: attachment.path ?? null,
      }))
  }

  function toMessageAttachment(attachment: ComposerAttachmentState): MessageAttachment {
    return {
      id: attachment.id,
      name: attachment.name,
      mime_type: attachment.mime_type,
      size: attachment.size,
      kind: attachment.kind,
      preview_url: attachment.preview_url ?? null,
      content_url: attachment.preview_url ?? null,
      path: null,
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

  function finalizeAssistantMessage(event: { content: string; item_id?: string }) {
    let preservedSequence: number | null = null
    if (event.item_id) {
      const matchedDraft = chatMessages.value.find(
        (message) => message.draftId === event.item_id,
      )
      preservedSequence = matchedDraft?.sequence ?? null
      chatMessages.value = chatMessages.value.filter(
        (message) => message.draftId !== event.item_id,
      )
    } else {
      const trailingDraftIndex = [...chatMessages.value]
        .reverse()
        .findIndex(
          (message) =>
            message.role === 'assistant' &&
            Boolean(message.draftId) &&
            message.content === event.content,
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

  async function submitPendingRequestDecision(
    requestId: string,
    decision: ApprovalDecision,
    contentText: string,
  ) {
    errorMessage.value = ''
    const implementPlanRequest = composerImplementPlanRequest.value
    if (implementPlanRequest?.request_id === requestId) {
      let content: Record<string, unknown> | null = null
      if (contentText.trim()) {
        try {
          content = JSON.parse(contentText) as Record<string, unknown>
        } catch (error) {
          errorMessage.value = `Invalid JSON approval response: ${toErrorMessage(error)}`
          return
        }
      }

      loadedPendingRequests.value = loadedPendingRequests.value.filter(
        (request) => request.request_id !== requestId,
      )
      if (activeSessionRuntime.value) {
        activeSessionRuntime.value.pending_request_count = Math.max(
          0,
          activeSessionRuntime.value.pending_request_count - 1,
        )
        activeSessionRuntime.value.pending_approval_count = Math.max(
          0,
          activeSessionRuntime.value.pending_approval_count - 1,
        )
      }

      if (decision !== 'accept') {
        return
      }

      const planContent = typeof content?.planContent === 'string'
        ? content.planContent
        : typeof implementPlanRequest.payload.planContent === 'string'
          ? implementPlanRequest.payload.planContent
          : ''
      const followup = typeof content?.followupMessage === 'string'
        ? content.followupMessage.trim()
        : ''
      const message = followup || `PLEASE IMPLEMENT THIS PLAN:\n${planContent}`.trim()
      if (!message) {
        return
      }
      if (activeCodexWorkMode.value === 'plan') {
        await updateCodexWorkMode('build')
      }
      await sendComposerMessage(message)
      return
    }

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
        buildSessionPendingRequestPath(activeSessionId.value),
        {
          request_id: requestId,
          decision,
          content,
        } satisfies ApprovalResponseRequest,
      )
      loadedPendingRequests.value = loadedPendingRequests.value.filter(
        (request) => request.request_id !== requestId,
      )
      if (activeSessionRuntime.value) {
        activeSessionRuntime.value.pending_request_count = Math.max(
          0,
          activeSessionRuntime.value.pending_request_count - 1,
        )
        activeSessionRuntime.value.pending_approval_count = Math.max(
          0,
          activeSessionRuntime.value.pending_approval_count - 1,
        )
      }
    } catch (error) {
      errorMessage.value = toErrorMessage(error)
    }
  }

  function replaySessionActivityEvents(
    activityEvents: SessionTranscriptResponse['activity_events'],
  ) {
    isReplayingSessionActivityEvents = true
    try {
      for (const event of activityEvents) {
        handleStreamEvent(event as ChatStreamEvent)
      }
    } finally {
      isReplayingSessionActivityEvents = false
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

  function normalizeChatSessionSummaries(
    payload: Partial<SessionListResponse> | null | undefined,
  ): SessionSummary[] {
    return normalizeSessionSummaries(payload).filter((session) => session.backend_id !== 'codex')
  }

  function normalizeCodexSessionSummaries(
    workspace: CodexWorkspaceResponse | null | undefined,
  ): SessionSummary[] {
    const sessions = (workspace?.projects ?? []).flatMap((project) =>
      project.sessions.map((session) => ({
        session_id: session.thread_id,
        title: session.title,
        preview: session.preview,
        updated_at: session.updated_at,
        message_count: 0,
        source: 'chat' as const,
        backend_id: 'codex' as const,
        project_path: session.project_path || session.cwd,
        channel_meta: null,
        codex_work_mode: 'build' as const,
        codex_goal_loop: normalizeCodexGoalLoopState(session.codex_goal_loop),
      })),
    )
    return sessions.sort(
      (left, right) =>
        right.updated_at - left.updated_at ||
        right.title.localeCompare(left.title),
    )
  }

  function findSessionSummary(sessionId: string): SessionSummary | null {
    if (!sessionId) {
      return null
    }
    return (
      sessionHistory.value.find((session) => session.session_id === sessionId) ??
      codexSessionHistory.value.find((session) => session.session_id === sessionId) ??
      null
    )
  }

  function normalizeCodexGoalLoopState(
    value: CodexGoalLoopState | null | undefined,
  ): CodexGoalLoopState | null {
    if (!value) {
      return null
    }
    return {
      status: value.status ?? 'idle',
      goal: value.goal ?? '',
      definition_of_done: value.definition_of_done ?? '',
      iteration_count: value.iteration_count ?? 0,
      max_iterations: value.max_iterations ?? 8,
      consecutive_failures: value.consecutive_failures ?? 0,
      max_consecutive_failures: value.max_consecutive_failures ?? 2,
      last_reason: value.last_reason ?? '',
      last_background_session_id: value.last_background_session_id ?? null,
      started_at: value.started_at ?? null,
      updated_at: value.updated_at ?? null,
      completed_at: value.completed_at ?? null,
    }
  }

  function hydrateCodexGoalLoopDraft(state: CodexGoalLoopState | null) {
    codexGoalLoopDraft.goal = state?.goal ?? ''
    codexGoalLoopDraft.definitionOfDone = state?.definition_of_done ?? ''
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
      configPayload.session_defaults?.workspace_surface ?? readCachedWorkspaceSurface(),
    )

    appForm.defaultBackendId = sessionDefaults.default_backend_id
    appForm.defaultProjectPath = sessionDefaults.default_project_path
    appForm.channelBackendId = sessionDefaults.channel_backend_id
    appForm.channelProjectPath = sessionDefaults.channel_project_path
    appForm.channelAutoApproveCodexRequests =
      sessionDefaults.channel_auto_approve_codex_requests
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
      media: overrides.media ?? null,
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

    if (
      BACKGROUND_SHELL_TOOL_NAMES.has(toolName) &&
      typeof argumentsValue.session_id === 'string'
    ) {
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
        : summarizeToolDigest(
          options.toolName,
          raw,
          metadata,
          result,
          options.argumentsValue,
        ),
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
      media: mediaPreviewFromMetadata(options.toolName, metadata),
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
      case 'image_generation':
        return 'Generate image'
      case 'image_view':
        return 'View image'
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
    if (toolName === 'todo_write') {
      const counts = raw?.kind === 'todo_update' ? raw.status_counts : null
      if (counts) {
        return `Updated todos: ${formatTodoCounts(counts)}.`
      }
    }

    if (toolName === 'todo_read') {
      const counts = raw?.kind === 'todo_list' ? raw.status_counts : null
      if (counts) {
        return `Loaded todos: ${formatTodoCounts(counts)}.`
      }
    }

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

    if (toolName === 'image_generation') {
      const label = stringMetadata(metadata.preview_name) ?? stringMetadata(metadata.saved_path)
      return label ? `Generated ${displayNameForPath(label)}.` : (result || 'Generated an image.')
    }

    if (toolName === 'image_view') {
      const path = stringMetadata(metadata.path)
      return path ? `Viewed ${displayNameForPath(path)}.` : (result || 'Viewed an image.')
    }

    if (raw?.kind === 'file_read') {
      return `Read ${displayNameForPath(raw.path)} lines ${raw.start_line}-${raw.end_line}.`
    }
    if (raw?.kind === 'file_list') {
      return `Listed ${displayNameForPath(raw.path)}.`
    }
    if (raw?.kind === 'file_search') {
      const matchCount = raw.matches.length
      return `Found ${matchCount} match${matchCount === 1 ? '' : 'es'} for ${JSON.stringify(raw.pattern)} in ${displayNameForPath(raw.path)}.`
    }
    if (raw?.kind === 'file_write') {
      return `Wrote ${displayNameForPath(raw.path)}.`
    }
    if (raw?.kind === 'file_replace') {
      const replacementCount = raw.replaced_count
      return `Updated ${displayNameForPath(raw.path)} with ${replacementCount} replacement${replacementCount === 1 ? '' : 's'}.`
    }
    if (raw?.kind === 'skill_load') {
      return `Loaded skill ${raw.name} with ${raw.sampled_files.length} sampled file${raw.sampled_files.length === 1 ? '' : 's'}.`
    }

    return summarizeToolDigestFallback(toolName, result, metadata, argumentsValue)
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
        return blockedPath
          ? `File not found: ${displayNameForPath(blockedPath)}.`
          : 'File not found.'
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

  function mediaPreviewFromMetadata(
    toolName: string,
    metadata: Record<string, unknown>,
  ): ChatActivity['media'] {
    if (toolName !== 'image_generation' && toolName !== 'image_view') {
      return null
    }
    const url = stringMetadata(metadata.preview_url)
    const path = stringMetadata(metadata.path) ?? stringMetadata(metadata.saved_path)
    const label = stringMetadata(metadata.preview_name)
    const mimeType = stringMetadata(metadata.mime_type)
    const size = numberMetadata(metadata.size)
    if (!url && !path) {
      return null
    }
    return {
      kind: 'image',
      url,
      label,
      path,
      mime_type: mimeType,
      size,
    }
  }

  function fileChangeMetadataEntries(metadata: Record<string, unknown>) {
    const value = metadata.changes
    if (!Array.isArray(value)) {
      return []
    }
    return value.filter(
      (
        item,
      ): item is { path: string; kind: { type: string; move_path: string | null } } => {
        if (!isRecord(item) || typeof item.path !== 'string' || !isRecord(item.kind)) {
          return false
        }
        return typeof item.kind.type === 'string'
      },
    )
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

  function numberMetadata(value: unknown) {
    return typeof value === 'number' && Number.isFinite(value) ? value : null
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

  function formatTodoCounts(counts: {
    pending: number
    in_progress: number
    completed: number
  }) {
    return `${counts.completed} completed, ${counts.in_progress} in progress, ${counts.pending} pending`
  }

  const SHELL_WRAPPER_NAMES = new Set(['sh', 'bash', 'zsh', 'dash', 'ksh'])

  function tokenizeShellWords(command: string) {
    const words: string[] = []
    let current = ''
    let quote: "'" | '"' | null = null

    for (let index = 0; index < command.length; index += 1) {
      const character = command[index]
      if (character === undefined) {
        continue
      }

      if (quote === "'") {
        if (character === "'") {
          quote = null
        } else {
          current += character
        }
        continue
      }

      if (quote === '"') {
        if (character === '"') {
          quote = null
          continue
        }
        if (character === '\\') {
          const nextCharacter = command[index + 1]
          if (nextCharacter !== undefined) {
            current += nextCharacter
            index += 1
            continue
          }
        }
        current += character
        continue
      }

      if (/\s/.test(character)) {
        if (current) {
          words.push(current)
          current = ''
        }
        continue
      }

      if (character === "'" || character === '"') {
        quote = character
        continue
      }

      if (character === '\\') {
        const nextCharacter = command[index + 1]
        if (nextCharacter !== undefined) {
          current += nextCharacter
          index += 1
          continue
        }
      }

      current += character
    }

    if (quote) {
      return null
    }

    if (current) {
      words.push(current)
    }

    return words
  }

  function unwrapShellWrapper(command: string) {
    const trimmed = command.trim()
    if (!trimmed) {
      return trimmed
    }

    const words = tokenizeShellWords(trimmed)
    if (!words || words.length < 3) {
      return trimmed
    }

    const shellToken = words[0] ?? ''
    const shellName = shellToken.split('/').filter(Boolean).pop() ?? shellToken
    if (!SHELL_WRAPPER_NAMES.has(shellName)) {
      return trimmed
    }

    let commandIndex = 1
    let hasCommandFlag = false
    while (commandIndex < words.length) {
      const token = words[commandIndex] ?? ''
      if (token === '--') {
        commandIndex += 1
        break
      }
      if (!token.startsWith('-') || token === '-') {
        break
      }
      if (/^-[A-Za-z]+$/.test(token) && token.includes('c')) {
        hasCommandFlag = true
      }
      commandIndex += 1
    }

    if (!hasCommandFlag || commandIndex >= words.length) {
      return trimmed
    }

    return words[commandIndex] ?? trimmed
  }

  function normalizeShellCommand(value: unknown, fallback = '') {
    if (typeof value !== 'string') {
      return fallback
    }
    return unwrapShellWrapper(value)
  }

  function normalizeShellRequest(request: Record<string, unknown> | undefined) {
    if (!request) {
      return {}
    }
    if (typeof request.command !== 'string') {
      return request
    }
    return {
      ...request,
      command: normalizeShellCommand(request.command),
    }
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
      request: normalizeShellRequest(options.request),
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
      request: normalizeShellRequest(raw.request),
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
      dropped_event_count: Math.max(
        current.dropped_event_count,
        incoming.dropped_event_count,
      ),
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
    target.media = nextValue.media ?? target.media
    target.planImplementation = nextValue.planImplementation ?? target.planImplementation
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
    target.media = nextValue.media ?? target.media
    target.planImplementation = nextValue.planImplementation ?? target.planImplementation
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
    if (
      process.state === 'completed' &&
      !process.timed_out &&
      process.exit_code === 0 &&
      !isError
    ) {
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

  function codexBackgroundToolName(
    request: Record<string, unknown> | undefined,
    fallbackToolName?: string | null,
  ) {
    const requestToolName =
      typeof request?.background_tool_name === 'string' ? request.background_tool_name : null
    if (requestToolName && CODEX_BACKGROUND_TOOL_NAMES.has(requestToolName)) {
      return requestToolName
    }
    if (fallbackToolName && CODEX_BACKGROUND_TOOL_NAMES.has(fallbackToolName)) {
      return fallbackToolName
    }
    return null
  }

  function parseJsonLines(text: string) {
    const items: Record<string, unknown>[] = []
    for (const line of text.split('\n')) {
      const trimmed = line.trim()
      if (!trimmed) {
        continue
      }
      try {
        const parsed = JSON.parse(trimmed)
        if (isRecord(parsed)) {
          items.push(parsed)
        }
      } catch {
        continue
      }
    }
    return items
  }

  function latestCodexBackgroundResult(stdoutText: string) {
    const events = parseJsonLines(stdoutText)
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index]
      if (!event || event.event !== 'codex_background_result' || !isRecord(event.result)) {
        continue
      }
      return event
    }
    return null
  }

  function codexBackgroundTitle(
    sessionId: string | null,
    backgroundToolName: string | null,
  ) {
    const action = backgroundToolName === 'resume_codex_background_session' ? 'Resume' : 'Start'
    return sessionId ? `${action} ${sessionId}` : action
  }

  function codexBackgroundDetail(
    process: ShellProcessSnapshot | null,
    stdoutText: string,
    backgroundToolName: string | null,
    fallback: string,
  ) {
    if (!backgroundToolName) {
      return fallback
    }

    if (!process || process.state === 'running' || process.state === 'stopping') {
      return '思考中'
    }

    const resultEvent = latestCodexBackgroundResult(stdoutText)
    const resultPayload = resultEvent?.result
    const latestAssistantMessage =
      isRecord(resultPayload) && typeof resultPayload.latest_assistant_message === 'string'
        ? resultPayload.latest_assistant_message.trim()
        : ''

    if (resultEvent?.ok === false || process.exit_code !== 0 || process.state === 'failed') {
      return '失败了'
    }

    if (latestAssistantMessage) {
      return latestAssistantMessage
    }

    if (process.exit_code === 0 || process.state === 'completed') {
      return '已完成'
    }

    return fallback
  }

  function shellTitle(
    kind: ShellActivityState['kind'],
    sessionId: string | null,
    request?: Record<string, unknown>,
    fallbackToolName?: string | null,
  ) {
    const backgroundToolName = codexBackgroundToolName(request, fallbackToolName)
    if (kind === 'background_command' && backgroundToolName) {
      return codexBackgroundTitle(sessionId, backgroundToolName)
    }
    if (kind === 'background_command') {
      return sessionId ? `Background ${sessionId}` : 'Background command'
    }
    return 'Shell command'
  }

  function shellCommandFromRequest(request: Record<string, unknown>, fallback: string) {
    return normalizeShellCommand(request.command, fallback)
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
    const sessionId =
      typeof argumentsValue.session_id === 'string' ? argumentsValue.session_id : null
    const existing =
      sessionId && isBackground
        ? activities.value.find((item) => item.id === getBackgroundActivityId(sessionId))
        : null
    const backgroundToolName = codexBackgroundToolName(
      existing?.shell?.request,
      existing?.shell?.tool_name,
    )
    upsertShellActivity(activityId, {
      id: activityId,
      kind: isBackground ? 'background' : 'command',
      title: shellTitle(
        isBackground ? 'background_command' : 'shell_command',
        sessionId,
        existing?.shell?.request,
        backgroundToolName,
      ),
      detail:
        isBackground && backgroundToolName
          ? codexBackgroundDetail(
            existing?.shell?.process ?? null,
            existing?.stdout ?? '',
            backgroundToolName,
            '思考中',
          )
          : isBackground
            ? 'Waiting for background output.'
            : 'Preparing shell command.',
      state: 'running',
      command: normalizeShellCommand(argumentsValue.command),
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

    const existing = activities.value.find((item) => item.id === activityId)
    const mergedRequest = {
      ...(existing?.shell?.request ?? {}),
      ...raw.request,
    }
    const backgroundToolName = codexBackgroundToolName(
      mergedRequest,
      existing?.shell?.tool_name,
    )
    const stdoutText = raw.streams.stdout.text || existing?.stdout || ''

    const meta: string[] = []
    if (metadata.truncated === true) {
      meta.push('Truncated')
    }

    upsertShellActivity(activityId, {
      id: activityId,
      kind: raw.kind === 'background_command' ? 'background' : 'command',
      title: shellTitle(raw.kind, raw.process.session_id, mergedRequest, backgroundToolName),
      detail: codexBackgroundDetail(
        shell.process,
        stdoutText,
        backgroundToolName,
        shellDetailFromProcess(shell.process, result),
      ),
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
    const backgroundToolName = codexBackgroundToolName(shell?.request, shell?.tool_name)

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
      title: shellTitle(kind, sessionId, shell?.request, backgroundToolName),
      detail:
        kind === 'background_command' && backgroundToolName
          ? codexBackgroundDetail(
            {
              session_id: sessionId,
              state,
              exit_code: exitCode,
              started_at: shell?.process?.started_at ?? 0,
              finished_at: state === 'running' ? null : Date.now() / 1000,
              runtime_seconds: shell?.process?.runtime_seconds ?? 0,
              timed_out: timedOut,
            },
            existing?.stdout ?? '',
            backgroundToolName,
            isError ? result : '',
          )
          : isError
            ? result
            : '',
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
          ? normalizeShellCommand(metadata.command)
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
          ...(typeof metadata.command === 'string'
            ? { command: normalizeShellCommand(metadata.command) }
            : {}),
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

  return proxyRefs({
    isBooting,
    isSending,
    errorMessage,
    successMessage,
    health,
    config,
    mcpConfig,
    channelWorkspace,
    channelPlatforms,
    channelConfig,
    channelMonitorSessions,
    codexWorkspace,
    channelLoginState,
    activeSessionRuntime,
    activeSessionId,
    openingSessionId,
    chatMessages,
    activities,
    codexTurnTimings,
    isHydratingOlderActivity,
    isSwitchingSession,
    sessionHistory,
    activeCodexWorkMode,
    activeCodexGoalLoop,
    isCodexCompactLayout,
    isSidebarDrawerOpen,
    isRuntimeSheetOpen,
    isCodexAdvancedModeOpen,
    selectedCodexAdvancedMode,
    codexAdvancedModeFabEdge,
    codexAdvancedModeFabTop,
    composerText,
    composerAttachments,
    queuedComposerFollowups,
    steerText,
    isSteering,
    steeringQueuedComposerFollowupId,
    isInterrupting,
    composerSelectionStart,
    composerSelectionEnd,
    composerSelectionVersion,
    deletingSessionId,
    savingState,
    appForm,
    codexGoalLoopDraft,
    newSessionDraft,
    llmForm,
    rootsDraft,
    mcpDraft,
    isSettingsRoute,
    isChannelRoute,
    isChatRoute,
    activeSession,
    backendOptions,
    defaultAllowedRoots,
    sessionLabel,
    llmReady,
    frontendMode,
    activeBackendId,
    activeBackendReady,
    canCompose,
    canComposeToSession,
    canSend,
    canSendToSession,
    activeProjectPath,
    workspaceEyebrow,
    workspaceTitle,
    sessionHistoryCount,
    sidebarSessionHistory,
    sidebarSessionHistoryCount,
    isCodexWorkspace,
    activeCodexProjects,
    activeCodexPairedEditors,
    assistantLabel,
    showMobileWorkspaceChrome,
    showCodexMobileChrome,
    isMobileChatPage,
    showSidebarDrawer,
    showRuntimeSheet,
    showCodexAdvancedModePanel,
    showCodexAdvancedModeSheet,
    showCodexAdvancedModeFab,
    showCodexAdvancedModeOverlay,
    activeWorkspaceSurface,
    workspaceSurfaceOptions,
    workspaceSurfaceModel,
    composerPlaceholder,
    composerPendingRequest,
    composerUserInputRequest,
    composerImplementPlanRequest,
    showQueuedComposerFollowupsPanel,
    hasCodexGoalLoop,
    codexGoalLoopSummary,
    codexGoalLoopShortStatus,
    isCodexGoalLoopRunning,
    openSidebarDrawer,
    closeSidebarDrawer,
    openRuntimeSheet,
    closeRuntimeSheet,
    openCodexAdvancedMode,
    closeCodexAdvancedMode,
    toggleCodexAdvancedMode,
    closeCodexSheets,
    selectCodexAdvancedMode,
    setCodexAdvancedModeFabPosition,
    handleNewChatClick,
    handleCodexSessionStart,
    openCodexNativeSession,
    openSessionFromHistory,
    deleteSessionFromHistory,
    displayNameForPath,
    formatSessionUpdatedAt,
    openSettings,
    openChannel,
    openChat,
    updateCodexWorkMode,
    saveCodexGoalLoop,
    runCodexGoalLoopAction,
    saveLlmSettings,
    saveAppSettings,
    saveCodexSandboxMode,
    saveAllowedRoots,
    saveMcpSettings,
    reloadMcpSettings,
    addMcpServer,
    addAllowedRoot,
    removeAllowedRoot,
    resetAllowedRoots,
    removeMcpServer,
    loginWeixin,
    startChannelAccount,
    stopChannelAccount,
    submitPendingRequestDecision,
    submitMessage,
    sendComposerMessage,
    submitQueuedComposerFollowupSteer,
    removeQueuedComposerFollowup,
    uploadComposerFiles,
    removeComposerAttachment,
    retryComposerAttachment,
    submitCodexSteer,
    interruptCodexTurn,
    handleComposerSelectionChange,
  })
}

export type WorkspaceAppContext = ReturnType<typeof createWorkspaceApp>

const workspaceAppContextKey: InjectionKey<WorkspaceAppContext> = Symbol('workspace-app')

export function provideWorkspaceAppContext() {
  const context = createWorkspaceApp()
  provide(workspaceAppContextKey, context)
  return context
}

export function useWorkspaceAppContext() {
  const context = inject(workspaceAppContextKey)
  if (!context) {
    throw new Error('Workspace app context is not available.')
  }
  return context
}
