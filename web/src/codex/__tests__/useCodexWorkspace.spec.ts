import { defineComponent, h, nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

import { useCodexWorkspace, type CodexRealtimeClient } from '../composables/useCodexWorkspace'
import type {
  CodexClientCommand,
  CodexServerEvent,
  CodexSocketStatus,
  JsonRecord,
} from '../types'

type CommandCall = {
  type: CodexClientCommand
  payload: JsonRecord
}

class FakeCodexSocket implements CodexRealtimeClient {
  readonly commands: CommandCall[] = []
  readonly eventListeners = new Set<(event: CodexServerEvent) => void>()
  readonly statusListeners = new Set<(status: CodexSocketStatus) => void>()
  closed = false

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

function mountHarness(socket: FakeCodexSocket) {
  const holder: { workspace?: ReturnType<typeof useCodexWorkspace> } = {}
  const wrapper = mount(
    defineComponent({
      setup() {
        holder.workspace = useCodexWorkspace({ socket })
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
})
