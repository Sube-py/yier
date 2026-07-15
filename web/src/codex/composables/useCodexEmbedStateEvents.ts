import { ref, watch, type ComputedRef, type Ref } from 'vue'

import { latestTurnState, type EmbedMessageType, type EmbedWorkStatus } from '../lib/embedProtocol'
import type {
  CodexConversationState,
  CodexPendingRequest,
  CodexQueuedFollowup,
  CodexWorkMode,
  JsonRecord,
} from '../types'

interface EmbedStateSource {
  activeThreadId: string
  activeStatus: string
  activeMode: CodexWorkMode
  activeUserInputRequest: CodexPendingRequest | null
  activeThreadState: CodexConversationState | null
  queuedFollowups: CodexQueuedFollowup[]
  isCommandBusy: boolean
}

interface EmbedStateEventOptions {
  source: EmbedStateSource
  hasStarted: Ref<boolean>
  isInitializing: Ref<boolean>
  displayError: ComputedRef<string>
  postMessage: (type: EmbedMessageType, payload?: JsonRecord) => void
}

export function useCodexEmbedStateEvents(options: EmbedStateEventOptions) {
  const { source, hasStarted, isInitializing, displayError, postMessage } = options
  const lastStatusKey = ref('')
  const lastTurnKey = ref('')
  const lastGoalKey = ref('')
  const lastModeKey = ref('')
  const lastRequestKey = ref('')
  const lastFollowupsKey = ref('')

  function workStatus(): EmbedWorkStatus {
    if (displayError.value) return 'failed'
    if (source.activeUserInputRequest) return 'awaiting_approval'
    if (isInitializing.value || source.isCommandBusy) return 'running'
    if (['inProgress', 'active', 'working'].includes(source.activeStatus)) return 'running'
    if (['completed', 'complete', 'succeeded', 'success'].includes(source.activeStatus)) {
      return 'done'
    }
    if (['error', 'failed'].includes(source.activeStatus)) return 'failed'
    return source.activeStatus === 'planning' ? 'planning' : 'idle'
  }

  function postStatus() {
    if (!hasStarted.value && !source.activeThreadId && !displayError.value) return
    const status = workStatus()
    const request = source.activeUserInputRequest
    const turn = latestTurnState(source.activeThreadState?.turns)
    const key = JSON.stringify({
      status,
      threadId: source.activeThreadId,
      turnId: turn?.turnId ?? '',
      rawStatus: source.activeStatus,
      mode: source.activeMode,
      requestId: request?.id ?? '',
      requestMethod: request?.method ?? '',
      error: displayError.value,
    })
    if (key === lastStatusKey.value) return
    lastStatusKey.value = key
    postMessage('yier:codex-status', {
      status,
      threadId: source.activeThreadId,
      turnId: turn?.turnId ?? '',
      mode: source.activeMode,
      codexStatus: source.activeStatus,
      requestId: request?.id ?? '',
      requestMethod: request?.method ?? '',
      message: displayError.value,
    })
  }

  function postTurn() {
    if (!source.activeThreadId) return
    const turn = latestTurnState(source.activeThreadState?.turns)
    const key = JSON.stringify({ threadId: source.activeThreadId, turn })
    if (key === lastTurnKey.value) return
    lastTurnKey.value = key
    postMessage('yier:codex-turn-state', {
      threadId: source.activeThreadId,
      mode: source.activeMode,
      turn,
    })
  }

  function postGoal() {
    if (!source.activeThreadId) return
    const goal = source.activeThreadState?.threadGoal ?? null
    const completedGoal = source.activeThreadState?.completedThreadGoal ?? null
    const key = JSON.stringify({ threadId: source.activeThreadId, goal, completedGoal })
    if (key === lastGoalKey.value) return
    lastGoalKey.value = key
    postMessage('yier:codex-goal-state', {
      threadId: source.activeThreadId,
      goal,
      completedGoal,
    })
  }

  function postMode() {
    if (!source.activeThreadId) return
    const key = `${source.activeThreadId}:${source.activeMode}`
    if (key === lastModeKey.value) return
    lastModeKey.value = key
    postMessage('yier:codex-mode-changed', {
      threadId: source.activeThreadId,
      mode: source.activeMode,
    })
  }

  function postRequest() {
    if (!source.activeThreadId) return
    const request = source.activeUserInputRequest ?? null
    const key = JSON.stringify({ threadId: source.activeThreadId, request })
    if (key === lastRequestKey.value) return
    lastRequestKey.value = key
    postMessage('yier:codex-user-input-request', {
      threadId: source.activeThreadId,
      request,
    })
  }

  function postFollowups() {
    if (!source.activeThreadId) return
    const key = JSON.stringify({
      threadId: source.activeThreadId,
      followups: source.queuedFollowups,
    })
    if (key === lastFollowupsKey.value) return
    lastFollowupsKey.value = key
    postMessage('yier:codex-followups-changed', {
      threadId: source.activeThreadId,
      followups: source.queuedFollowups,
    })
  }

  watch(
    () => [
      source.activeThreadId,
      source.activeStatus,
      source.activeMode,
      source.activeUserInputRequest?.id ?? '',
      source.activeUserInputRequest?.method ?? '',
      source.isCommandBusy,
      isInitializing.value,
      displayError.value,
    ],
    postStatus,
  )
  watch(
    () => JSON.stringify({
      threadId: source.activeThreadId,
      turn: latestTurnState(source.activeThreadState?.turns),
    }),
    postTurn,
  )
  watch(
    () => JSON.stringify({
      threadId: source.activeThreadId,
      goal: source.activeThreadState?.threadGoal ?? null,
      completedGoal: source.activeThreadState?.completedThreadGoal ?? null,
    }),
    postGoal,
  )
  watch(() => [source.activeThreadId, source.activeMode], postMode)
  watch(
    () => JSON.stringify({
      threadId: source.activeThreadId,
      request: source.activeUserInputRequest ?? null,
    }),
    postRequest,
  )
  watch(
    () => JSON.stringify({
      threadId: source.activeThreadId,
      followups: source.queuedFollowups,
    }),
    postFollowups,
  )
}
