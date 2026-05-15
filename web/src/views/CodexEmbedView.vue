<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, proxyRefs, ref } from 'vue'
import { useRoute } from 'vue-router'

import CodexChatPane from '../codex/components/CodexChatPane.vue'
import { useCodexWorkspace } from '../codex/composables/useCodexWorkspace'
import { codexSocketUrl } from '../codex/lib/codexSocket'
import type { CodexWorkMode, JsonRecord } from '../codex/types'

type EmbedMessageType =
  | 'yier:codex-ready'
  | 'yier:codex-thread-created'
  | 'yier:codex-thread-resumed'
  | 'yier:codex-prompt-sent'
  | 'yier:codex-error'

type EmbedCommandType = 'yier:codex-start' | 'yier:codex-resume'

type EmbedCommand = {
  type?: unknown
  cwd?: unknown
  threadId?: unknown
  thread_id?: unknown
  mode?: unknown
  prompt?: unknown
}

const route = useRoute()
const embedToken = queryText('embed_token')
const codex = proxyRefs(
  useCodexWorkspace({
    autoConnect: false,
    persistActiveThread: false,
    selectInitialThread: false,
    socketUrl: codexSocketUrl(
      `/api/codex/ws?embed_token=${encodeURIComponent(embedToken)}`,
    ),
  }),
)
const initError = ref('')
const isInitializing = ref(false)
const hasStarted = ref(false)

const displayError = computed(() => initError.value || codex.errorMessage)

function queryText(key: string) {
  const value = route.query[key]
  if (Array.isArray(value)) {
    return (value[0] ?? '').trim()
  }
  return typeof value === 'string' ? value.trim() : ''
}

function postEmbedMessage(type: EmbedMessageType, payload: JsonRecord) {
  if (typeof window === 'undefined') {
    return
  }
  window.parent.postMessage({ type, ...payload }, '*')
}

function failEmbedInit(message: string) {
  initError.value = message
  postEmbedMessage('yier:codex-error', { message })
}

function normalizeMode(value: unknown): { mode: CodexWorkMode | null; error: string } {
  const mode = typeof value === 'string' ? value.trim().toLowerCase() : ''
  if (!mode) {
    return { mode: null, error: '' }
  }
  if (mode === 'build' || mode === 'plan') {
    return { mode, error: '' }
  }
  return { mode: null, error: 'mode must be build or plan.' }
}

async function applyRequestedMode(mode: CodexWorkMode | null) {
  if (!mode) {
    return true
  }
  return await codex.setMode(mode)
}

async function ensureEmbedToken() {
  if (!embedToken) {
    failEmbedInit('embed_token is required.')
    return false
  }
  return true
}

async function startFromPayload({
  cwd,
  threadId,
  mode,
  prompt,
}: {
  cwd: string
  threadId: string
  mode: CodexWorkMode | null
  prompt: string
}) {
  if (!(await ensureEmbedToken())) {
    return
  }
  if (cwd && threadId) {
    failEmbedInit('Pass either cwd or thread_id, not both.')
    return
  }
  if (threadId && prompt) {
    failEmbedInit('prompt is only supported when starting a new thread with cwd.')
    return
  }
  if (!cwd && !threadId) {
    failEmbedInit('cwd or thread_id is required.')
    return
  }

  initError.value = ''
  hasStarted.value = true
  isInitializing.value = true
  try {
    await codex.connect()
    if (cwd) {
      const payload = await codex.startEmbedThread(cwd)
      if (!payload?.thread_id) {
        failEmbedInit(codex.errorMessage || 'Codex thread could not be created.')
        return
      }
      if (!(await applyRequestedMode(mode))) {
        failEmbedInit(codex.errorMessage || `Codex mode could not be set to ${mode}.`)
        return
      }
      postEmbedMessage('yier:codex-thread-created', {
        threadId: payload.thread_id,
        cwd: codex.activeThreadState?.cwd ?? cwd,
        mode: mode ?? codex.activeMode,
      })
      if (prompt) {
        await codex.sendPrompt(prompt)
        if (codex.errorMessage) {
          failEmbedInit(codex.errorMessage || 'Initial prompt could not be sent.')
          return
        }
        postEmbedMessage('yier:codex-prompt-sent', {
          threadId: payload.thread_id,
          cwd: codex.activeThreadState?.cwd ?? cwd,
          mode: mode ?? codex.activeMode,
        })
      }
      return
    }

    const resumed = await codex.resumeEmbedThread(threadId)
    if (!resumed) {
      failEmbedInit(codex.errorMessage || 'Codex thread could not be resumed.')
      return
    }
    if (!(await applyRequestedMode(mode))) {
      failEmbedInit(codex.errorMessage || `Codex mode could not be set to ${mode}.`)
      return
    }
    postEmbedMessage('yier:codex-thread-resumed', {
      threadId,
      cwd: codex.activeThreadState?.cwd ?? '',
      mode: mode ?? codex.activeMode,
    })
  } finally {
    isInitializing.value = false
  }
}

