import { flushPromises, shallowMount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'

import { createTestRouter } from '../../router'
import CodexEmbedView from '../../views/CodexEmbedView.vue'
import type { CodexConversationState, CodexPendingRequest } from '../types'

const workspace = {
  activeMode: ref('build'),
  activeStatus: ref('idle'),
  activeThreadId: ref(''),
  activeThreadState: ref<CodexConversationState | null>(null),
  activeUserInputRequest: ref<CodexPendingRequest | null>(null),
  archivingThreadId: ref(''),
  archiveThread: vi.fn(),
  compactThread: vi.fn(),
  connect: vi.fn(),
  enqueueFollowup: vi.fn(),
  errorMessage: ref(''),
  forkingThreadId: ref(''),
  forkThread: vi.fn(),
  isActiveTurnInProgress: ref(false),
  isArchiving: ref(false),
  isBooting: ref(false),
  isCommandBusy: ref(false),
  isRenaming: ref(false),
  openingThreadId: ref(''),
  projectPathDraft: ref(''),
  queuedFollowups: ref([]),
  refreshWorkspace: vi.fn(),
  removeFollowup: vi.fn(),
  renameThread: vi.fn(),
  resumeEmbedThread: vi.fn(),
  selectThread: vi.fn(),
  sendPrompt: vi.fn(),
  setMode: vi.fn(),
  startEmbedThread: vi.fn(),
  startThread: vi.fn(),
  status: ref('idle'),
  steerPrompt: vi.fn(),
  submitUserInputResponse: vi.fn(),
  successMessage: ref(''),
  unarchiveThread: vi.fn(),
  workspace: ref({ paired_editors: [], projects: [] }),
  interruptTurn: vi.fn(),
}

vi.mock('../composables/useCodexWorkspace', () => ({
  useCodexWorkspace: vi.fn(() => workspace),
}))

const wrappers: Array<ReturnType<typeof shallowMount>> = []

async function mountEmbed(path: string) {
  const router = createTestRouter()
  await router.push(path)
  await router.isReady()
  const wrapper = shallowMount(CodexEmbedView, {
    global: {
      plugins: [router],
      stubs: {
        CodexChatPane: true,
      },
    },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

async function sendHostMessage(payload: Record<string, unknown>) {
  window.dispatchEvent(new MessageEvent('message', { data: payload }))
  await flushPromises()
}

describe('CodexEmbedView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workspace.activeThreadId.value = ''
    workspace.activeThreadState.value = null
    workspace.activeUserInputRequest.value = null
    workspace.activeStatus.value = 'idle'
    workspace.errorMessage.value = ''
    workspace.successMessage.value = ''
    workspace.status.value = 'idle'
    workspace.connect.mockResolvedValue(undefined)
    workspace.sendPrompt.mockResolvedValue(undefined)
    workspace.setMode.mockResolvedValue(true)
    workspace.startEmbedThread.mockResolvedValue({
      thread_id: 'thread-created',
      state: { id: 'thread-created', cwd: '/tmp/embed' },
    })
    workspace.resumeEmbedThread.mockResolvedValue(true)
    vi.spyOn(window.parent, 'postMessage').mockImplementation(() => undefined)
  })

  afterEach(() => {
    for (const wrapper of wrappers.splice(0)) {
      wrapper.unmount()
    }
  })

  it('creates and announces a thread from cwd', async () => {
    workspace.activeThreadState.value = { id: 'thread-created', cwd: '/tmp/embed' }
    await mountEmbed('/codex/embed?embed_token=secret')
    await sendHostMessage({ type: 'yier:codex-start', cwd: '/tmp/embed' })

    expect(workspace.connect).toHaveBeenCalledOnce()
    expect(workspace.startEmbedThread).toHaveBeenCalledWith('/tmp/embed')
    expect(workspace.resumeEmbedThread).not.toHaveBeenCalled()
    expect(window.parent.postMessage).toHaveBeenCalledWith(
      {
        type: 'yier:codex-thread-created',
        threadId: 'thread-created',
        cwd: '/tmp/embed',
        mode: 'build',
      },
      '*',
    )
  })

  it('resumes and announces a thread from thread_id', async () => {
    workspace.activeThreadState.value = { id: 'thread-a', cwd: '/tmp/project' }
    await mountEmbed('/codex/embed?embed_token=secret')
    await sendHostMessage({ type: 'yier:codex-resume', threadId: 'thread-a' })

    expect(workspace.connect).toHaveBeenCalledOnce()
    expect(workspace.resumeEmbedThread).toHaveBeenCalledWith('thread-a')
    expect(workspace.startEmbedThread).not.toHaveBeenCalled()
    expect(window.parent.postMessage).toHaveBeenCalledWith(
      {
        type: 'yier:codex-thread-resumed',
        threadId: 'thread-a',
        cwd: '/tmp/project',
        mode: 'build',
      },
      '*',
    )
  })

  it('applies plan mode after creating a thread', async () => {
    workspace.activeThreadState.value = { id: 'thread-created', cwd: '/tmp/embed' }
    const wrapper = await mountEmbed('/codex/embed?cwd=/tmp/embed&mode=plan&embed_token=secret')

    expect(workspace.startEmbedThread).toHaveBeenCalledWith('/tmp/embed')
    expect(workspace.setMode).toHaveBeenCalledWith('plan')
    expect(wrapper.vm.$route.query.mode).toBeUndefined()
    expect(window.parent.postMessage).toHaveBeenCalledWith(
      {
        type: 'yier:codex-thread-created',
        threadId: 'thread-created',
        cwd: '/tmp/embed',
        mode: 'plan',
      },
      '*',
    )
  })

  it('sends an initial prompt after creating a thread', async () => {
    workspace.activeThreadState.value = { id: 'thread-created', cwd: '/tmp/embed' }
    await mountEmbed('/codex/embed?embed_token=secret')
    await sendHostMessage({
      type: 'yier:codex-start',
      cwd: '/tmp/embed',
      mode: 'plan',
      prompt: 'Build a summary',
    })

    expect(workspace.startEmbedThread).toHaveBeenCalledWith('/tmp/embed')
    expect(workspace.setMode).toHaveBeenCalledWith('plan')
    expect(workspace.sendPrompt).toHaveBeenCalledWith('Build a summary')
    expect(window.parent.postMessage).toHaveBeenCalledWith(
      {
        type: 'yier:codex-prompt-sent',
        threadId: 'thread-created',
        cwd: '/tmp/embed',
        mode: 'plan',
      },
      '*',
    )
  })

  it('announces active work status changes to the host', async () => {
    workspace.activeThreadState.value = { id: 'thread-created', cwd: '/tmp/embed' }
    await mountEmbed('/codex/embed?embed_token=secret')
    await sendHostMessage({ type: 'yier:codex-start', cwd: '/tmp/embed' })

    workspace.activeThreadId.value = 'thread-created'
    workspace.activeStatus.value = 'inProgress'
    await nextTick()

    expect(window.parent.postMessage).toHaveBeenCalledWith(
      {
        type: 'yier:codex-status',
        status: 'running',
        threadId: 'thread-created',
        mode: 'build',
        codexStatus: 'inProgress',
        requestId: '',
        requestMethod: '',
        message: '',
      },
      '*',
    )

    workspace.activeUserInputRequest.value = {
      id: 'request-1',
      method: 'item/tool/requestUserInput',
    }
    await nextTick()

    expect(window.parent.postMessage).toHaveBeenCalledWith(
      {
        type: 'yier:codex-status',
        status: 'awaiting_approval',
        threadId: 'thread-created',
        mode: 'build',
        codexStatus: 'inProgress',
        requestId: 'request-1',
        requestMethod: 'item/tool/requestUserInput',
        message: '',
      },
      '*',
    )

    workspace.activeUserInputRequest.value = null
    workspace.activeStatus.value = 'completed'
    await nextTick()

    expect(window.parent.postMessage).toHaveBeenCalledWith(
      {
        type: 'yier:codex-status',
        status: 'done',
        threadId: 'thread-created',
        mode: 'build',
        codexStatus: 'completed',
        requestId: '',
        requestMethod: '',
        message: '',
      },
      '*',
    )
  })

  it('applies plan mode after resuming a thread', async () => {
    workspace.activeThreadState.value = { id: 'thread-a', cwd: '/tmp/project' }
    await mountEmbed('/codex/embed?embed_token=secret')
    await sendHostMessage({ type: 'yier:codex-resume', threadId: 'thread-a', mode: 'plan' })

    expect(workspace.resumeEmbedThread).toHaveBeenCalledWith('thread-a')
    expect(workspace.setMode).toHaveBeenCalledWith('plan')
    expect(window.parent.postMessage).toHaveBeenCalledWith(
      {
        type: 'yier:codex-thread-resumed',
        threadId: 'thread-a',
        cwd: '/tmp/project',
        mode: 'plan',
      },
      '*',
    )
  })

  it('rejects invalid mode without sending commands', async () => {
    const wrapper = await mountEmbed('/codex/embed?embed_token=secret')
    await sendHostMessage({ type: 'yier:codex-start', cwd: '/tmp/embed', mode: 'review' })

    expect(workspace.connect).not.toHaveBeenCalled()
    expect(workspace.startEmbedThread).not.toHaveBeenCalled()
    expect(workspace.resumeEmbedThread).not.toHaveBeenCalled()
    expect(workspace.setMode).not.toHaveBeenCalled()
    expect(wrapper.getComponent({ name: 'CodexChatPane' }).props('errorMessage')).toBe(
      'mode must be build or plan.',
    )
  })

  it('sends a follow-up prompt after resuming a thread', async () => {
    workspace.activeThreadState.value = { id: 'thread-a', cwd: '/tmp/project' }
    await mountEmbed('/codex/embed?embed_token=secret')
    await sendHostMessage({ type: 'yier:codex-resume', threadId: 'thread-a', prompt: 'Continue' })

    expect(workspace.connect).toHaveBeenCalledOnce()
    expect(workspace.startEmbedThread).not.toHaveBeenCalled()
    expect(workspace.resumeEmbedThread).toHaveBeenCalledWith('thread-a')
    expect(workspace.sendPrompt).toHaveBeenCalledWith('Continue')
    expect(window.parent.postMessage).toHaveBeenCalledWith(
      {
        type: 'yier:codex-prompt-sent',
        threadId: 'thread-a',
        cwd: '/tmp/project',
        mode: 'build',
      },
      '*',
    )
  })

  it('rejects conflicting cwd and thread_id without sending commands', async () => {
    const wrapper = await mountEmbed(
      '/codex/embed?cwd=/tmp/embed&thread_id=thread-a&embed_token=secret',
    )

    expect(workspace.connect).not.toHaveBeenCalled()
    expect(workspace.startEmbedThread).not.toHaveBeenCalled()
    expect(workspace.resumeEmbedThread).not.toHaveBeenCalled()
    expect(wrapper.getComponent({ name: 'CodexChatPane' }).props('errorMessage')).toBe(
      'Pass either cwd or thread_id, not both.',
    )
    expect(window.parent.postMessage).toHaveBeenCalledWith(
      {
        type: 'yier:codex-error',
        message: 'Pass either cwd or thread_id, not both.',
      },
      '*',
    )
  })

  it('does not render the session sidebar', async () => {
    const wrapper = await mountEmbed('/codex/embed?thread_id=thread-a&embed_token=secret')

    expect(wrapper.findComponent({ name: 'CodexSidebar' }).exists()).toBe(false)
    expect(wrapper.find('codex-sidebar-stub').exists()).toBe(false)
  })

  it('announces readiness when loaded without a command query', async () => {
    await mountEmbed('/codex/embed?embed_token=secret')

    expect(window.parent.postMessage).toHaveBeenCalledWith({ type: 'yier:codex-ready' }, '*')
    expect(workspace.connect).not.toHaveBeenCalled()
  })

  it('keeps supporting legacy query-based start', async () => {
    workspace.activeThreadState.value = { id: 'thread-created', cwd: '/tmp/embed' }
    await mountEmbed(
      '/codex/embed?cwd=/tmp/embed&mode=plan&prompt=Build%20a%20summary&embed_token=secret',
    )

    expect(workspace.startEmbedThread).toHaveBeenCalledWith('/tmp/embed')
    expect(workspace.setMode).toHaveBeenCalledWith('plan')
    expect(workspace.sendPrompt).toHaveBeenCalledWith('Build a summary')
  })
})
