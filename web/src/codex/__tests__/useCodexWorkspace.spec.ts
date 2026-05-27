import { defineComponent, h, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

import {
  useCodexWorkspace,
  type CodexRealtimeClient,
  type UseCodexWorkspaceOptions,
} from '../composables/useCodexWorkspace'
import type { CodexClientCommand, CodexServerEvent, CodexSocketStatus, JsonRecord } from '../types'

type CommandCall = {
  type: CodexClientCommand
  payload: JsonRecord
}

class FakeCodexSocket implements CodexRealtimeClient {
  readonly commands: CommandCall[] = []
  readonly eventListeners = new Set<(event: CodexServerEvent) => void>()
  readonly statusListeners = new Set<(status: CodexSocketStatus) => void>()
  closed = false
  readonly forkedThread = {
    thread_id: 'thread-forked',
    title: 'Forked',
    preview: 'forked',
    updated_at: 30,
    started_at: 30,
    status: 'idle',
    cwd: '/tmp/alpha',
    project: 'alpha',
    project_path: '/tmp/alpha',
    source: 'appServer',
  }
  includeForkedThread = false

  async connect() {
    this.statusListeners.forEach((listener) => listener('open'))
    this.emit({ type: 'connection_ready', payload: { ok: true } })
  }

  close() {
    this.closed = true
  }

  async sendCommand<TPayload = unknown>(
    type: CodexClientCommand,
    payload: JsonRecord = {},
  ): Promise<TPayload> {
    this.commands.push({ type, payload })
    if (type === 'list_threads') {
      return {
        projects: [
          {
            project: 'alpha',
            project_path: '/tmp/alpha',
            session_count: 2,
            sessions: [
              {
                thread_id: 'thread-a',
                title: 'Alpha A',
                preview: 'first',
                updated_at: 20,
                started_at: 10,
                status: 'idle',
                cwd: '/tmp/alpha',
                project: 'alpha',
                project_path: '/tmp/alpha',
                source: 'appServer',
              },
              {
                thread_id: 'thread-b',
                title: 'Alpha B',
                preview: 'second',
                updated_at: 10,
                started_at: 5,
                status: 'idle',
                cwd: '/tmp/alpha',
                project: 'alpha',
                project_path: '/tmp/alpha',
                source: 'appServer',
              },
              ...(this.includeForkedThread ? [this.forkedThread] : []),
            ],
          },
        ],
        paired_editors: [],
      } as TPayload
    }
    if (type === 'subscribe_thread') {
      const threadId = String(payload.thread_id)
      return {
        thread_id: threadId,
        state: { id: threadId, turns: [], requests: [] },
        stream_role: null,
        queued_followups: [],
      } as TPayload
    }
    if (type === 'fork_thread') {
      this.includeForkedThread = true
      return {
        thread_id: this.forkedThread.thread_id,
        state: { id: this.forkedThread.thread_id, turns: [], requests: [] },
      } as TPayload
    }
    if (type === 'start_thread') {
      return {
        thread_id: 'thread-created',
        state: {
          id: 'thread-created',
          turns: [],
          requests: [],
          cwd: String(payload.project_path ?? '/tmp/created'),
        },
      } as TPayload
    }
    return { ok: true } as TPayload
  }

  onEvent(listener: (event: CodexServerEvent) => void) {
    this.eventListeners.add(listener)
    return () => this.eventListeners.delete(listener)
  }

  onStatus(listener: (status: CodexSocketStatus) => void) {
    this.statusListeners.add(listener)
    return () => this.statusListeners.delete(listener)
  }

  emit(event: CodexServerEvent) {
    this.eventListeners.forEach((listener) => listener(event))
  }
}

function mountHarness(
  socket: FakeCodexSocket,
  options: Omit<UseCodexWorkspaceOptions, 'socket'> = {},
) {
  const holder: { workspace?: ReturnType<typeof useCodexWorkspace> } = {}
  const wrapper = mount(
    defineComponent({
      setup() {
        holder.workspace = useCodexWorkspace({ socket, ...options })
        return () => h('div')
      },
    }),
  )
  if (!holder.workspace) {
    throw new Error('Expected Codex workspace harness to initialize.')
  }
  return { wrapper, workspace: holder.workspace }
}

describe('useCodexWorkspace', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('subscribes to one visible thread while leaving server sessions alive', async () => {
    const socket = new FakeCodexSocket()
    const { wrapper, workspace } = mountHarness(socket)
    await flushPromises()

    expect(workspace.activeThreadId.value).toBe('thread-a')
    expect(socket.commands.map((command) => command.type)).toEqual([
      'list_threads',
      'subscribe_thread',
    ])

    await workspace.selectThread('thread-b')
    await flushPromises()

    expect(socket.commands.slice(-2)).toEqual([
      { type: 'unsubscribe_thread', payload: { thread_id: 'thread-a' } },
      { type: 'subscribe_thread', payload: { thread_id: 'thread-b' } },
    ])
    expect(socket.closed).toBe(false)

    socket.emit({
      type: 'thread_state',
      payload: {
        thread_id: 'thread-b',
        state: {
          id: 'thread-b',
          turns: [{ turnId: 'turn-1', status: 'inProgress', items: [] }],
          requests: [],
        },
        queued_followups: [],
      },
    })
    await nextTick()

    expect(workspace.activeStatus.value).toBe('inProgress')
    expect(workspace.isActiveTurnInProgress.value).toBe(true)
  })

  it('sends user input responses through the websocket command envelope', async () => {
    const socket = new FakeCodexSocket()
    const { workspace } = mountHarness(socket)
    await flushPromises()

    await workspace.submitUserInputResponse('request-1', {
      answers: { mode: { answers: ['Plan'] } },
    })

    expect(socket.commands[socket.commands.length - 1]).toEqual({
      type: 'submit_user_input_response',
      payload: {
        thread_id: 'thread-a',
        request_id: 'request-1',
        response: { answers: { mode: { answers: ['Plan'] } } },
      },
    })
  })

  it('sends model and reasoning overrides with prompt submissions', async () => {
    const socket = new FakeCodexSocket()
    const { workspace } = mountHarness(socket)
    await flushPromises()

    await workspace.sendPrompt({
      prompt: 'Use the selected model',
      model: 'gpt-5.4-mini',
      reasoningEffort: 'high',
    })

    expect(socket.commands[socket.commands.length - 1]).toEqual({
      type: 'send_prompt',
      payload: {
        thread_id: 'thread-a',
        prompt: 'Use the selected model',
        collaboration_mode: {
          mode: 'default',
          settings: {
            model: 'gpt-5.4-mini',
            reasoning_effort: 'high',
            developer_instructions: null,
          },
        },
      },
    })
  })

  it('implements plan requests by returning to build mode and sending the plan prompt', async () => {
    const socket = new FakeCodexSocket()
    const { workspace } = mountHarness(socket)
    await flushPromises()

    socket.emit({
      type: 'thread_state',
      payload: {
        thread_id: 'thread-a',
        state: {
          id: 'thread-a',
          latestCollaborationMode: {
            mode: 'plan',
            settings: {
              model: 'gpt-5.4',
              reasoning_effort: 'medium',
              developer_instructions: null,
            },
          },
          latestModel: 'gpt-5.4',
          latestReasoningEffort: 'medium',
          turns: [],
          requests: [
            {
              id: 'implement-plan:turn-1',
              method: 'item/plan/requestImplementation',
              params: {
                threadId: 'thread-a',
                turnId: 'turn-1',
                planContent: '1. Update composer\n2. Add tests',
              },
            },
          ],
        },
        queued_followups: [],
      },
    })
    await nextTick()

    expect(workspace.activeMode.value).toBe('plan')
    expect(workspace.activeUserInputRequest.value?.method).toBe('item/plan/requestImplementation')

    await workspace.submitUserInputResponse('implement-plan:turn-1', {
      decision: 'accept',
      planContent: '1. Update composer\n2. Add tests',
    })

    const defaultCollaborationMode = {
      mode: 'default',
      settings: {
        model: 'gpt-5.4',
        reasoning_effort: 'medium',
        developer_instructions: null,
      },
    }
    expect(socket.commands.slice(-2)).toEqual([
      {
        type: 'set_collaboration_mode',
        payload: {
          thread_id: 'thread-a',
          collaboration_mode: defaultCollaborationMode,
        },
      },
      {
        type: 'send_prompt',
        payload: {
          thread_id: 'thread-a',
          prompt: 'PLEASE IMPLEMENT THIS PLAN:\n1. Update composer\n2. Add tests',
          collaboration_mode: defaultCollaborationMode,
        },
      },
    ])
    expect(workspace.activeMode.value).toBe('build')
    expect(workspace.activeUserInputRequest.value).toBeNull()
  })

  it('forks a thread, refreshes the workspace, and selects the forked thread', async () => {
    const socket = new FakeCodexSocket()
    const { workspace } = mountHarness(socket)
    await flushPromises()

    await workspace.forkThread('thread-a')
    await flushPromises()

    expect(socket.commands.slice(-4)).toEqual([
      { type: 'fork_thread', payload: { thread_id: 'thread-a' } },
      { type: 'list_threads', payload: {} },
      { type: 'unsubscribe_thread', payload: { thread_id: 'thread-a' } },
      { type: 'subscribe_thread', payload: { thread_id: 'thread-forked' } },
    ])
    expect(workspace.activeThreadId.value).toBe('thread-forked')
    expect(workspace.successMessage.value).toBe('Thread forked.')
    expect(workspace.forkingThreadId.value).toBe('')
  })

  it('starts embed threads without using the persisted active thread', async () => {
    localStorage.setItem('yier.codex.active-thread-id', 'thread-a')
    const socket = new FakeCodexSocket()
    const { workspace } = mountHarness(socket, {
      persistActiveThread: false,
      selectInitialThread: false,
    })
    await flushPromises()

    expect(workspace.activeThreadId.value).toBe('')

    await workspace.startEmbedThread('/tmp/embed')
    await flushPromises()

    expect(socket.commands.slice(-3)).toEqual([
      { type: 'start_thread', payload: { project_path: '/tmp/embed' } },
      { type: 'list_threads', payload: {} },
      { type: 'subscribe_thread', payload: { thread_id: 'thread-created' } },
    ])
    expect(workspace.activeThreadId.value).toBe('thread-created')
    expect(localStorage.getItem('yier.codex.active-thread-id')).toBe('thread-a')
  })

  it('resumes embed threads by subscribing to the requested thread', async () => {
    const socket = new FakeCodexSocket()
    const { workspace } = mountHarness(socket, {
      persistActiveThread: false,
      selectInitialThread: false,
    })
    await flushPromises()

    await workspace.resumeEmbedThread('thread-b')
    await flushPromises()

    expect(socket.commands.slice(-1)).toEqual([
      { type: 'subscribe_thread', payload: { thread_id: 'thread-b' } },
    ])
    expect(workspace.activeThreadId.value).toBe('thread-b')
  })
})
