import { flushPromises, shallowMount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'

import { createTestRouter } from '../../router'
import CodexEmbedView from '../../views/CodexEmbedView.vue'
import type { CodexConversationState, CodexPendingRequest, CodexQueuedFollowup } from '../types'

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
  isActiveThreadLoading: ref(false),
  isArchiving: ref(false),
  isBooting: ref(false),
  isCommandBusy: ref(false),
  isRenaming: ref(false),
  openingThreadId: ref(''),
  projectPathDraft: ref(''),
  queuedFollowups: ref<CodexQueuedFollowup[]>([]),
  refreshWorkspace: vi.fn(),
  removeFollowup: vi.fn(),
  renameThread: vi.fn(),
  resumeEmbedThread: vi.fn(),
  selectThread: vi.fn(),
  sendPrompt: vi.fn(),
  setThreadGoal: vi.fn(),
  updateThreadGoalStatus: vi.fn(),
  clearThreadGoal: vi.fn(),
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

function postedMessages(type: string) {
  return vi
    .mocked(window.parent.postMessage)
    .mock.calls.map(([message]) => message)
    .filter((message) => (message as { type?: string })?.type === type)
}

describe('CodexEmbedView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workspace.activeThreadId.value = ''
    workspace.activeThreadState.value = null
    workspace.activeUserInputRequest.value = null
    workspace.activeMode.value = 'build'
    workspace.activeStatus.value = 'idle'
    workspace.queuedFollowups.value = []
    workspace.isActiveThreadLoading.value = false
    workspace.errorMessage.value = ''
    workspace.successMessage.value = ''
    workspace.status.value = 'idle'
    workspace.connect.mockResolvedValue(undefined)
    workspace.sendPrompt.mockResolvedValue(undefined)
    workspace.startEmbedThread.mockImplementation(async (cwd: string) => {
      workspace.activeThreadId.value = 'thread-created'
      workspace.activeThreadState.value = {
        ...(workspace.activeThreadState.value ?? {}),
        id: 'thread-created',
        cwd,
      }
      return {
        thread_id: 'thread-created',
        state: workspace.activeThreadState.value,
      }
    })
    workspace.resumeEmbedThread.mockImplementation(async (threadId: string) => {
      workspace.activeThreadId.value = threadId
      workspace.activeThreadState.value = {
        ...(workspace.activeThreadState.value ?? {}),
        id: threadId,
      }
      return true
    })
    workspace.setMode.mockImplementation(async (mode: 'build' | 'plan') => {
      workspace.activeMode.value = mode
      return true
    })
    workspace.setThreadGoal.mockImplementation(
      async (objective: string, tokenBudget?: number | null) => {
        workspace.activeThreadState.value = {
          ...(workspace.activeThreadState.value ?? {}),
          threadGoal: {
            objective,
            status: 'active',
            tokenBudget: tokenBudget ?? null,
          },
        }
      },
    )
    workspace.updateThreadGoalStatus.mockImplementation(async (status: string) => {
      const goal = workspace.activeThreadState.value?.threadGoal
      workspace.activeThreadState.value = {
        ...(workspace.activeThreadState.value ?? {}),
        threadGoal: goal ? { ...goal, status } : null,
      }
    })
    workspace.clearThreadGoal.mockImplementation(async () => {
      workspace.activeThreadState.value = {
        ...(workspace.activeThreadState.value ?? {}),
        threadGoal: null,
      }
    })
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
    await mountEmbed('/codex/embed?embed_token=secret')
    await sendHostMessage({
      type: 'yier:codex-start',
      cwd: '/tmp/embed',
      mode: 'plan',
    })

    expect(workspace.startEmbedThread).toHaveBeenCalledWith('/tmp/embed')
    expect(workspace.setMode).toHaveBeenCalledWith('plan')
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
    expect(workspace.sendPrompt).toHaveBeenCalledWith(
      expect.objectContaining({ prompt: 'Build a summary' }),
    )
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
        turnId: '',
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
        turnId: '',
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
        turnId: '',
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
    expect(postedMessages('yier:codex-command-result')).toContainEqual(
      expect.objectContaining({
        command: 'yier:codex-start',
        ok: false,
        message: 'mode must be build or plan.',
      }),
    )
  })

  it('sends a follow-up prompt after resuming a thread', async () => {
    workspace.activeThreadState.value = { id: 'thread-a', cwd: '/tmp/project' }
    await mountEmbed('/codex/embed?embed_token=secret')
    await sendHostMessage({ type: 'yier:codex-resume', threadId: 'thread-a', prompt: 'Continue' })

    expect(workspace.connect).toHaveBeenCalledOnce()
    expect(workspace.startEmbedThread).not.toHaveBeenCalled()
    expect(workspace.resumeEmbedThread).toHaveBeenCalledWith('thread-a')
    expect(workspace.sendPrompt).toHaveBeenCalledWith(
      expect.objectContaining({ prompt: 'Continue' }),
    )
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

  it('ignores operational URL parameters and waits for a parent message', async () => {
    await mountEmbed(
      '/codex/embed?cwd=/tmp/embed&thread_id=thread-a&mode=plan&prompt=ignored&embed_token=secret',
    )

    expect(workspace.connect).not.toHaveBeenCalled()
    expect(workspace.startEmbedThread).not.toHaveBeenCalled()
    expect(workspace.resumeEmbedThread).not.toHaveBeenCalled()
    expect(workspace.setMode).not.toHaveBeenCalled()
    expect(workspace.sendPrompt).not.toHaveBeenCalled()
    expect(window.parent.postMessage).toHaveBeenCalledWith({ type: 'yier:codex-ready' }, '*')
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

  it('returns a correlated result for parent commands', async () => {
    await mountEmbed('/codex/embed?embed_token=secret')
    await sendHostMessage({
      type: 'yier:codex-start',
      commandId: 'start-1',
      cwd: '/tmp/embed',
    })

    expect(window.parent.postMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'yier:codex-command-result',
        command: 'yier:codex-start',
        commandId: 'start-1',
        ok: true,
        threadId: 'thread-created',
      }),
      '*',
    )
  })

  it('applies an initial goal before sending the first prompt', async () => {
    await mountEmbed('/codex/embed?embed_token=secret')
    await sendHostMessage({
      type: 'yier:codex-start',
      cwd: '/tmp/embed',
      mode: 'plan',
      goal: {
        objective: 'Finish the migration',
        tokenBudget: 12_000,
      },
      prompt: 'Start with the API',
    })

    expect(workspace.setMode).toHaveBeenCalledWith('plan')
    expect(workspace.setThreadGoal).toHaveBeenCalledWith('Finish the migration', 12_000)
    expect(workspace.sendPrompt).toHaveBeenCalledWith(
      expect.objectContaining({ prompt: 'Start with the API' }),
    )
    expect(workspace.setThreadGoal.mock.invocationCallOrder[0]).toBeLessThan(
      workspace.sendPrompt.mock.invocationCallOrder[0] ?? Infinity,
    )
  })

  it('exposes goal lifecycle and active turn commands to the parent', async () => {
    await mountEmbed('/codex/embed?embed_token=secret')
    await sendHostMessage({ type: 'yier:codex-start', cwd: '/tmp/embed' })

    await sendHostMessage({
      type: 'yier:codex-set-goal',
      objective: 'Ship the feature',
      tokenBudget: 8_000,
    })
    await sendHostMessage({ type: 'yier:codex-update-goal-status', status: 'paused' })
    await sendHostMessage({ type: 'yier:codex-clear-goal' })
    await sendHostMessage({ type: 'yier:codex-set-mode', mode: 'plan' })
    await sendHostMessage({ type: 'yier:codex-steer-prompt', prompt: 'Check the tests first' })
    await sendHostMessage({ type: 'yier:codex-enqueue-followup', prompt: 'Then update docs' })
    await sendHostMessage({ type: 'yier:codex-remove-followup', messageId: 'followup-1' })
    await sendHostMessage({ type: 'yier:codex-interrupt-turn' })
    await sendHostMessage({ type: 'yier:codex-compact-thread' })
    await sendHostMessage({
      type: 'yier:codex-submit-user-input',
      requestId: 'request-1',
      response: { decision: 'accept' },
    })

    expect(workspace.setThreadGoal).toHaveBeenCalledWith('Ship the feature', 8_000)
    expect(workspace.updateThreadGoalStatus).toHaveBeenCalledWith('paused')
    expect(workspace.clearThreadGoal).toHaveBeenCalledOnce()
    expect(workspace.setMode).toHaveBeenCalledWith('plan')
    expect(workspace.steerPrompt).toHaveBeenCalledWith('Check the tests first')
    expect(workspace.enqueueFollowup).toHaveBeenCalledWith('Then update docs')
    expect(workspace.removeFollowup).toHaveBeenCalledWith('followup-1')
    expect(workspace.interruptTurn).toHaveBeenCalledOnce()
    expect(workspace.compactThread).toHaveBeenCalledOnce()
    expect(workspace.submitUserInputResponse).toHaveBeenCalledWith('request-1', {
      decision: 'accept',
    })
  })

  it('announces turn, goal, mode, and user-input state changes', async () => {
    await mountEmbed('/codex/embed?embed_token=secret')
    await sendHostMessage({ type: 'yier:codex-start', cwd: '/tmp/embed' })

    workspace.activeThreadState.value = {
      ...(workspace.activeThreadState.value ?? {}),
      turns: [
        {
          turnId: 'turn-1',
          status: 'inProgress',
          turnStartedAtMs: 100,
        },
      ],
      threadGoal: {
        objective: 'Ship the feature',
        status: 'active',
        tokenBudget: 8_000,
      },
    }
    workspace.activeMode.value = 'plan'
    workspace.activeUserInputRequest.value = {
      id: 'request-1',
      method: 'item/tool/requestUserInput',
      params: { questions: [{ id: 'confirm', question: 'Continue?' }] },
    }
    workspace.queuedFollowups.value = [{ id: 'followup-1', prompt: 'Update docs' }]
    await nextTick()

    expect(postedMessages('yier:codex-turn-state')).toContainEqual(
      expect.objectContaining({
        threadId: 'thread-created',
        mode: 'plan',
        turn: expect.objectContaining({ turnId: 'turn-1', status: 'inProgress' }),
      }),
    )
    expect(postedMessages('yier:codex-goal-state')).toContainEqual(
      expect.objectContaining({
        threadId: 'thread-created',
        goal: expect.objectContaining({ objective: 'Ship the feature', status: 'active' }),
      }),
    )
    expect(postedMessages('yier:codex-mode-changed')).toContainEqual({
      type: 'yier:codex-mode-changed',
      threadId: 'thread-created',
      mode: 'plan',
    })
    expect(postedMessages('yier:codex-user-input-request')).toContainEqual(
      expect.objectContaining({
        threadId: 'thread-created',
        request: expect.objectContaining({ id: 'request-1' }),
      }),
    )
    expect(postedMessages('yier:codex-followups-changed')).toContainEqual({
      type: 'yier:codex-followups-changed',
      threadId: 'thread-created',
      followups: [{ id: 'followup-1', prompt: 'Update docs' }],
    })
    for (const [message] of vi.mocked(window.parent.postMessage).mock.calls) {
      expect(() => structuredClone(message)).not.toThrow()
    }

    workspace.activeThreadState.value = {
      ...(workspace.activeThreadState.value ?? {}),
      turns: [{ turnId: 'turn-1', status: 'completed', durationMs: 2_000 }],
      threadGoal: {
        objective: 'Ship the feature',
        status: 'complete',
        tokenBudget: 8_000,
      },
    }
    await nextTick()

    expect(postedMessages('yier:codex-turn-state')).toContainEqual(
      expect.objectContaining({
        turn: expect.objectContaining({ turnId: 'turn-1', status: 'completed' }),
      }),
    )
    expect(postedMessages('yier:codex-goal-state')).toContainEqual(
      expect.objectContaining({
        goal: expect.objectContaining({ status: 'complete' }),
      }),
    )
  })

  it('forwards goal actions performed inside the iframe', async () => {
    const wrapper = await mountEmbed('/codex/embed?embed_token=secret')
    const chatPane = wrapper.getComponent({ name: 'CodexChatPane' })

    chatPane.vm.$emit('setThreadGoal', 'Finish locally', 4_000)
    chatPane.vm.$emit('updateThreadGoalStatus', 'complete')
    chatPane.vm.$emit('clearThreadGoal')
    await nextTick()

    expect(workspace.setThreadGoal).toHaveBeenCalledWith('Finish locally', 4_000)
    expect(workspace.updateThreadGoalStatus).toHaveBeenCalledWith('complete')
    expect(workspace.clearThreadGoal).toHaveBeenCalledOnce()
  })
})
