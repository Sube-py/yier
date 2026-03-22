import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import PrimeVue from 'primevue/config'
import Aura from '@primeuix/themes/aura'

import App from '../App.vue'
import { createTestRouter } from '../router'

class MockEventSource {
  static instances: MockEventSource[] = []

  readonly listeners = new Map<string, Set<(event: MessageEvent<string>) => void>>()
  onerror: ((event: Event) => void) | null = null

  constructor(readonly url: string) {
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    const bucket = this.listeners.get(type) ?? new Set<(event: MessageEvent<string>) => void>()
    bucket.add(listener)
    this.listeners.set(type, bucket)
  }

  close() {}

  emit(type: string, data: unknown) {
    const event = { data: JSON.stringify(data) } as MessageEvent<string>
    this.listeners.get(type)?.forEach((listener) => listener(event))
  }

  static reset() {
    MockEventSource.instances = []
  }
}

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

function sseResponse(frames: string) {
  return Promise.resolve(
    new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(frames))
          controller.close()
        },
      }),
      {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      },
    ),
  )
}

function isSessionListRequest(path: string, init?: RequestInit) {
  return path.endsWith('/api/chat/sessions') && (!init?.method || init.method === 'GET')
}

function isSessionCreateRequest(path: string, init?: RequestInit) {
  return path.endsWith('/api/chat/sessions') && init?.method === 'POST'
}

function shellCommandRaw(overrides: Record<string, unknown> = {}) {
  return {
    kind: 'shell_command',
    request: {
      command: 'printf "hello"',
      cwd: '/tmp/project',
      allow_shell: true,
      shell_program: '/bin/bash',
      timeout_seconds: 30,
      max_output_chars: 8000,
    },
    process: {
      session_id: null,
      state: 'completed',
      exit_code: 0,
      started_at: 1,
      finished_at: 2,
      runtime_seconds: 1,
      timed_out: false,
    },
    events: [
      { index: 0, timestamp: 1, type: 'started', command: 'printf "hello"', cwd: '/tmp/project' },
      { index: 1, timestamp: 2, type: 'stdout', text: 'hello', stream: 'stdout' },
      { index: 2, timestamp: 2, type: 'state_changed', state: 'completed' },
      { index: 3, timestamp: 2, type: 'exit', exit_code: 0, state: 'completed', timed_out: false },
    ],
    latest_event_index: 3,
    streams: {
      stdout: { text: 'hello', truncated: false },
      stderr: { text: '', truncated: false },
    },
    events_truncated: false,
    dropped_event_count: 0,
    ...overrides,
  }
}

function backgroundCommandRaw(overrides: Record<string, unknown> = {}) {
  return {
    kind: 'background_command',
    request: {
      command: 'npm run dev',
      cwd: '/tmp/project',
      allow_shell: true,
      shell_program: '/bin/bash',
    },
    process: {
      session_id: 'bg-1',
      state: 'running',
      exit_code: null,
      started_at: 1,
      finished_at: null,
      runtime_seconds: 3,
      timed_out: false,
    },
    events: [
      { index: 0, timestamp: 1, type: 'started', command: 'npm run dev', cwd: '/tmp/project' },
      { index: 1, timestamp: 2, type: 'stdout', text: 'ready on http://localhost:3000', stream: 'stdout' },
    ],
    latest_event_index: 1,
    streams: {
      stdout: { text: 'ready on http://localhost:3000', truncated: false },
      stderr: { text: '', truncated: false },
    },
    events_truncated: false,
    dropped_event_count: 0,
    ...overrides,
  }
}

async function mountApp(initialPath = '/chat') {
  const router = createTestRouter()
  await router.push(initialPath)
  await router.isReady()

  const wrapper = mount(App, {
    global: {
      plugins: [router, [PrimeVue, { theme: { preset: Aura } }]],
    },
  })
  await nextTick()
  return wrapper
}

