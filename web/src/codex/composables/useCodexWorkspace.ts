import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { CodexSocket } from '../lib/codexSocket'
import type {
  CodexCollaborationMode,
  CodexConversationState,
  CodexNativeSessionSummary,
  CodexPendingRequest,
  CodexQueuedFollowup,
  CodexSocketStatus,
  CodexThreadCreateResponse,
  CodexThreadForkResponse,
  CodexThreadStatePayload,
  CodexWorkMode,
  CodexWorkspaceResponse,
  JsonRecord,
} from '../types'

const ACTIVE_THREAD_STORAGE_KEY = 'yier.codex.active-thread-id'

export interface CodexRealtimeClient {
  connect: () => Promise<void>
  close: () => void
  sendCommand: <TPayload = unknown>(
    type: Parameters<CodexSocket['sendCommand']>[0],
    payload?: JsonRecord,
  ) => Promise<TPayload>
  onEvent: CodexSocket['onEvent']
  onStatus: CodexSocket['onStatus']
}

export interface UseCodexWorkspaceOptions {
  autoConnect?: boolean
  socket?: CodexRealtimeClient
}

function readActiveThreadId() {
  if (typeof localStorage === 'undefined') {
    return ''
  }
  return localStorage.getItem(ACTIVE_THREAD_STORAGE_KEY) ?? ''
}

function persistActiveThreadId(threadId: string) {
  if (typeof localStorage === 'undefined') {
    return
  }
  if (!threadId) {
    localStorage.removeItem(ACTIVE_THREAD_STORAGE_KEY)
    return
  }
  localStorage.setItem(ACTIVE_THREAD_STORAGE_KEY, threadId)
}

function emptyWorkspace(): CodexWorkspaceResponse {
  return { projects: [], paired_editors: [] }
}

function normalizeWorkspace(value: unknown): CodexWorkspaceResponse {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return emptyWorkspace()
  }
  const record = value as Partial<CodexWorkspaceResponse>
  return {
    projects: Array.isArray(record.projects) ? record.projects : [],
    paired_editors: Array.isArray(record.paired_editors) ? record.paired_editors : [],
  }
}

function normalizeThreadPayload(
  value: unknown,
  fallbackThreadId: string,
): CodexThreadStatePayload | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null
  }
  const record = value as Partial<CodexThreadStatePayload>
  const threadId =
    typeof record.thread_id === 'string' && record.thread_id.trim()
      ? record.thread_id.trim()
      : fallbackThreadId
  if (!threadId) {
    return null
  }
  return {
    thread_id: threadId,
    state:
      record.state && typeof record.state === 'object' && !Array.isArray(record.state)
        ? (record.state as CodexConversationState)
        : null,
    stream_role:
      record.stream_role && typeof record.stream_role === 'object'
        ? (record.stream_role as JsonRecord)
        : null,
    queued_followups: Array.isArray(record.queued_followups)
      ? record.queued_followups
      : [],
  }
}

function threadStatus(state: CodexConversationState | null) {
  const turns = Array.isArray(state?.turns) ? state.turns : []
  const latestTurn = turns[turns.length - 1]
  if (latestTurn?.status) {
    return latestTurn.status
  }
  const runtime = state?.threadRuntimeStatus
  if (typeof runtime === 'string') {
    return runtime
  }
  if (runtime && typeof runtime === 'object') {
    const type = runtime.type
    if (typeof type === 'string' && type) {
      return type
    }
  }
  return 'idle'
}

function toErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function isPendingUserInputRequest(value: unknown): value is CodexPendingRequest {
  return (
    Boolean(value) &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    (value as CodexPendingRequest).method === 'item/tool/requestUserInput' &&
    typeof (value as CodexPendingRequest).id === 'string' &&
    Boolean((value as CodexPendingRequest).id)
  )
}

