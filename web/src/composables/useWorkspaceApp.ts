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

import { createApprovalActivity } from '../components/chat-timeline/helpers'
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
  ComposerAttachmentState,
  ChatTurnAbortedEvent,
  ConfigResponse,
  CreateSessionRequest,
  DeleteSessionResponse,
  EditableAllowedRoot,
  EditableMcpServer,
  HealthResponse,
  LlmProvider,
  McpConfigResponse,
  MessageAttachment,
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
  WorkspaceSurface,
} from '../types/api'

const SESSION_STORAGE_KEY = 'yier.active-session-id'
const WORKSPACE_SURFACE_STORAGE_KEY = 'yier.workspace-surface'
const COMPACT_MEDIA_QUERY = '(max-width: 1023px)'
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
  if (value === 'yier' || value === 'codex' || value === 'claude') {
    return value
  }
  return 'yier'
}

function readCachedWorkspaceSurface(): WorkspaceSurface {
  return normalizeWorkspaceSurface(localStorage.getItem(WORKSPACE_SURFACE_STORAGE_KEY))
}

type LoadedPendingRequest = SessionTranscriptResponse['pending_requests'][number]
type PendingRequestId = string | number

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

function normalizePendingRequestId(value: PendingRequestId | null | undefined) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value)
  }
  if (typeof value === 'string') {
    const trimmed = value.trim()
    return trimmed ? trimmed : ''
  }
  return ''
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
  const isHydratingOlderActivity = ref(false)
  const sessionHistory = ref<SessionSummary[]>([])
  const isSidebarDrawerOpen = ref(false)
  const isRuntimeSheetOpen = ref(false)
  const composerText = ref('')
  const composerAttachments = ref<ComposerAttachmentState[]>([])
  const queuedComposerFollowups = ref<QueuedComposerFollowup[]>([])
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
    () => findSessionSummary(activeSessionId.value) ?? null,
  )
  const backendOptions = computed(
    () =>
      (config.value?.backends ?? [
        { id: 'yier' as BackendId, label: 'Yier Agent' },
      ]).filter((backend) => backend.id !== 'codex'),
  )
  const defaultAllowedRoots = computed(() => health.value?.allowed_roots ?? [])
  let closePersistentEventStream: (() => void) | null = null
  let latestSessionLoadRequestId = 0
  let nextTimelineSequence = 0
  let currentStreamSequenceHint: number | null = null
  let isReplayingSessionActivityEvents = false
  let isFlushingQueuedComposerFollowups = false
  let compactMediaQuery: MediaQueryList | null = null
  const unavailableSessionIds = new Set<string>()
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
    return llmReady.value
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
    () => false,
  )
  const activeProjectPath = computed(
    () => activeSession.value?.project_path ?? newSessionDraft.projectPath,
  )
  const workspaceEyebrow = computed(() =>
    isSettingsRoute.value
      ? 'Configuration workspace'
      : isChannelRoute.value
        ? 'Channel workspace'
        : 'Chat workspace',
  )
  const workspaceSessionHistory = computed(() =>
    sessionHistory.value,
  )
  const sessionHistoryCount = computed(() => workspaceSessionHistory.value.length)
  const sidebarSessionHistory = computed(() =>
    sessionHistory.value.filter((session) => session.backend_id !== 'codex'),
  )
  const sidebarSessionHistoryCount = computed(() => sidebarSessionHistory.value.length)
  const isCodexWorkspace = computed(
    () => route.name === 'codex',
  )
  const assistantLabel = computed(() =>
    'Yier',
  )
  const isCompactLayout = ref(false)
  const isCodexCompactLayout = computed(() => isCompactLayout.value)
  const showMobileWorkspaceChrome = computed(
    () => false,
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
    if (route.name === 'codex') {
      return 'codex'
    }
    if (activeSession.value?.backend_id === 'yier') {
      return 'yier'
    }
    return appForm.workspaceSurface === 'claude' ? 'claude' : 'yier'
  })
  const workspaceSurfaceOptions: Array<{
    label: string
    value: WorkspaceSurface
    disabled: boolean
  }> = [
    { label: 'Yier Agent', value: 'yier', disabled: false },
    { label: 'Codex', value: 'codex', disabled: false },
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
    'Ask yier to inspect code, read files...',
  )
  const composerPendingRequest = computed(() => loadedPendingRequests.value[0] ?? null)
  const composerUserInputRequest = computed(() =>
    composerPendingRequest.value?.kind === 'user_input'
      ? composerPendingRequest.value
      : null,
  )
  const composerImplementPlanRequest = computed(() =>
    composerPendingRequest.value?.kind === 'plan_implementation'
      ? composerPendingRequest.value
      : null,
  )

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

  function openRuntimeSheet() {
    isSidebarDrawerOpen.value = false
    isRuntimeSheetOpen.value = true
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

  function updateCompactLayout(matches: boolean) {
    isCompactLayout.value = matches
  }

  function handleCompactLayoutChange(event: MediaQueryListEvent) {
    updateCompactLayout(event.matches)
  }

  function setupCompactLayoutWatcher() {
    compactMediaQuery = window.matchMedia(COMPACT_MEDIA_QUERY)
    updateCompactLayout(compactMediaQuery.matches)
    if (typeof compactMediaQuery.addEventListener === 'function') {
      compactMediaQuery.addEventListener('change', handleCompactLayoutChange)
      return
    }
    compactMediaQuery.addListener(handleCompactLayoutChange)
  }

  function teardownCompactLayoutWatcher() {
    if (!compactMediaQuery) {
      return
    }
    if (typeof compactMediaQuery.removeEventListener === 'function') {
      compactMediaQuery.removeEventListener('change', handleCompactLayoutChange)
    } else {
      compactMediaQuery.removeListener(handleCompactLayoutChange)
    }
    compactMediaQuery = null
  }

  watch(activeSessionId, (value) => {
    if (!value) {
      localStorage.removeItem(SESSION_STORAGE_KEY)
    } else {
      localStorage.setItem(SESSION_STORAGE_KEY, value)
    }
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
      showRuntimeSheet.value,
    (locked) => {
      syncSheetScrollLock(locked)
    },
    { immediate: true },
  )

  onMounted(async () => {
    setupCompactLayoutWatcher()
    window.addEventListener('keydown', handleGlobalKeydown)
    startPersistentEvents()
    await bootstrap()
  })

  onBeforeUnmount(() => {
    syncSheetScrollLock(false)
    window.removeEventListener('keydown', handleGlobalKeydown)
    teardownCompactLayoutWatcher()
    closePersistentEventStream?.()
    closePersistentEventStream = null
  })

  async function bootstrap() {
    isBooting.value = true
    errorMessage.value = ''
    try {
      await refreshDashboard()
      await ensureSession({
        preferFreshOnMissingActiveSession: false,
      })
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
      channelWorkspacePayload,
      channelPlatformsPayload,
      channelConfigPayload,
      channelMonitorSessionsPayload,
    ] = await Promise.all([
      apiGet<HealthResponse>('/api/health'),
      apiGet<ConfigResponse>('/api/config'),
      apiGet<McpConfigResponse>('/api/config/mcp'),
      apiGet<SessionListResponse>('/api/chat/sessions'),
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
    sessionHistory.value = normalizeChatSessionSummaries(sessionsPayload)
    hydrateLlmForm(configPayload.llm)
    hydrateAppForm(configPayload)
    initializeNewSessionDraft(configPayload)
    rootsDraft.value = toEditableAllowedRoots(configPayload.allowed_roots)
    mcpDraft.value = toEditableMcpServers(mcpPayload)
  }

  async function ensureSession(
    options: {
      preferFreshOnMissingActiveSession?: boolean
    } = {},
  ) {
    const preferFreshOnMissingActiveSession =
      options.preferFreshOnMissingActiveSession ?? false

    if (activeSessionId.value) {
      const currentSessionId = activeSessionId.value
      try {
        await loadSessionTranscript(currentSessionId)
        return
      } catch (error) {
        activeSessionId.value = ''
        if (
          preferFreshOnMissingActiveSession &&
          isMissingSessionError(error)
        ) {
          const fallbackProjectPath =
            activeProjectPath.value || newSessionDraft.projectPath
          await createSession('yier', fallbackProjectPath, false)
          return
        }
      }
    }

    const availableSessionHistory = workspaceSessionHistory.value.filter(
      (session) => !unavailableSessionIds.has(session.session_id),
    )
    if (availableSessionHistory.length) {
      activeSessionId.value = availableSessionHistory[0]?.session_id ?? ''
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
    const normalizedBackendId = backendId === 'codex' ? 'yier' : backendId
    const payload = await apiPost<{ session_id: string }>('/api/chat/sessions', {
      backend_id: normalizedBackendId,
      project_path: projectPath,
    } satisfies CreateSessionRequest)
    activeSessionId.value = payload.session_id
    chatMessages.value = []
    activities.value = []
    resetLoadedTranscriptState()
    resetTimelineSequence()
    activeSessionRuntime.value = null
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
    const normalizedBackendId = backendId === 'codex' ? 'yier' : backendId
    return sessionHistory.value.find(
      (session) => session.source === 'chat' && session.backend_id === normalizedBackendId,
    )?.session_id
  }

  async function refreshSessionHistory() {
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
          throw new Error(event.data.reason || 'Turn was interrupted.')
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

  function backendIdForWorkspaceSurface(surface: WorkspaceSurface): BackendId {
    void surface
    return 'yier'
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

    if (target === 'codex') {
      await router.push({ name: 'codex' })
      return
    }

    const previousWorkspaceSurface = appForm.workspaceSurface
    const previousDraftBackendId = newSessionDraft.backendId
    const backendId = backendIdForWorkspaceSurface(target)
    appForm.workspaceSurface = target
    newSessionDraft.backendId = backendId

    try {
      sessionHistory.value = normalizeChatSessionSummaries(
        await apiGet<SessionListResponse>('/api/chat/sessions'),
      )
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
      errorMessage.value = 'Attachments are not available in the Yier chat workspace.'
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

  function removeQueuedComposerFollowup(followupId: string) {
    queuedComposerFollowups.value = queuedComposerFollowups.value.filter(
      (item) => item.id !== followupId,
    )
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
    appendTimelinePendingRequests()
  }

  function shouldRenderPendingRequestInComposer(request: LoadedPendingRequest) {
    return request.kind === 'user_input' || request.kind === 'plan_implementation'
  }

  function appendTimelinePendingRequests() {
    const existingApprovalIds = new Set(
      activities.value
        .filter((activity) => activity.kind === 'approval')
        .map((activity) => activity.approval?.requestId)
        .map((requestId) => normalizePendingRequestId(requestId))
        .filter((requestId) => requestId.length > 0),
    )

    for (const request of loadedPendingRequests.value) {
      if (
        shouldRenderPendingRequestInComposer(request)
        || existingApprovalIds.has(normalizePendingRequestId(request.request_id))
      ) {
        continue
      }

      activities.value.push(makeActivity(createApprovalActivity(request)))
    }
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
    return `/api/chat/sessions/${sessionId}`
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

  function buildSessionDeletePath(sessionId: string): string {
    return buildSessionBasePath(sessionId)
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
    let transcript: SessionTranscriptResponse
    try {
      transcript = await apiGet<SessionTranscriptResponse>(
        buildSessionTranscriptPath(sessionId),
      )
    } catch (error) {
      if (isMissingSessionError(error)) {
        markSessionUnavailable(sessionId)
      }
      throw error
    }
    unavailableSessionIds.delete(sessionId)
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
    activeSessionRuntime.value = transcript.backend_runtime ?? null
    syncIsSendingFromRuntime(transcript.backend_runtime)
    rebuildLoadedSessionTimeline()
    void hydrateOlderActivityEvents(sessionId, requestId)
  }

  function resetActiveSessionView() {
    chatMessages.value = []
    activities.value = []
    resetLoadedTranscriptState()
    resetTimelineSequence()
    activeSessionRuntime.value = null
    backgroundActivityIdsByToolCallId.clear()
    clearQueuedComposerFollowups()
  }

  async function switchToSession(sessionId: string) {
    activeSessionId.value = sessionId
    resetActiveSessionView()
    await ensureSession({
      preferFreshOnMissingActiveSession: false,
    })
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
  }

  function isRelevantPersistentEvent(event: ChatStreamEvent) {
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

      if (event.event === 'run_started') {
        if (activeSessionRuntime.value) {
          activeSessionRuntime.value.status = 'active'
          activeSessionRuntime.value.detail = 'Yier is working on this turn.'
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
        upsertShellActivity(activityId, {
          id: activityId,
          kind: 'background',
          title: shellTitle('background_command', event.data.background_session_id),
          detail: 'Background task is running.',
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
        upsertShellActivity(activityId, {
          id: activityId,
          kind: 'background',
          title: shellTitle('background_command', event.data.background_session_id),
          detail: event.data.exit_code === null
            ? `Finished with state ${event.data.state}.`
            : `Finished with state ${event.data.state} and exit code ${event.data.exit_code}.`,
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
          const normalizedRequestId = normalizePendingRequestId(event.data.request_id)
          loadedPendingRequests.value = [
            ...loadedPendingRequests.value.filter(
              (request) => normalizePendingRequestId(request.request_id) !== normalizedRequestId,
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
          const normalizedRequestId = normalizePendingRequestId(event.data.request_id)
          loadedPendingRequests.value = loadedPendingRequests.value.filter(
            (request) => normalizePendingRequestId(request.request_id) !== normalizedRequestId,
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
        errorMessage.value = abortedEvent.data.reason || 'Turn was interrupted.'
        activities.value.push(
          makeActivity({
            id: abortedEvent.data.turn_id
              ? `turn:${abortedEvent.data.turn_id}:aborted`
              : createClientId(),
            title: 'Turn aborted',
            detail:
              abortedEvent.data.reason || 'The turn was interrupted before completion.',
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
              streamErrorEvent.data.will_retry ? 'Yier will retry automatically.' : '',
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
    requestId: PendingRequestId,
    decision: ApprovalDecision,
    contentText: string,
  ) {
    errorMessage.value = ''
    const implementPlanRequest = composerImplementPlanRequest.value
    const normalizedRequestId = normalizePendingRequestId(requestId)
    if (
      implementPlanRequest
      && normalizePendingRequestId(implementPlanRequest.request_id) === normalizedRequestId
    ) {
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
        (request) => normalizePendingRequestId(request.request_id) !== normalizedRequestId,
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
        (request) => normalizePendingRequestId(request.request_id) !== normalizedRequestId,
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

  function findSessionSummary(sessionId: string): SessionSummary | null {
    if (!sessionId) {
      return null
    }
    return sessionHistory.value.find((session) => session.session_id === sessionId) ?? null
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

  function shellTitle(
    kind: ShellActivityState['kind'],
    sessionId: string | null,
  ) {
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
    upsertShellActivity(activityId, {
      id: activityId,
      kind: isBackground ? 'background' : 'command',
      title: shellTitle(isBackground ? 'background_command' : 'shell_command', sessionId),
      detail: isBackground ? 'Waiting for background output.' : 'Preparing shell command.',
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

  function isMissingSessionError(error: unknown) {
    return error instanceof ApiError && error.status === 404
  }

  function markSessionUnavailable(sessionId: string) {
    const normalizedSessionId = sessionId.trim()
    if (!normalizedSessionId) {
      return
    }
    unavailableSessionIds.add(normalizedSessionId)
    sessionHistory.value = sessionHistory.value.filter(
      (session) => session.session_id !== normalizedSessionId,
    )
    if (activeSessionId.value === normalizedSessionId) {
      activeSessionId.value = ''
    }
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
    channelLoginState,
    activeSessionRuntime,
    activeSessionId,
    openingSessionId,
    chatMessages,
    activities,
    isHydratingOlderActivity,
    isSwitchingSession,
    sessionHistory,
    isCodexCompactLayout,
    isSidebarDrawerOpen,
    isRuntimeSheetOpen,
    composerText,
    composerAttachments,
    queuedComposerFollowups,
    composerSelectionStart,
    composerSelectionEnd,
    composerSelectionVersion,
    deletingSessionId,
    savingState,
    appForm,
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
    sessionHistoryCount,
    sidebarSessionHistory,
    sidebarSessionHistoryCount,
    isCodexWorkspace,
    assistantLabel,
    showMobileWorkspaceChrome,
    showCodexMobileChrome,
    isMobileChatPage,
    showSidebarDrawer,
    showRuntimeSheet,
    activeWorkspaceSurface,
    workspaceSurfaceOptions,
    workspaceSurfaceModel,
    composerPlaceholder,
    composerPendingRequest,
    composerUserInputRequest,
    composerImplementPlanRequest,
    showQueuedComposerFollowupsPanel,
    openSidebarDrawer,
    closeSidebarDrawer,
    openRuntimeSheet,
    closeRuntimeSheet,
    closeCodexSheets,
    handleNewChatClick,
    switchWorkspaceSurface,
    openSessionFromHistory,
    deleteSessionFromHistory,
    displayNameForPath,
    formatSessionUpdatedAt,
    openSettings,
    openChannel,
    openChat,
    saveLlmSettings,
    saveAppSettings,
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
    removeQueuedComposerFollowup,
    uploadComposerFiles,
    removeComposerAttachment,
    retryComposerAttachment,
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