describe('App', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
    MockEventSource.reset()
    vi.stubGlobal(
      'ResizeObserver',
      class ResizeObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    )
    vi.stubGlobal('EventSource', MockEventSource)
  })

  it('shows the setup empty state when the llm is not configured', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const path = input.toString()
        if (path.endsWith('/api/health')) {
          return jsonResponse({
            frontend: { ready: true, mode: 'static' },
            llm: { ready: false, detail: 'Needs setup' },
            mcp: { ready: true, runtime: {} },
            allowed_roots: ['/tmp/project'],
          })
        }
        if (path.endsWith('/api/config')) {
          return jsonResponse({
            llm: { base_url: '', model: '', has_api_key: false },
            allowed_roots: ['/tmp/project'],
            mcp_runtime: {},
          })
        }
        if (path.endsWith('/api/config/mcp')) {
          return jsonResponse({ mcp_servers: {}, runtime: {} })
        }
        if (isSessionListRequest(path, init)) {
          return jsonResponse({ sessions: [] })
        }
        if (isSessionCreateRequest(path, init)) {
          return jsonResponse({ session_id: 'session-1' }, 201)
        }
        throw new Error(`Unexpected request: ${path}`)
      }),
    )

    const wrapper = await mountApp()
    await flushPromises()

    expect(wrapper.text()).toContain('Configure the LLM connection before sending messages.')
    expect(wrapper.text()).toContain('session-1'.slice(0, 8))
  })

  it('starts a fresh session when clicking New Chat', async () => {
    let sessionCounter = 0
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = input.toString()
      if (path.endsWith('/api/health')) {
        return jsonResponse({
          frontend: { ready: true, mode: 'static' },
          llm: { ready: true },
          mcp: { ready: true, runtime: {} },
          allowed_roots: ['/tmp/project'],
        })
      }
      if (path.endsWith('/api/config')) {
        return jsonResponse({
          llm: { base_url: 'https://example.test', model: 'demo', has_api_key: true },
          allowed_roots: ['/tmp/project'],
          mcp_runtime: {},
        })
      }
      if (path.endsWith('/api/config/mcp')) {
        return jsonResponse({ mcp_servers: {}, runtime: {} })
      }
      if (isSessionListRequest(path, init)) {
        return jsonResponse({ sessions: [] })
      }
      if (isSessionCreateRequest(path, init)) {
        sessionCounter += 1
        return jsonResponse({ session_id: `session-${sessionCounter}` }, 201)
      }
      if (path.includes('/api/chat/sessions/')) {
        return jsonResponse({ session_id: 'session-1', messages: [] })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const wrapper = await mountApp()
    await flushPromises()

    const buttons = wrapper.findAll('button')
    const newChatButton = buttons.find((item) => item.text().includes('New Chat'))
    expect(newChatButton).toBeTruthy()
    await newChatButton!.trigger('click')
    await flushPromises()

    expect(sessionCounter).toBe(2)
    expect(wrapper.text()).toContain('Started a fresh session.')
  })

  it('renders recent sessions and switches to the selected transcript', async () => {
    localStorage.setItem('yier.active-session-id', 'session-1')

    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const path = input.toString()
        if (path.endsWith('/api/health')) {
          return jsonResponse({
            frontend: { ready: true, mode: 'static' },
            llm: { ready: true },
            mcp: { ready: true, runtime: {} },
            allowed_roots: ['/tmp/project'],
          })
        }
        if (path.endsWith('/api/config')) {
          return jsonResponse({
            llm: { base_url: 'https://example.test', model: 'demo', has_api_key: true },
            allowed_roots: ['/tmp/project'],
            mcp_runtime: {},
          })
        }
        if (path.endsWith('/api/config/mcp')) {
          return jsonResponse({ mcp_servers: {}, runtime: {} })
        }
        if (isSessionListRequest(path, init)) {
          return jsonResponse({
            sessions: [
              {
                session_id: 'session-1',
                title: 'First session',
                preview: 'first preview',
                updated_at: 100,
                message_count: 2,
              },
              {
                session_id: 'session-2',
                title: 'Second session',
                preview: 'second preview',
                updated_at: 200,
                message_count: 2,
              },
            ],
          })
        }
        if (path.endsWith('/api/chat/sessions/session-1')) {
          return jsonResponse({
            session_id: 'session-1',
            messages: [
              { role: 'user', content: 'open first' },
              { role: 'assistant', content: 'first transcript body' },
            ],
            activity_events: [],
          })
        }
        if (path.endsWith('/api/chat/sessions/session-2')) {
          return jsonResponse({
            session_id: 'session-2',
            messages: [
              { role: 'user', content: 'open second' },
              { role: 'assistant', content: 'second transcript body' },
            ],
            activity_events: [],
          })
        }
        if (isSessionCreateRequest(path, init)) {
          return jsonResponse({ session_id: 'session-created' }, 201)
        }
        throw new Error(`Unexpected request: ${path}`)
      }),
    )

    const wrapper = await mountApp()
    await flushPromises()

    expect(wrapper.text()).toContain('First session')
    expect(wrapper.text()).toContain('Second session')
    expect(wrapper.text()).toContain('first transcript body')

    const historyButtons = wrapper.findAll('.session-history-main')
    expect(historyButtons).toHaveLength(2)
    await historyButtons[1]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('second transcript body')
    expect(wrapper.find('.session-history-item--active').text()).toContain('Second session')
  })

  it('deletes the active session and switches to the next saved session', async () => {
    localStorage.setItem('yier.active-session-id', 'session-1')

    let sessions = [
      {
        session_id: 'session-1',
        title: 'First session',
        preview: 'first preview',
        updated_at: 200,
        message_count: 2,
      },
      {
        session_id: 'session-2',
        title: 'Second session',
        preview: 'second preview',
        updated_at: 100,
        message_count: 2,
      },
    ]
    const deletedSessionIds: string[] = []

    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const path = input.toString()
        if (path.endsWith('/api/health')) {
          return jsonResponse({
            frontend: { ready: true, mode: 'static' },
            llm: { ready: true },
            mcp: { ready: true, runtime: {} },
            allowed_roots: ['/tmp/project'],
          })
        }
        if (path.endsWith('/api/config')) {
          return jsonResponse({
            llm: { base_url: 'https://example.test', model: 'demo', has_api_key: true },
            allowed_roots: ['/tmp/project'],
            mcp_runtime: {},
          })
        }
        if (path.endsWith('/api/config/mcp')) {
          return jsonResponse({ mcp_servers: {}, runtime: {} })
        }
        if (isSessionListRequest(path, init)) {
          return jsonResponse({ sessions })
        }
        if (path.endsWith('/api/chat/sessions/session-1') && init?.method === 'DELETE') {
          deletedSessionIds.push('session-1')
          sessions = sessions.filter((session) => session.session_id !== 'session-1')
          return jsonResponse({ session_id: 'session-1', deleted: true })
        }
        if (path.endsWith('/api/chat/sessions/session-1')) {
          return jsonResponse({
            session_id: 'session-1',
            messages: [
              { role: 'user', content: 'open first' },
              { role: 'assistant', content: 'first transcript body' },
            ],
            activity_events: [],
          })
        }
        if (path.endsWith('/api/chat/sessions/session-2')) {
          return jsonResponse({
            session_id: 'session-2',
            messages: [
              { role: 'user', content: 'open second' },
              { role: 'assistant', content: 'second transcript body' },
            ],
            activity_events: [],
          })
        }
        if (isSessionCreateRequest(path, init)) {
          return jsonResponse({ session_id: 'session-created' }, 201)
        }
        throw new Error(`Unexpected request: ${path}`)
      }),
    )

    const wrapper = await mountApp()
    await flushPromises()

    const deleteButtons = wrapper.findAll('.session-history-delete')
    expect(deleteButtons).toHaveLength(2)
    await deleteButtons[0]!.trigger('click')
    await flushPromises()

    expect(deletedSessionIds).toEqual(['session-1'])
    expect(wrapper.text()).toContain('Session deleted.')
    expect(wrapper.text()).toContain('second transcript body')
    expect(wrapper.find('.session-history-item--active').text()).toContain('Second session')
    expect(wrapper.findAll('.session-history-item')).toHaveLength(1)
  })

  it('restores persisted tool activities for the active session after refresh', async () => {
    localStorage.setItem('yier.active-session-id', 'session-1')

    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const path = input.toString()
        if (path.endsWith('/api/health')) {
          return jsonResponse({
            frontend: { ready: true, mode: 'static' },
            llm: { ready: true },
            mcp: { ready: true, runtime: {} },
            allowed_roots: ['/tmp/project'],
          })
        }
        if (path.endsWith('/api/config')) {
          return jsonResponse({
            llm: { base_url: 'https://example.test', model: 'demo', has_api_key: true },
            allowed_roots: ['/tmp/project'],
            mcp_runtime: {},
          })
        }
        if (path.endsWith('/api/config/mcp')) {
          return jsonResponse({ mcp_servers: {}, runtime: {} })
        }
        if (isSessionListRequest(path, init)) {
          return jsonResponse({
            sessions: [
              {
                session_id: 'session-1',
                title: 'run something',
                preview: 'Done.',
                updated_at: 123,
                message_count: 2,
              },
            ],
          })
        }
        if (path.includes('/api/chat/sessions/')) {
          return jsonResponse({
            session_id: 'session-1',
            messages: [
              { role: 'user', content: 'run something' },
              { role: 'assistant', content: 'Done.' },
            ],
            activity_events: [
              {
                event: 'tool_call_start',
                data: {
                  session_id: 'session-1',
                  tool_name: 'run_command',
                  tool_call_id: 'call-1',
                  arguments: {
                    command: 'printf "hello"',
                    cwd: '/tmp/project',
                  },
                  iteration: 1,
                },
              },
              {
                event: 'command_start',
                data: {
                  session_id: 'session-1',
                  tool_call_id: 'call-1',
                  tool_name: 'run_command',
                  command: 'printf "hello"',
                  cwd: '/tmp/project',
                  is_background: false,
                },
              },
              {
                event: 'command_output',
                data: {
                  session_id: 'session-1',
                  tool_call_id: 'call-1',
                  tool_name: 'run_command',
                  stream: 'stdout',
                  content: 'hello',
                  is_background: false,
                },
              },
              {
                event: 'tool_call_end',
                data: {
                  session_id: 'session-1',
                  tool_name: 'run_command',
                  tool_call_id: 'call-1',
                  result: 'Command finished successfully.',
                  is_error: false,
                  iteration: 1,
                  raw: shellCommandRaw(),
                },
              },
            ],
          })
        }
        throw new Error(`Unexpected request: ${path}`)
      }),
    )

    const wrapper = await mountApp()
    await flushPromises()

    expect(wrapper.text()).toContain('Shell command')
    expect(wrapper.text()).toContain('printf "hello"')
    expect(wrapper.text()).toContain('hello')
  })

  it('renders a streamed assistant reply after submitting a message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const path = input.toString()
        if (path.endsWith('/api/health')) {
          return jsonResponse({
            frontend: { ready: true, mode: 'static' },
            llm: { ready: true },
            mcp: { ready: true, runtime: {} },
            allowed_roots: ['/tmp/project'],
          })
        }
        if (path.endsWith('/api/config')) {
          return jsonResponse({
            llm: { base_url: 'https://example.test', model: 'demo', has_api_key: true },
            allowed_roots: ['/tmp/project'],
            mcp_runtime: {},
          })
        }
        if (path.endsWith('/api/config/mcp')) {
          return jsonResponse({ mcp_servers: {}, runtime: {} })
        }
        if (isSessionListRequest(path, init)) {
          return jsonResponse({ sessions: [] })
        }
        if (isSessionCreateRequest(path, init)) {
          return jsonResponse({ session_id: 'session-1' }, 201)
        }
        if (path.includes('/api/chat/sessions/')) {
          return jsonResponse({ session_id: 'session-1', messages: [] })
        }
        if (path.endsWith('/api/chat/stream') && init?.method === 'POST') {
          return sseResponse(
            [
              'event: run_started',
              'data: {"session_id":"session-1"}',
              '',
              'event: tool_call_start',
              'data: {"session_id":"session-1","tool_name":"run_command","tool_call_id":"call-1","arguments":{"command":"printf \\"hello\\"","cwd":"."},"iteration":1}',
              '',
              'event: command_start',
              'data: {"session_id":"session-1","tool_call_id":"call-1","tool_name":"run_command","command":"printf \\"hello\\"","cwd":"/tmp/project","is_background":false}',
              '',
              'event: command_output',
              'data: {"session_id":"session-1","tool_call_id":"call-1","tool_name":"run_command","stream":"stdout","content":"hello","is_background":false}',
              '',
              'event: command_end',
              'data: {"session_id":"session-1","tool_call_id":"call-1","tool_name":"run_command","command":"printf \\"hello\\"","cwd":"/tmp/project","exit_code":0,"timed_out":false,"is_background":false}',
              '',
              'event: tool_call_end',
              `data: ${JSON.stringify({
                session_id: 'session-1',
                tool_name: 'run_command',
                tool_call_id: 'call-1',
                result: 'Command finished successfully.',
                is_error: false,
                iteration: 1,
                metadata: {
                  command: 'printf "hello"',
                  cwd: '/tmp/project',
                  exit_code: 0,
                },
                raw: shellCommandRaw(),
              })}`,
              '',
              'event: assistant_message',
              'data: {"session_id":"session-1","content":"I found the **project files**.\\n\\n```ts\\nconsole.log(1)\\n```","iteration":1}',
              '',
              'event: done',
              'data: {"session_id":"session-1","finish_reason":"stop"}',
              '',
            ].join('\r\n'),
          )
        }
        throw new Error(`Unexpected request: ${path}`)
      }),
    )

    const wrapper = await mountApp()
    await flushPromises()

    const textarea = wrapper.get('textarea')
    await textarea.setValue('List the project files')
    const buttons = wrapper.findAll('button')
    const sendButton = buttons.find((item) => item.text().includes('Send'))
    expect(sendButton).toBeTruthy()
    await sendButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('List the project files')
    expect(wrapper.html()).toContain('<strong>project files</strong>')
    expect(wrapper.html()).toContain('<pre><code class="language-ts">')
    expect(wrapper.text()).toContain('Shell command')
    expect(wrapper.text()).toContain('printf "hello"')
    expect(wrapper.text()).toContain('/tmp/project')
    expect(wrapper.text()).toContain('hello')
    expect(wrapper.text()).toContain('1s')
    expect(wrapper.text()).not.toContain('Iteration 1')
    expect(wrapper.text()).not.toContain('Exit 0')
    expect(wrapper.text()).not.toContain('#3 exit 0 (completed)')
    expect(wrapper.findAll('.activity-item')).toHaveLength(1)
  })

  it('renders settings in the shared workspace via routing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const path = input.toString()
        if (path.endsWith('/api/health')) {
          return jsonResponse({
            frontend: { ready: true, mode: 'static' },
            llm: { ready: true },
            mcp: { ready: true, runtime: {} },
            allowed_roots: ['/tmp/project'],
          })
        }
        if (path.endsWith('/api/config')) {
          return jsonResponse({
            llm: { base_url: 'https://example.test', model: 'demo', has_api_key: true },
            allowed_roots: ['/tmp/project'],
            mcp_runtime: {},
          })
        }
        if (path.endsWith('/api/config/mcp')) {
          return jsonResponse({ mcp_servers: {}, runtime: {} })
        }
        if (isSessionListRequest(path, init)) {
          return jsonResponse({ sessions: [] })
        }
        if (isSessionCreateRequest(path, init)) {
          return jsonResponse({ session_id: 'session-1' }, 201)
        }
        throw new Error(`Unexpected request: ${path}`)
      }),
    )

    const wrapper = await mountApp('/settings')
    await flushPromises()

    expect(wrapper.text()).toContain('Local console settings')
    expect(wrapper.text()).toContain('Save LLM Settings')
    expect(wrapper.text()).toContain('Adjust the assistant without leaving the main console')
  })

  it('renders background updates from the persistent event stream', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const path = input.toString()
        if (path.endsWith('/api/health')) {
          return jsonResponse({
            frontend: { ready: true, mode: 'static' },
            llm: { ready: true },
            mcp: { ready: true, runtime: {} },
            allowed_roots: ['/tmp/project'],
          })
        }
        if (path.endsWith('/api/config')) {
          return jsonResponse({
            llm: { base_url: 'https://example.test', model: 'demo', has_api_key: true },
            allowed_roots: ['/tmp/project'],
            mcp_runtime: {},
          })
        }
        if (path.endsWith('/api/config/mcp')) {
          return jsonResponse({ mcp_servers: {}, runtime: {} })
        }
        if (isSessionListRequest(path, init)) {
          return jsonResponse({ sessions: [] })
        }
        if (isSessionCreateRequest(path, init)) {
          return jsonResponse({ session_id: 'session-1' }, 201)
        }
        if (path.includes('/api/chat/sessions/')) {
          return jsonResponse({ session_id: 'session-1', messages: [] })
        }
        throw new Error(`Unexpected request: ${path}`)
      }),
    )

    const wrapper = await mountApp()
    await flushPromises()

    const eventSource = MockEventSource.instances[0]
    expect(eventSource).toBeTruthy()
    if (!eventSource) {
      throw new Error('Expected persistent EventSource connection.')
    }

    eventSource.emit('background_command_started', {
      session_id: 'session-1',
      background_session_id: 'bg-1',
      tool_call_id: 'call-1',
      tool_name: 'start_background_command',
      command: 'npm run dev',
      cwd: '/tmp/project',
      state: 'running',
    })
    eventSource.emit('background_command_output', {
      session_id: 'session-1',
      background_session_id: 'bg-1',
      command: 'npm run dev',
      cwd: '/tmp/project',
      stream: 'stdout',
      content: 'ready on http://localhost:3000',
    })
    eventSource.emit('background_command_end', {
      session_id: 'session-1',
      background_session_id: 'bg-1',
      command: 'npm run dev',
      cwd: '/tmp/project',
      state: 'completed',
      exit_code: 0,
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Background bg-1')
    expect(wrapper.text()).toContain('npm run dev')
    expect(wrapper.text()).toContain('ready on http://localhost:3000')
  })

  it('merges background shell tool updates into one activity', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const path = input.toString()
        if (path.endsWith('/api/health')) {
          return jsonResponse({
            frontend: { ready: true, mode: 'static' },
            llm: { ready: true },
            mcp: { ready: true, runtime: {} },
            allowed_roots: ['/tmp/project'],
          })
        }
        if (path.endsWith('/api/config')) {
          return jsonResponse({
            llm: { base_url: 'https://example.test', model: 'demo', has_api_key: true },
            allowed_roots: ['/tmp/project'],
            mcp_runtime: {},
          })
        }
        if (path.endsWith('/api/config/mcp')) {
          return jsonResponse({ mcp_servers: {}, runtime: {} })
        }
        if (isSessionListRequest(path, init)) {
          return jsonResponse({ sessions: [] })
        }
        if (isSessionCreateRequest(path, init)) {
          return jsonResponse({ session_id: 'session-1' }, 201)
        }
        if (path.includes('/api/chat/sessions/')) {
          return jsonResponse({ session_id: 'session-1', messages: [] })
        }
        throw new Error(`Unexpected request: ${path}`)
      }),
    )

    const wrapper = await mountApp()
    await flushPromises()

    const eventSource = MockEventSource.instances[0]
    expect(eventSource).toBeTruthy()
    if (!eventSource) {
      throw new Error('Expected persistent EventSource connection.')
    }

    eventSource.emit('tool_call_start', {
      session_id: 'session-1',
      tool_name: 'start_background_command',
      tool_call_id: 'call-start',
      arguments: {
        command: 'npm run dev',
        cwd: '/tmp/project',
      },
      iteration: 1,
    })
    eventSource.emit('background_command_started', {
      session_id: 'session-1',
      background_session_id: 'bg-1',
      tool_call_id: 'call-start',
      tool_name: 'start_background_command',
      command: 'npm run dev',
      cwd: '/tmp/project',
      state: 'running',
    })
    eventSource.emit('tool_call_end', {
      session_id: 'session-1',
      tool_name: 'start_background_command',
      tool_call_id: 'call-start',
      result: 'Started background command bg-1',
      is_error: false,
      iteration: 1,
      metadata: {
        session_id: 'bg-1',
        command: 'npm run dev',
      },
      raw: backgroundCommandRaw(),
    })
    eventSource.emit('tool_call_start', {
      session_id: 'session-1',
      tool_name: 'read_background_command',
      tool_call_id: 'call-read',
      arguments: {
        session_id: 'bg-1',
      },
      iteration: 2,
    })
    eventSource.emit('tool_call_end', {
      session_id: 'session-1',
      tool_name: 'read_background_command',
      tool_call_id: 'call-read',
      result: 'Read background output.',
      is_error: false,
      iteration: 2,
      metadata: {
        session_id: 'bg-1',
        state: 'running',
      },
      raw: backgroundCommandRaw({
        process: {
          session_id: 'bg-1',
          state: 'running',
          exit_code: null,
          started_at: 1,
          finished_at: null,
          runtime_seconds: 5,
          timed_out: false,
        },
        events: [
          { index: 0, timestamp: 1, type: 'started', command: 'npm run dev', cwd: '/tmp/project' },
          { index: 1, timestamp: 2, type: 'stdout', text: 'ready on http://localhost:3000', stream: 'stdout' },
          { index: 2, timestamp: 3, type: 'stdin', text: 'rs\n', append_newline: true },
        ],
        latest_event_index: 2,
      }),
    })
    await flushPromises()

    expect(wrapper.findAll('.activity-item')).toHaveLength(1)
    expect(wrapper.text()).toContain('Background bg-1')
    expect(wrapper.text()).toContain('/tmp/project')
    expect(wrapper.text()).toContain('ready on http://localhost:3000')
    expect(wrapper.text()).toContain('5s')
    expect(wrapper.text()).not.toContain('Iteration 1')
    expect(wrapper.text()).not.toContain('#2 stdin rs')
  })

  it('saves allowed roots from the settings workspace', async () => {
    let savedRoots: string[] = ['/tmp/project', '/tmp/docs']

    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const path = input.toString()
        if (path.endsWith('/api/health')) {
          return jsonResponse({
            frontend: { ready: true, mode: 'static' },
            llm: { ready: true },
            mcp: { ready: true, runtime: {} },
            allowed_roots: ['/tmp/project'],
          })
        }
        if (path.endsWith('/api/config') && (!init || init.method === 'GET')) {
          return jsonResponse({
            llm: { base_url: 'https://example.test', model: 'demo', has_api_key: true },
            allowed_roots: savedRoots,
            mcp_runtime: {},
          })
        }
        if (path.endsWith('/api/config/roots') && init?.method === 'PUT') {
          const payload = JSON.parse(String(init.body)) as { allowed_roots: string[] }
          savedRoots = payload.allowed_roots
          return jsonResponse({
            llm: { base_url: 'https://example.test', model: 'demo', has_api_key: true },
            allowed_roots: savedRoots,
            mcp_runtime: {},
          })
        }
        if (path.endsWith('/api/config/mcp')) {
          return jsonResponse({ mcp_servers: {}, runtime: {} })
        }
        if (isSessionListRequest(path, init)) {
          return jsonResponse({ sessions: [] })
        }
        if (isSessionCreateRequest(path, init)) {
          return jsonResponse({ session_id: 'session-1' }, 201)
        }
        throw new Error(`Unexpected request: ${path}`)
      }),
    )

    const wrapper = await mountApp('/settings')
    await flushPromises()

    const workspaceTab = wrapper.findAll('[role="tab"]').find((item) => item.text().includes('Workspace'))
    expect(workspaceTab).toBeTruthy()
    await workspaceTab!.trigger('click')
    await flushPromises()

    const addDirectoryButton = wrapper
      .findAll('button')
      .find((item) => item.text().includes('Add Directory'))
    expect(addDirectoryButton).toBeTruthy()
    await addDirectoryButton!.trigger('click')
    await flushPromises()

    const inputs = wrapper.findAll('input')
    const directoryInput = inputs.find((item) =>
      (item.attributes('placeholder') ?? '').includes('/absolute/path'),
    )
    expect(directoryInput).toBeTruthy()
    await directoryInput!.setValue('/tmp/new-root')

    const saveDirectoriesButton = wrapper
      .findAll('button')
      .find((item) => item.text().includes('Save Directories'))
    expect(saveDirectoriesButton).toBeTruthy()
    await saveDirectoriesButton!.trigger('click')
    await flushPromises()

    expect(savedRoots).toContain('/tmp/new-root')
    expect(wrapper.text()).toContain('Allowed directories updated.')
    expect(wrapper.text()).toContain('/tmp/new-root')
  })
})