function planCollaborationMode(state: CodexConversationState | null): CodexCollaborationMode {
  return {
    mode: 'plan',
    settings: {
      model: state?.latestModel ?? '',
      reasoning_effort: state?.latestReasoningEffort ?? null,
      developer_instructions: null,
    },
  }
}

function buildCollaborationPayload(
  mode: CodexWorkMode,
  state: CodexConversationState | null,
) {
  return mode === 'plan' ? planCollaborationMode(state) : null
}

export function useCodexWorkspace(options: UseCodexWorkspaceOptions = {}) {
  const socket = options.socket ?? new CodexSocket()
  const status = ref<CodexSocketStatus>('idle')
  const workspace = ref<CodexWorkspaceResponse>(emptyWorkspace())
  const threadPayloads = ref<Record<string, CodexThreadStatePayload>>({})
  const activeThreadId = ref(readActiveThreadId())
  const activeSubscriptionId = ref('')
  const openingThreadId = ref('')
  const errorMessage = ref('')
  const successMessage = ref('')
  const isBooting = ref(true)
  const isCommandBusy = ref(false)
  const isRenaming = ref(false)
  const isArchiving = ref(false)
  const archivingThreadId = ref('')
  const forkingThreadId = ref('')
  const projectPathDraft = ref('')

  const flatThreads = computed<CodexNativeSessionSummary[]>(() =>
    workspace.value.projects
      .flatMap((project) => project.sessions)
      .sort(
        (left, right) =>
          right.updated_at - left.updated_at ||
          right.started_at - left.started_at ||
          right.thread_id.localeCompare(left.thread_id),
      ),
  )

  const activeThreadPayload = computed(() =>
    activeThreadId.value ? threadPayloads.value[activeThreadId.value] ?? null : null,
  )
  const activeThreadState = computed(() => activeThreadPayload.value?.state ?? null)
  const activeThread = computed(
    () =>
      flatThreads.value.find((thread) => thread.thread_id === activeThreadId.value) ??
      null,
  )
  const activeStatus = computed(() => threadStatus(activeThreadState.value))
  const isActiveTurnInProgress = computed(() => activeStatus.value === 'inProgress')
  const activeMode = computed<CodexWorkMode>(() =>
    activeThreadState.value?.latestCollaborationMode?.mode === 'plan' ? 'plan' : 'build',
  )
  const pendingUserInputRequests = computed(() =>
    (Array.isArray(activeThreadState.value?.requests)
      ? activeThreadState.value.requests
      : []
    ).filter(isPendingUserInputRequest),
  )
  const activeUserInputRequest = computed(() => pendingUserInputRequests.value[0] ?? null)
  const queuedFollowups = computed<CodexQueuedFollowup[]>(() => {
    const fromPayload = activeThreadPayload.value?.queued_followups
    if (Array.isArray(fromPayload) && fromPayload.length) {
      return fromPayload
    }
    return Array.isArray(activeThreadState.value?.queuedFollowups)
      ? activeThreadState.value.queuedFollowups
      : []
  })

  function setThreadPayload(payload: CodexThreadStatePayload) {
    threadPayloads.value = {
      ...threadPayloads.value,
      [payload.thread_id]: payload,
    }
  }

  function updateActiveState(patch: Partial<CodexConversationState>) {
    const threadId = activeThreadId.value
    if (!threadId) {
      return
    }
    const current = threadPayloads.value[threadId]
    const state = current?.state
    if (!state) {
      return
    }
    setThreadPayload({
      ...current,
      thread_id: threadId,
      state: {
        ...state,
        ...patch,
      },
    })
  }

  function removeThreadPayload(threadId: string) {
    const next = { ...threadPayloads.value }
    delete next[threadId]
    threadPayloads.value = next
  }

  function handleSocketEvent(event: { type: string; payload?: unknown; message?: string }) {
    if (event.type === 'connection_ready') {
      status.value = 'open'
      return
    }
    if (event.type === 'workspace') {
      workspace.value = normalizeWorkspace(event.payload)
      return
    }
    if (event.type === 'thread_snapshot' || event.type === 'thread_state') {
      const payload = normalizeThreadPayload(event.payload, '')
      if (payload) {
        setThreadPayload(payload)
      }
      return
    }
    if (event.type === 'thread_archived') {
      const threadId = threadIdFromEvent(event.payload)
      if (threadId) {
        removeThreadPayload(threadId)
        if (activeThreadId.value === threadId) {
          activeThreadId.value = ''
          persistActiveThreadId('')
        }
      }
      void refreshWorkspaceAndSelect()
      return
    }
    if (event.type === 'thread_unarchived') {
      void refreshWorkspace()
      return
    }
    if (event.type === 'error') {
      errorMessage.value = event.message ?? 'Codex socket error.'
    }
  }

  function threadIdFromEvent(payload: unknown) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      return ''
    }
    const threadId = (payload as { thread_id?: unknown }).thread_id
    return typeof threadId === 'string' ? threadId : ''
  }

  async function runCommand<T>(operation: () => Promise<T>) {
    isCommandBusy.value = true
    errorMessage.value = ''
    successMessage.value = ''
    try {
      return await operation()
    } catch (error) {
      errorMessage.value = toErrorMessage(error)
      return null
    } finally {
      isCommandBusy.value = false
    }
  }

  async function connect() {
    isBooting.value = true
    errorMessage.value = ''
    const unsubscribeEvent = socket.onEvent(handleSocketEvent)
    const unsubscribeStatus = socket.onStatus((nextStatus) => {
      status.value = nextStatus
    })
    try {
      await socket.connect()
      await refreshWorkspaceAndSelect()
    } catch (error) {
      errorMessage.value = toErrorMessage(error)
    } finally {
      isBooting.value = false
    }
    return () => {
      unsubscribeEvent()
      unsubscribeStatus()
    }
  }

  async function refreshWorkspace() {
    const payload = await socket.sendCommand<CodexWorkspaceResponse>('list_threads')
    workspace.value = normalizeWorkspace(payload)
    return workspace.value
  }

  async function refreshWorkspaceAndSelect() {
    await refreshWorkspace()
    const currentThread = activeThreadId.value
    const nextThread =
      flatThreads.value.find((thread) => thread.thread_id === currentThread)?.thread_id ??
      flatThreads.value[0]?.thread_id ??
      ''
    if (!nextThread) {
      activeThreadId.value = ''
      persistActiveThreadId('')
      return
    }
    await selectThread(nextThread)
  }

  async function selectThread(threadId: string) {
    const normalizedThreadId = threadId.trim()
    if (!normalizedThreadId) {
      return false
    }

    openingThreadId.value = normalizedThreadId
    try {
      if (
        activeSubscriptionId.value &&
        activeSubscriptionId.value !== normalizedThreadId
      ) {
        await socket
          .sendCommand('unsubscribe_thread', {
            thread_id: activeSubscriptionId.value,
          })
          .catch(() => null)
      }
      activeThreadId.value = normalizedThreadId
      activeSubscriptionId.value = normalizedThreadId
      persistActiveThreadId(normalizedThreadId)
      const payload = await socket.sendCommand<CodexThreadStatePayload>(
        'subscribe_thread',
        { thread_id: normalizedThreadId },
      )
      const normalizedPayload = normalizeThreadPayload(payload, normalizedThreadId)
      if (normalizedPayload) {
        setThreadPayload(normalizedPayload)
      }
      return true
    } catch (error) {
      errorMessage.value = toErrorMessage(error)
      return false
    } finally {
      openingThreadId.value = ''
    }
  }

  async function startThread(projectPath?: string) {
    await runCommand(async () => {
      const payload = await socket.sendCommand<CodexThreadCreateResponse>(
        'start_thread',
        {
          project_path: projectPath?.trim() || projectPathDraft.value.trim() || undefined,
        },
      )
      const threadId = payload.thread_id
      if (payload.state) {
        setThreadPayload({
          thread_id: threadId,
          state: payload.state,
          stream_role: null,
          queued_followups: [],
        })
      }
      await refreshWorkspace()
      await selectThread(threadId)
      successMessage.value = 'Thread started.'
    })
  }

  async function sendPrompt(prompt: string) {
    const threadId = activeThreadId.value
    const message = prompt.trim()
    if (!threadId || !message) {
      return
    }
    await runCommand(async () => {
      await socket.sendCommand('send_prompt', {
        thread_id: threadId,
        prompt: message,
        collaboration_mode: buildCollaborationPayload(activeMode.value, activeThreadState.value),
      })
    })
  }

  async function steerPrompt(prompt: string) {
    const threadId = activeThreadId.value
    const message = prompt.trim()
    if (!threadId || !message) {
      return
    }
    await runCommand(async () => {
      await socket.sendCommand('steer_prompt', {
        thread_id: threadId,
        prompt: message,
      })
    })
  }

  async function enqueueFollowup(prompt: string) {
    const threadId = activeThreadId.value
    const message = prompt.trim()
    if (!threadId || !message) {
      return
    }
    await runCommand(async () => {
      const followup = await socket.sendCommand<CodexQueuedFollowup>(
        'enqueue_followup',
        { thread_id: threadId, prompt: message },
      )
      const current = activeThreadPayload.value
      if (current) {
        setThreadPayload({
          ...current,
          queued_followups: [...(current.queued_followups ?? []), followup],
        })
      }
      successMessage.value = 'Follow-up queued.'
    })
  }

  async function removeFollowup(messageId: string) {
    const threadId = activeThreadId.value
    if (!threadId || !messageId) {
      return
    }
    await runCommand(async () => {
      await socket.sendCommand('remove_followup', {
        thread_id: threadId,
        message_id: messageId,
      })
      const current = activeThreadPayload.value
      if (current) {
        setThreadPayload({
          ...current,
          queued_followups: (current.queued_followups ?? []).filter(
            (followup) => followup.id !== messageId,
          ),
        })
      }
    })
  }

  async function interruptTurn() {
    const threadId = activeThreadId.value
    if (!threadId) {
      return
    }
    await runCommand(async () => {
      await socket.sendCommand('interrupt_turn', { thread_id: threadId })
      successMessage.value = 'Interrupt sent.'
    })
  }

  async function compactThread() {
    const threadId = activeThreadId.value
    if (!threadId) {
      return
    }
    await runCommand(async () => {
      await socket.sendCommand('compact_thread', { thread_id: threadId })
      successMessage.value = 'Compact requested.'
    })
  }

  async function setMode(mode: CodexWorkMode) {
    const threadId = activeThreadId.value
    if (!threadId || activeMode.value === mode) {
      return
    }
    await runCommand(async () => {
      const collaborationMode = buildCollaborationPayload(mode, activeThreadState.value)
      await socket.sendCommand('set_collaboration_mode', {
        thread_id: threadId,
        collaboration_mode: collaborationMode,
      })
      updateActiveState({
        latestCollaborationMode:
          collaborationMode ?? {
            mode: 'default',
            settings: {
              model: activeThreadState.value?.latestModel ?? '',
              reasoning_effort: activeThreadState.value?.latestReasoningEffort ?? null,
              developer_instructions: null,
            },
          },
      })
    })
  }

  async function renameThread(name: string) {
    const threadId = activeThreadId.value
    const trimmedName = name.trim()
    if (!threadId || !trimmedName) {
      return
    }
    isRenaming.value = true
    await runCommand(async () => {
      await socket.sendCommand('rename_thread', {
        thread_id: threadId,
        name: trimmedName,
      })
      updateActiveState({ title: trimmedName })
      await refreshWorkspace()
      successMessage.value = 'Thread renamed.'
    })
    isRenaming.value = false
  }

  async function archiveThread(threadId = activeThreadId.value) {
    const normalizedThreadId = threadId.trim()
    if (!normalizedThreadId) {
      return
    }
    isArchiving.value = true
    archivingThreadId.value = normalizedThreadId
    await runCommand(async () => {
      await socket.sendCommand('archive_thread', { thread_id: normalizedThreadId })
      removeThreadPayload(normalizedThreadId)
      if (activeThreadId.value === normalizedThreadId) {
        activeThreadId.value = ''
        activeSubscriptionId.value = ''
        persistActiveThreadId('')
      }
      await refreshWorkspaceAndSelect()
      successMessage.value = 'Thread archived.'
    })
    isArchiving.value = false
    archivingThreadId.value = ''
  }

  async function forkThread(threadId = activeThreadId.value) {
    const normalizedThreadId = threadId.trim()
    if (!normalizedThreadId) {
      return
    }
    forkingThreadId.value = normalizedThreadId
    errorMessage.value = ''
    successMessage.value = ''
    try {
      const payload = await socket.sendCommand<CodexThreadForkResponse>('fork_thread', {
        thread_id: normalizedThreadId,
      })
      const forkedThreadId = payload.thread_id.trim()
      if (!forkedThreadId) {
        throw new Error('Codex did not return a forked thread id.')
      }
      if (payload.state) {
        setThreadPayload({
          thread_id: forkedThreadId,
          state: payload.state,
          stream_role: null,
          queued_followups: [],
        })
      }
      await refreshWorkspace()
      const selected = await selectThread(forkedThreadId)
      if (selected) {
        successMessage.value = 'Thread forked.'
      }
    } catch (error) {
      errorMessage.value = toErrorMessage(error)
    } finally {
      forkingThreadId.value = ''
    }
  }

  async function unarchiveThread(threadId: string) {
    const normalizedThreadId = threadId.trim()
    if (!normalizedThreadId) {
      return
    }
    await runCommand(async () => {
      await socket.sendCommand('unarchive_thread', { thread_id: normalizedThreadId })
      await refreshWorkspace()
      successMessage.value = 'Thread unarchived.'
    })
  }

  async function submitUserInputResponse(
    requestId: string,
    response: JsonRecord,
  ) {
    const threadId = activeThreadId.value
    if (!threadId || !requestId) {
      return
    }
    await runCommand(async () => {
      await socket.sendCommand('submit_user_input_response', {
        thread_id: threadId,
        request_id: requestId,
        response,
      })
      const state = activeThreadState.value
      if (state && Array.isArray(state.requests)) {
        updateActiveState({
          requests: state.requests.filter((request) => request.id !== requestId),
        })
      }
    })
  }

  let teardown: (() => void) | null = null
  onMounted(async () => {
    if (options.autoConnect === false) {
      isBooting.value = false
      return
    }
    teardown = await connect()
  })

  onBeforeUnmount(() => {
    teardown?.()
    teardown = null
    socket.close()
  })

  return {
    activeMode,
    activeStatus,
    activeThread,
    activeThreadId,
    activeThreadPayload,
    activeThreadState,
    activeUserInputRequest,
    archivingThreadId,
    archiveThread,
    compactThread,
    connect,
    enqueueFollowup,
    errorMessage,
    flatThreads,
    forkingThreadId,
    forkThread,
    isActiveTurnInProgress,
    isArchiving,
    isBooting,
    isCommandBusy,
    isRenaming,
    openingThreadId,
    pendingUserInputRequests,
    projectPathDraft,
    queuedFollowups,
    refreshWorkspace,
    removeFollowup,
    renameThread,
    selectThread,
    sendPrompt,
    setMode,
    startThread,
    status,
    steerPrompt,
    submitUserInputResponse,
    successMessage,
    unarchiveThread,
    workspace,
    interruptTurn,
  }
}