async function initializeFromQuery() {
  const cwd = queryText('cwd')
  const threadId = queryText('thread_id')
  const prompt = queryText('prompt')
  const { mode, error } = normalizeMode(queryText('mode'))
  if (error) {
    failEmbedInit(error)
    return
  }
  if (!cwd && !threadId && !prompt && !queryText('mode')) {
    if (!(await ensureEmbedToken())) {
      return
    }
    postEmbedMessage('yier:codex-ready', {})
    return
  }
  await startFromPayload({ cwd, threadId, mode, prompt })
}

function textFromCommand(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

function handleParentMessage(event: MessageEvent<unknown>) {
  if (!event.data || typeof event.data !== 'object' || Array.isArray(event.data)) {
    return
  }
  const command = event.data as EmbedCommand
  if (command.type !== 'yier:codex-start' && command.type !== 'yier:codex-resume') {
    return
  }
  const modeResult = normalizeMode(command.mode)
  if (modeResult.error) {
    failEmbedInit(modeResult.error)
    return
  }
  const cwd = command.type === 'yier:codex-start' ? textFromCommand(command.cwd) : ''
  const threadId =
    command.type === 'yier:codex-resume'
      ? textFromCommand(command.threadId) || textFromCommand(command.thread_id)
      : ''
  void startFromPayload({
    cwd,
    threadId,
    mode: modeResult.mode,
    prompt: textFromCommand(command.prompt),
  })
}

function submitUserInputResponse(requestId: string, response: JsonRecord) {
  void codex.submitUserInputResponse(requestId, response)
}

onMounted(() => {
  window.addEventListener('message', handleParentMessage)
  void initializeFromQuery()
})

onBeforeUnmount(() => {
  window.removeEventListener('message', handleParentMessage)
})
</script>

<template>
  <main class="flex h-screen min-h-0 flex-col overflow-hidden bg-[color:var(--app-bg)]">
    <div
      class="flex items-center justify-between gap-3 border-b border-[color:var(--app-border)] bg-[rgba(255,253,247,0.94)] px-4 py-2"
    >
      <div class="flex min-w-0 items-center gap-2">
        <span
          class="h-2.5 w-2.5 shrink-0 rounded-full"
          :class="codex.status === 'open' ? 'bg-emerald-500' : codex.status === 'connecting' ? 'bg-amber-500' : 'bg-red-500'"
        ></span>
        <span class="truncate text-sm font-semibold text-[color:var(--app-text)]">
          Codex {{ isInitializing ? 'starting' : codex.status }}
        </span>
      </div>
    </div>

    <CodexChatPane
      :active-thread-id="codex.activeThreadId"
      :active-thread-state="codex.activeThreadState"
      :active-user-input-request="codex.activeUserInputRequest"
      :active-status="codex.activeStatus"
      :active-mode="codex.activeMode"
      :queued-followups="codex.queuedFollowups"
      :socket-status="codex.status"
      :error-message="displayError"
      :success-message="codex.successMessage"
      :is-command-busy="codex.isCommandBusy || isInitializing"
      :is-renaming="codex.isRenaming"
      :is-archiving="codex.isArchiving"
      :is-active-turn-in-progress="codex.isActiveTurnInProgress"
      empty-eyebrow="Codex embed"
      :empty-title="isInitializing ? 'Starting thread' : hasStarted ? 'Waiting for thread' : 'Waiting for host message'"
      @rename-thread="codex.renameThread"
      @archive-thread="codex.archiveThread()"
      @compact-thread="codex.compactThread"
      @interrupt-turn="codex.interruptTurn"
      @set-mode="codex.setMode"
      @refresh="codex.refreshWorkspace"
      @submit-user-input-response="submitUserInputResponse"
      @send-prompt="codex.sendPrompt"
      @steer-prompt="codex.steerPrompt"
      @enqueue-followup="codex.enqueueFollowup"
      @remove-followup="codex.removeFollowup"
    />
  </main>
</template>
