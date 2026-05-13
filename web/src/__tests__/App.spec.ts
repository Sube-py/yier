import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import PrimeVue from 'primevue/config'
import Aura from '@primeuix/themes/aura'

import App from '../App.vue'
import { provideWorkspaceAppContext } from '../composables/useWorkspaceApp'
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

class MockWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  static instances: MockWebSocket[] = []

  readyState = MockWebSocket.CONNECTING
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null

  constructor(readonly url: string) {
    MockWebSocket.instances.push(this)
  }

  send() {}

  close() {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.()
  }

  static reset() {
    MockWebSocket.instances = []
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

function isSessionTranscriptRequest(path: string, sessionId: string, init?: RequestInit) {
  return (
    (!init?.method || init.method === 'GET') &&
    (path === `/api/chat/sessions/${sessionId}` ||
      path.startsWith(`/api/chat/sessions/${sessionId}?`))
  )
}

function isAnySessionRequestPath(path: string) {
  return path.includes('/api/chat/sessions/')
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
      {
        index: 1,
        timestamp: 2,
        type: 'stdout',
        text: 'ready on http://localhost:3000',
        stream: 'stdout',
      },
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

const wrappedShellCommand = `/bin/zsh -lc "nl -ba tests/test_git_ops.py | sed -n '150,240p'"`

function skillLoadRaw(overrides: Record<string, unknown> = {}) {
  return {
    kind: 'skill_load',
    name: 'codex-session-history',
    description: 'Inspect Codex sessions',
    location: '/tmp/skills/codex-session-history/SKILL.md',
    directory: '/tmp/skills/codex-session-history',
    sampled_files: ['scripts/list_codex_sessions.py', '_meta.json'],
    sampled_file_locations: [
      '/tmp/skills/codex-session-history/scripts/list_codex_sessions.py',
      '/tmp/skills/codex-session-history/_meta.json',
    ],
    ...overrides,
  }
}

function fileReadRaw(overrides: Record<string, unknown> = {}) {
  return {
    kind: 'file_read',
    path: '/tmp/project/notes.md',
    start_line: 1,
    end_line: 4,
    max_chars: 6000,
    truncated: false,
    lines: [
      { number: 1, text: '# Notes' },
      { number: 2, text: 'hello' },
    ],
    ...overrides,
  }
}

function fileSearchRaw(overrides: Record<string, unknown> = {}) {
  return {
    kind: 'file_search',
    path: '/tmp/project',
    pattern: 'TODO',
    regex: false,
    case_sensitive: false,
    include_hidden: false,
    matches: [
      { path: 'src/main.ts', line_number: 2, text: 'const note = "TODO"' },
      { path: 'README.md', line_number: 8, text: '- TODO: document this' },
    ],
    ...overrides,
  }
}

async function mountAppWithRouter(initialPath = '/chat') {
  const router = createTestRouter()
  await router.push(initialPath)
  await router.isReady()

  const wrapper = mount(App, {
    global: {
      plugins: [router, [PrimeVue, { theme: { preset: Aura } }]],
    },
  })
  await nextTick()
  return { router, wrapper }
}

async function mountApp(initialPath = '/chat') {
  const { wrapper } = await mountAppWithRouter(initialPath)
  return wrapper
}

function stubBasicWorkspaceFetch() {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = input.toString()
    if (path.endsWith('/api/health')) {
      return jsonResponse({
        frontend: { ready: true, mode: 'static' },
        llm: { ready: true },
        mcp: { ready: true, runtime: {} },
        backends: {
          yier: { ready: true, label: 'Yier Agent' },
          codex: { ready: true, label: 'Codex App Server' },
        },
        allowed_roots: ['/tmp/project'],
      })
    }
    if (path.endsWith('/api/config')) {
      return jsonResponse({
        llm: { base_url: 'https://example.test', model: 'demo', has_api_key: true },
        allowed_roots: ['/tmp/project'],
        mcp_runtime: {},
        backends: [
          { id: 'yier', label: 'Yier Agent' },
          { id: 'codex', label: 'Codex App Server' },
        ],
        session_defaults: {
          default_backend_id: 'yier',
          default_project_path: '/tmp/project',
          channel_backend_id: 'yier',
          channel_project_path: '/tmp/project',
          channel_auto_approve_codex_requests: true,
          workspace_surface: 'yier',
        },
        codex: {
          launcher_command: 'codex app-server --listen stdio://',
          model: 'gpt-5-codex',
          sandbox: 'workspace-write',
          approval_policy: 'on-request',
          approvals_reviewer: 'user',
          personality: 'friendly',
          reasoning_effort: 'medium',
          show_reasoning_cards: false,
          service_tier: '',
        },
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
            title: 'First yier session',
            preview: 'first preview',
            updated_at: 100,
            message_count: 2,
            source: 'chat',
            backend_id: 'yier',
            project_path: '/tmp/project',
            channel_meta: null,
          },
        ],
      })
    }
    if (isSessionTranscriptRequest(path, 'session-1', init)) {
      return jsonResponse({
        session_id: 'session-1',
        source: 'chat',
        backend_id: 'yier',
        project_path: '/tmp/project',
        backend_runtime: {
          backend_id: 'yier',
          label: 'Yier Agent',
          ready: true,
          status: 'idle',
          active_flags: [],
          detail: null,
          pending_approval_count: 0,
        },
        pending_requests: [],
        pending_approvals: [],
        messages: [{ role: 'assistant', content: 'yier transcript body' }],
        activity_events: [],
        codex_turn_timings: [],
      })
    }
    if (path.endsWith('/api/channel/workspace')) {
      return jsonResponse({ platforms: [], accounts: [] })
    }
    if (path.endsWith('/api/channel/platforms')) {
      return jsonResponse({ platforms: [] })
    }
    if (path.endsWith('/api/channel/config')) {
      return jsonResponse({ enabled_platforms: [], weixin: {} })
    }
    if (path.endsWith('/api/channel/monitor/sessions')) {
      return jsonResponse({ sessions: [] })
    }
    throw new Error(`Unexpected request: ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('App', () => {
  beforeEach(() => {
    localStorage.clear()
    document.body.innerHTML = ''
    vi.restoreAllMocks()
    MockEventSource.reset()
    MockWebSocket.reset()
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    )
    Object.defineProperty(navigator, 'clipboard', {
      value: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
      configurable: true,
    })
    vi.stubGlobal(
      'ResizeObserver',
      class ResizeObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    )
    vi.stubGlobal('EventSource', MockEventSource)
    vi.stubGlobal('WebSocket', MockWebSocket)
    vi.stubGlobal('alert', vi.fn())
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
    expect(wrapper.text()).toContain(
      'Add a provider or `base_url`, plus `api_key` and `model`, in Settings.',
    )
    expect(wrapper.text()).toContain('Started a fresh session.')
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
        expect(JSON.parse(String(init?.body))).toEqual({
          backend_id: 'yier',
          project_path: '/tmp/project',
        })
        return jsonResponse({ session_id: `session-${sessionCounter}` }, 201)
      }
      if (isAnySessionRequestPath(path)) {
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
        if (isSessionTranscriptRequest(path, 'session-1', init)) {
          return jsonResponse({
            session_id: 'session-1',
            messages: [
              { role: 'user', content: 'open first' },
              { role: 'assistant', content: 'first transcript body' },
            ],
            activity_events: [],
          })
        }
        if (isSessionTranscriptRequest(path, 'session-2', init)) {
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

  it('hides codex sessions from the yier workspace sidebar', async () => {
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
            backends: {
              yier: { ready: true, label: 'Yier Agent' },
              codex: { ready: true, label: 'Codex App Server' },
            },
            allowed_roots: ['/tmp/project'],
          })
        }
        if (path.endsWith('/api/config')) {
          return jsonResponse({
            llm: { base_url: 'https://example.test', model: 'demo', has_api_key: true },
            allowed_roots: ['/tmp/project'],
            mcp_runtime: {},
            backends: [
              { id: 'yier', label: 'Yier Agent' },
              { id: 'codex', label: 'Codex App Server' },
            ],
            session_defaults: {
              default_backend_id: 'yier',
              default_project_path: '/tmp/project',
              channel_backend_id: 'yier',
              channel_project_path: '/tmp/project',
              channel_auto_approve_codex_requests: true,
              workspace_surface: 'yier',
            },
            codex: {
              launcher_command: 'codex app-server --listen stdio://',
              model: 'gpt-5-codex',
              sandbox: 'workspace-write',
              approval_policy: 'on-request',
              approvals_reviewer: 'user',
              personality: 'friendly',
              reasoning_effort: 'medium',
              show_reasoning_cards: false,
              service_tier: '',
            },
          })
        }
        if (path.endsWith('/api/config/mcp')) {
          return jsonResponse({ mcp_servers: {}, runtime: {} })
        }
        if (path.endsWith('/api/codex/workspace')) {
          return jsonResponse({ projects: [], paired_editors: [] })
        }
        if (isSessionListRequest(path, init)) {
          return jsonResponse({
            sessions: [
              {
                session_id: 'session-1',
                title: 'First yier session',
                preview: 'first preview',
                updated_at: 100,
                message_count: 2,
                source: 'chat',
                backend_id: 'yier',
                project_path: '/tmp/project',
                channel_meta: null,
              },
              {
                session_id: 'codex-thread-1',
                title: 'Codex hidden session',
                preview: 'codex preview',
                updated_at: 90,
                message_count: 1,
                source: 'chat',
                backend_id: 'codex',
                project_path: '/tmp/project',
                channel_meta: null,
                codex_work_mode: 'build',
              },
            ],
          })
        }
        if (isSessionTranscriptRequest(path, 'session-1', init)) {
          return jsonResponse({
            session_id: 'session-1',
            source: 'chat',
            backend_id: 'yier',
            project_path: '/tmp/project',
            backend_runtime: {
              backend_id: 'yier',
              label: 'Yier Agent',
              ready: true,
              status: 'idle',
              active_flags: [],
              detail: null,
              pending_approval_count: 0,
            },
            pending_approvals: [],
            messages: [{ role: 'assistant', content: 'yier transcript body' }],
            activity_events: [],
          })
        }
        if (path.endsWith('/api/channel/workspace')) {
          return jsonResponse({ platforms: [], accounts: [] })
        }
        if (path.endsWith('/api/channel/platforms')) {
          return jsonResponse({ platforms: [] })
        }
        if (path.endsWith('/api/channel/config')) {
          return jsonResponse({ enabled_platforms: [], weixin: {} })
        }
        if (path.endsWith('/api/channel/monitor/sessions')) {
          return jsonResponse({ sessions: [] })
        }
        throw new Error(`Unexpected request: ${path}`)
      }),
    )

    const wrapper = await mountApp()
    await flushPromises()

    expect(wrapper.text()).toContain('First yier session')
    expect(wrapper.text()).not.toContain('Codex hidden session')
    expect(wrapper.text()).toContain('Recent sessions1')
  })

  it('shows Codex in the workspace switcher', async () => {
    stubBasicWorkspaceFetch()
    const wrapper = await mountApp()
    await flushPromises()

    const workspaceSelect = wrapper
      .findAllComponents({ name: 'Select' })
      .find((select) => select.classes().includes('workspace-switcher-control'))

    if (!workspaceSelect) {
      throw new Error('Expected workspace switcher select to render.')
    }

    expect(workspaceSelect.props('options')).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: 'Codex',
          value: 'codex',
          disabled: false,
        }),
      ]),
    )
  })

  it('routes Codex workspace surface selections to the top-level Codex page', async () => {
    const fetchMock = stubBasicWorkspaceFetch()
    const router = createTestRouter()
    await router.push('/chat')
    await router.isReady()

    const Harness = defineComponent({
      setup() {
        const workspace = provideWorkspaceAppContext()
        return { workspace }
      },
      template: '<button type="button" @click="workspace.switchWorkspaceSurface(\'codex\')">Codex</button>',
    })

    const wrapper = mount(Harness, {
      global: {
        plugins: [router],
      },
    })
    await flushPromises()

    await (
      wrapper.vm as unknown as {
        workspace: { switchWorkspaceSurface: (surface: 'codex') => Promise<void> }
      }
    ).workspace.switchWorkspaceSurface('codex')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('codex')
    expect(router.currentRoute.value.matched).toHaveLength(1)
    expect(
      fetchMock.mock.calls.some(([input]) =>
        input.toString().endsWith('/api/config/app'),
      ),
    ).toBe(false)
    wrapper.unmount()
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
        if (isSessionTranscriptRequest(path, 'session-1', init)) {
          return jsonResponse({
            session_id: 'session-1',
            messages: [
              { role: 'user', content: 'open first' },
              { role: 'assistant', content: 'first transcript body' },
            ],
            activity_events: [],
          })
        }
        if (isSessionTranscriptRequest(path, 'session-2', init)) {
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
        if (isAnySessionRequestPath(path)) {
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
                  metadata: {
                    command: 'printf "hello"',
                    cwd: '/tmp/project',
                    exit_code: 0,
                    timed_out: false,
                  },
                  raw: {},
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
    expect(wrapper.text()).not.toContain('Command finished successfully.')
    expect(wrapper.findAll('.activity-item')).toHaveLength(1)
  })

  it('shows the unwrapped shell command instead of the shell launcher wrapper', async () => {
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
                title: 'inspect tests',
                preview: 'Done.',
                updated_at: 123,
                message_count: 2,
              },
            ],
          })
        }
        if (isAnySessionRequestPath(path)) {
          return jsonResponse({
            session_id: 'session-1',
            messages: [
              { role: 'user', content: 'inspect tests' },
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
                    command: wrappedShellCommand,
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
                  command: wrappedShellCommand,
                  cwd: '/tmp/project',
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
                  metadata: {
                    command: wrappedShellCommand,
                    cwd: '/tmp/project',
                    exit_code: 0,
                    timed_out: false,
                  },
                  raw: shellCommandRaw({
                    request: {
                      command: wrappedShellCommand,
                      cwd: '/tmp/project',
                      allow_shell: true,
                      shell_program: '/bin/zsh',
                    },
                    events: [
                      {
                        index: 0,
                        timestamp: 1,
                        type: 'started',
                        command: wrappedShellCommand,
                        cwd: '/tmp/project',
                      },
                    ],
                    latest_event_index: 0,
                  }),
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

    expect(wrapper.text()).toContain(`nl -ba tests/test_git_ops.py | sed -n '150,240p'`)
    expect(wrapper.text()).not.toContain('/bin/zsh -lc')
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
        if (isAnySessionRequestPath(path)) {
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

    const textarea = wrapper.get('textarea.composer-textarea')
    await textarea.setValue('List the project files')
    const buttons = wrapper.findAll('button')
    const sendButton = buttons.find((item) => item.attributes('aria-label') === 'Send message')
    expect(sendButton).toBeTruthy()
    await sendButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('List the project files')
    expect(wrapper.html()).toContain('<strong>project files</strong>')
    expect(wrapper.html()).toContain('language-ts')
    expect(wrapper.text()).toContain('Shell command')
    expect(wrapper.text()).toContain('printf "hello"')
    expect(wrapper.text()).toContain('hello')
    expect(wrapper.text()).toContain('1s')
    expect(wrapper.text()).not.toContain('Iteration 1')
    expect(wrapper.text()).not.toContain('Exit 0')
    expect(wrapper.text()).not.toContain('#3 exit 0 (completed)')
    expect(wrapper.findAll('.activity-item')).toHaveLength(1)
  })

  it('copies a shell command from the command bar', async () => {
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
        if (isAnySessionRequestPath(path)) {
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

    const textarea = wrapper.get('textarea.composer-textarea')
    await textarea.setValue('Run the command')
    const sendButton = wrapper
      .findAll('button')
      .find((item) => item.attributes('aria-label') === 'Send message')
    expect(sendButton).toBeTruthy()
    await sendButton!.trigger('click')
    await flushPromises()

    const copyButton = wrapper.find('.activity-command-copy')
    expect(copyButton.exists()).toBe(true)
    await copyButton.trigger('click')
    await flushPromises()

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('printf "hello"')
    expect(copyButton.attributes('aria-label')).toBe('Copied')
  })

  it('merges reasoning updates into one activity card and renders markdown', async () => {
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
            codex: { show_reasoning_cards: true },
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
        if (isAnySessionRequestPath(path)) {
          return jsonResponse({ session_id: 'session-1', messages: [] })
        }
        if (path.endsWith('/api/chat/stream') && init?.method === 'POST') {
          return sseResponse(
            [
              'event: run_started',
              'data: {"session_id":"session-1"}',
              '',
              'event: reasoning',
              'data: {"session_id":"session-1","item_id":"reasoning-1","content":"Inspect **repo**","iteration":0}',
              '',
              'event: reasoning',
              'data: {"session_id":"session-1","item_id":"reasoning-1","content":"Inspect **repo**\\n\\n- list files","iteration":0}',
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

    const textarea = wrapper.get('textarea.composer-textarea')
    await textarea.setValue('Inspect the repo')
    const sendButton = wrapper.find('[aria-label="Send message"]')
    expect(sendButton.exists()).toBe(true)
    await sendButton.trigger('click')
    await flushPromises()

    expect(wrapper.html()).toContain('<strong>repo</strong>')
    expect(wrapper.html()).toContain('<li>list files</li>')
  })

  it('hides reasoning activity cards by default', async () => {
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
        if (isAnySessionRequestPath(path)) {
          return jsonResponse({ session_id: 'session-1', messages: [] })
        }
        if (path.endsWith('/api/chat/stream') && init?.method === 'POST') {
          return sseResponse(
            [
              'event: run_started',
              'data: {"session_id":"session-1"}',
              '',
              'event: reasoning',
              'data: {"session_id":"session-1","item_id":"reasoning-1","content":"Inspect **repo**","iteration":0}',
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

    const textarea = wrapper.get('textarea.composer-textarea')
    await textarea.setValue('Inspect the repo')
    const sendButton = wrapper.find('[aria-label="Send message"]')
    expect(sendButton.exists()).toBe(true)
    await sendButton.trigger('click')
    await flushPromises()

    expect(wrapper.html()).not.toContain('<strong>repo</strong>')
    expect(wrapper.text()).not.toContain('Run activity')
  })

  it('keeps the final assistant message in sequence when later activity cards arrive', async () => {
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
            codex: { show_reasoning_cards: true },
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
        if (isAnySessionRequestPath(path)) {
          return jsonResponse({ session_id: 'session-1', messages: [] })
        }
        if (path.endsWith('/api/chat/stream') && init?.method === 'POST') {
          return sseResponse(
            [
              'event: assistant_delta',
              'data: {"session_id":"session-1","item_id":"assistant-1","delta":"Final answer"}',
              '',
              'event: reasoning',
              'data: {"session_id":"session-1","item_id":"reasoning-1","content":"Second step","iteration":0}',
              '',
              'event: assistant_message',
              'data: {"session_id":"session-1","item_id":"assistant-1","content":"Final answer","iteration":0}',
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

    const textarea = wrapper.get('textarea.composer-textarea')
    await textarea.setValue('Give me the result')
    const sendButton = wrapper.find('[aria-label="Send message"]')
    expect(sendButton.exists()).toBe(true)
    await sendButton.trigger('click')
    await flushPromises()

    const html = wrapper.html()
    expect(html.indexOf('Final answer')).toBeGreaterThan(-1)
    expect(html.indexOf('Second step')).toBeGreaterThan(-1)
    expect(html.indexOf('Final answer')).toBeLessThan(html.indexOf('Second step'))
  })

  it('replaces a streamed assistant draft when the final message arrives without item_id', async () => {
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
        if (isAnySessionRequestPath(path)) {
          return jsonResponse({ session_id: 'session-1', messages: [] })
        }
        if (path.endsWith('/api/chat/stream') && init?.method === 'POST') {
          return sseResponse(
            [
              'event: assistant_delta',
              'data: {"session_id":"session-1","item_id":"msg-1","delta":"你好！"}',
              '',
              'event: assistant_delta',
              'data: {"session_id":"session-1","item_id":"msg-1","delta":"我在这儿。"}',
              '',
              'event: assistant_message',
              'data: {"session_id":"session-1","content":"你好！我在这儿。","iteration":0}',
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

    const textarea = wrapper.get('textarea.composer-textarea')
    await textarea.setValue('Say hello')
    const sendButton = wrapper
      .findAll('button')
      .find((item) => item.attributes('aria-label') === 'Send message')
    expect(sendButton).toBeTruthy()
    await sendButton!.trigger('click')
    await flushPromises()

    const assistantBubbles = wrapper.findAll('.message-bubble--assistant')
    expect(assistantBubbles).toHaveLength(1)
    expect(wrapper.text().match(/你好！我在这儿。/g)?.length).toBe(1)
  })

  it('shows explicit stream error activity for codex stream failures', async () => {
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
        if (isAnySessionRequestPath(path)) {
          return jsonResponse({ session_id: 'session-1', messages: [] })
        }
        if (path.endsWith('/api/chat/stream') && init?.method === 'POST') {
          return sseResponse(
            [
              'event: run_started',
              'data: {"session_id":"session-1"}',
              '',
              'event: stream_error',
              'data: {"session_id":"session-1","message":"socket closed","thread_id":"thread-1","turn_id":"turn-1","code":"sandboxError","will_retry":false}',
              '',
              'event: done',
              'data: {"session_id":"session-1","finish_reason":"error"}',
              '',
            ].join('\r\n'),
          )
        }
        throw new Error(`Unexpected request: ${path}`)
      }),
    )

    const wrapper = await mountApp()
    await flushPromises()

    const textarea = wrapper.get('textarea.composer-textarea')
    await textarea.setValue('Run codex')
    const sendButton = wrapper
      .findAll('button')
      .find((item) => item.attributes('aria-label') === 'Send message')
    expect(sendButton).toBeTruthy()
    await sendButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Stream error')
    expect(wrapper.text()).toContain('socket closed')
    expect(wrapper.text()).toContain('code sandboxError')
  })

  it('renders compact digests for successful non-shell tools', async () => {
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
                title: 'digest tools',
                preview: 'Done.',
                updated_at: 123,
                message_count: 2,
              },
            ],
          })
        }
        if (isAnySessionRequestPath(path)) {
          return jsonResponse({
            session_id: 'session-1',
            messages: [
              { role: 'user', content: 'inspect tools' },
              { role: 'assistant', content: 'Done.' },
            ],
            activity_events: [
              {
                event: 'tool_call_start',
                data: {
                  session_id: 'session-1',
                  tool_name: 'skill_load',
                  tool_call_id: 'tool-1',
                  arguments: { name: 'codex-session-history' },
                  iteration: 1,
                },
              },
              {
                event: 'tool_call_end',
                data: {
                  session_id: 'session-1',
                  tool_name: 'skill_load',
                  tool_call_id: 'tool-1',
                  result: 'Loaded skill content',
                  is_error: false,
                  iteration: 1,
                  raw: skillLoadRaw(),
                },
              },
              {
                event: 'tool_call_start',
                data: {
                  session_id: 'session-1',
                  tool_name: 'read_file',
                  tool_call_id: 'tool-2',
                  arguments: { path: '/tmp/project/notes.md' },
                  iteration: 2,
                },
              },
              {
                event: 'tool_call_end',
                data: {
                  session_id: 'session-1',
                  tool_name: 'read_file',
                  tool_call_id: 'tool-2',
                  result: 'Read file',
                  is_error: false,
                  iteration: 2,
                  raw: fileReadRaw(),
                },
              },
              {
                event: 'tool_call_start',
                data: {
                  session_id: 'session-1',
                  tool_name: 'search_files',
                  tool_call_id: 'tool-3',
                  arguments: { path: '/tmp/project', pattern: 'TODO' },
                  iteration: 3,
                },
              },
              {
                event: 'tool_call_end',
                data: {
                  session_id: 'session-1',
                  tool_name: 'search_files',
                  tool_call_id: 'tool-3',
                  result: 'Found matches',
                  is_error: false,
                  iteration: 3,
                  raw: fileSearchRaw(),
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

    expect(wrapper.text()).toContain('Loaded skill codex-session-history with 2 sampled files.')
    expect(wrapper.text()).toContain('Read notes.md lines 1-4.')
    expect(wrapper.text()).toContain('Found 2 matches for "TODO" in project.')
    expect(wrapper.text()).not.toContain('<skill_content')
    expect(wrapper.text()).not.toContain('"path": "/tmp/project/notes.md"')
    expect(wrapper.text()).not.toContain('"pattern": "TODO"')
  })

  it('falls back to a compact summary when skill_load raw is missing', async () => {
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
                title: 'load skill',
                preview: 'Done.',
                updated_at: 123,
                message_count: 1,
              },
            ],
          })
        }
        if (isAnySessionRequestPath(path)) {
          return jsonResponse({
            session_id: 'session-1',
            messages: [{ role: 'user', content: 'load a skill' }],
            activity_events: [
              {
                event: 'tool_call_start',
                data: {
                  session_id: 'session-1',
                  tool_name: 'skill_load',
                  tool_call_id: 'tool-1',
                  arguments: { name: 'codex-session-history' },
                  iteration: 1,
                },
              },
              {
                event: 'tool_call_end',
                data: {
                  session_id: 'session-1',
                  tool_name: 'skill_load',
                  tool_call_id: 'tool-1',
                  result:
                    '<skill_content name="codex-session-history">\n# Skill: codex-session-history\n<skill_files>\n<file>a</file>\n<file>b</file>\n</skill_files>\n</skill_content>',
                  is_error: false,
                  iteration: 1,
                  metadata: {
                    name: 'codex-session-history',
                    dir: '/tmp/skills/codex-session-history',
                  },
                  raw: {},
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

    expect(wrapper.text()).toContain('Load skill')
    expect(wrapper.text()).toContain('Loaded skill codex-session-history with 2 sampled files.')
    expect(wrapper.text()).not.toContain('<skill_content')
    expect(wrapper.text()).not.toContain('# Skill: codex-session-history')
  })

  it('does not create a duplicate digest card for shell tools when raw is empty', async () => {
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
        if (isAnySessionRequestPath(path)) {
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
                  content: 'hello\n',
                  is_background: false,
                },
              },
              {
                event: 'command_end',
                data: {
                  session_id: 'session-1',
                  tool_call_id: 'call-1',
                  tool_name: 'run_command',
                  command: 'printf "hello"',
                  cwd: '/tmp/project',
                  exit_code: 0,
                  timed_out: false,
                  is_background: false,
                },
              },
              {
                event: 'tool_call_end',
                data: {
                  session_id: 'session-1',
                  tool_name: 'run_command',
                  tool_call_id: 'call-1',
                  result: 'Command: printf "hello"\nWorking directory: /tmp/project\nExit code: 0',
                  is_error: false,
                  iteration: 1,
                  metadata: {
                    command: 'printf "hello"',
                    cwd: '/tmp/project',
                    exit_code: 0,
                    timed_out: false,
                  },
                  raw: {},
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

    expect(wrapper.findAll('.activity-item')).toHaveLength(1)
    expect(wrapper.text()).toContain('Shell command')
    expect(wrapper.text()).toContain('printf "hello"')
    expect(wrapper.text()).toContain('hello')
    expect(wrapper.text()).not.toContain('Working directory: /tmp/project')
    expect(wrapper.text()).not.toContain('Exit code: 0')
  })

  it('keeps tool errors visible while hiding background list noise', async () => {
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
                title: 'tool errors',
                preview: 'Oops.',
                updated_at: 123,
                message_count: 1,
              },
            ],
          })
        }
        if (isAnySessionRequestPath(path)) {
          return jsonResponse({
            session_id: 'session-1',
            messages: [{ role: 'user', content: 'inspect tools' }],
            activity_events: [
              {
                event: 'tool_call_start',
                data: {
                  session_id: 'session-1',
                  tool_name: 'list_background_commands',
                  tool_call_id: 'tool-bg',
                  arguments: { include_completed: true },
                  iteration: 1,
                },
              },
              {
                event: 'tool_call_end',
                data: {
                  session_id: 'session-1',
                  tool_name: 'list_background_commands',
                  tool_call_id: 'tool-bg',
                  result: 'No background commands.',
                  is_error: false,
                  iteration: 1,
                  metadata: { count: 0, running_count: 0 },
                  raw: {
                    kind: 'background_command',
                    sessions: [],
                  },
                },
              },
              {
                event: 'tool_call_start',
                data: {
                  session_id: 'session-1',
                  tool_name: 'read_file',
                  tool_call_id: 'tool-err',
                  arguments: { path: '/tmp/project/missing.md' },
                  iteration: 2,
                },
              },
              {
                event: 'tool_call_end',
                data: {
                  session_id: 'session-1',
                  tool_name: 'read_file',
                  tool_call_id: 'tool-err',
                  result: 'Execution error: File not found: /tmp/project/missing.md',
                  is_error: true,
                  iteration: 2,
                  raw: {},
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

    expect(wrapper.text()).toContain('File not found: missing.md.')
    expect(wrapper.text()).not.toContain('No background commands.')
    expect(wrapper.text()).not.toContain('Shell command')
    expect(wrapper.findAll('.activity-item')).toHaveLength(1)
  })

  it('compresses read_file allowed-roots errors into a short message', async () => {
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
                title: 'read file',
                preview: 'Blocked.',
                updated_at: 123,
                message_count: 1,
              },
            ],
          })
        }
        if (isAnySessionRequestPath(path)) {
          return jsonResponse({
            session_id: 'session-1',
            messages: [{ role: 'user', content: 'read a file' }],
            activity_events: [
              {
                event: 'tool_call_start',
                data: {
                  session_id: 'session-1',
                  tool_name: 'read_file',
                  tool_call_id: 'tool-1',
                  arguments: {
                    path: '/Users/sube/.codex/sessions/demo.jsonl',
                  },
                  iteration: 1,
                },
              },
              {
                event: 'tool_call_end',
                data: {
                  session_id: 'session-1',
                  tool_name: 'read_file',
                  tool_call_id: 'tool-1',
                  result:
                    'Execution error: Path is outside allowed roots: /Users/sube/.codex/sessions/demo.jsonl. Allowed roots: /tmp/project, /tmp/docs, /tmp/more',
                  is_error: true,
                  iteration: 1,
                  raw: {},
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

    expect(wrapper.text()).toContain('Read file')
    expect(wrapper.text()).toContain("Can't read demo.jsonl because it is outside allowed roots.")
    expect(wrapper.text()).not.toContain('Allowed roots: /tmp/project, /tmp/docs, /tmp/more')
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

    expect(wrapper.text()).toContain('Save LLM Settings')
    expect(wrapper.text()).toContain('Configuration workspace')
  })

  it('keeps the workspace sidebar hidden on mobile settings screens', async () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn().mockImplementation((query: string) => ({
        matches: query === '(max-width: 1023px)',
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    )

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

    expect(wrapper.find('.rail-actions').exists()).toBe(false)
    expect(wrapper.find('.brand-panel').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Recent sessions')
  })

  it('hydrates the provider selector from config', async () => {
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
            llm: {
              provider: 'zai-coding-plan',
              base_url: 'https://api.z.ai/api/coding/paas/v4',
              model: 'glm-4.7-flash',
              has_api_key: true,
            },
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

    const providerSelect = wrapper
      .findAllComponents({ name: 'Select' })
      .find((component) => component.props('inputId') === 'provider')
    expect(providerSelect).toBeTruthy()
    expect(providerSelect!.props('modelValue')).toBe('zai-coding-plan')
    expect((wrapper.find('input#base-url').element as HTMLInputElement).value).toBe(
      'https://api.z.ai/api/coding/paas/v4',
    )
    expect((wrapper.find('input#model').element as HTMLInputElement).value).toBe('glm-4.7-flash')
    expect((wrapper.find('input#base-url').element as HTMLInputElement).placeholder).toBe(
      'Optional override for the preset endpoint',
    )
  })

  it('keeps a saved custom model when hydrating a preset provider', async () => {
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
            llm: {
              provider: 'zai-coding-plan',
              base_url: '',
              model: 'glm-4.7',
              has_api_key: true,
            },
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

    expect((wrapper.find('input#base-url').element as HTMLInputElement).value).toBe(
      'https://api.z.ai/api/coding/paas/v4',
    )
    expect((wrapper.find('input#model').element as HTMLInputElement).value).toBe('glm-4.7')
  })

  it('prefills Z.AI Coding Plan defaults when switching provider and saves without persisting the default base URL', async () => {
    let savedPayload: Record<string, unknown> | null = null

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
            llm: { provider: '', base_url: '', model: '', has_api_key: false },
            allowed_roots: ['/tmp/project'],
            mcp_runtime: {},
          })
        }
        if (path.endsWith('/api/config/llm') && init?.method === 'PUT') {
          savedPayload = JSON.parse(String(init.body)) as Record<string, unknown>
          return jsonResponse({
            llm: {
              provider: 'zai-coding-plan',
              base_url: '',
              model: 'glm-4.7-flash',
              has_api_key: true,
            },
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

    const providerSelect = wrapper
      .findAllComponents({ name: 'Select' })
      .find((component) => component.props('inputId') === 'provider')
    expect(providerSelect).toBeTruthy()
    providerSelect!.vm.$emit('update:modelValue', 'zai-coding-plan')
    await flushPromises()

    expect((wrapper.find('input#base-url').element as HTMLInputElement).value).toBe(
      'https://api.z.ai/api/coding/paas/v4',
    )
    expect((wrapper.find('input#model').element as HTMLInputElement).value).toBe('glm-4.7-flash')

    const apiKeyInput = wrapper
      .findAll('input')
      .find((item) => (item.attributes('placeholder') ?? '').includes('Leave blank'))
    expect(apiKeyInput).toBeTruthy()
    await apiKeyInput!.setValue('secret-token')
    await flushPromises()

    const saveButton = wrapper
      .findAll('button')
      .find((item) => item.text().includes('Save LLM Settings'))
    expect(saveButton).toBeTruthy()
    await saveButton!.trigger('click')
    await flushPromises()

    expect(savedPayload).toEqual({
      provider: 'zai-coding-plan',
      base_url: '',
      model: 'glm-4.7-flash',
      api_key: 'secret-token',
    })
    expect(wrapper.text()).toContain('LLM settings saved.')
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
        if (isAnySessionRequestPath(path)) {
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
        if (isAnySessionRequestPath(path)) {
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
          {
            index: 1,
            timestamp: 2,
            type: 'stdout',
            text: 'ready on http://localhost:3000',
            stream: 'stdout',
          },
          { index: 2, timestamp: 3, type: 'stdin', text: 'rs\n', append_newline: true },
        ],
        latest_event_index: 2,
      }),
    })
    await flushPromises()

    expect(wrapper.findAll('.activity-item')).toHaveLength(1)
    expect(wrapper.text()).toContain('Background bg-1')
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

    const workspaceTab = wrapper
      .findAll('[role="tab"]')
      .find((item) => item.text().includes('Workspace'))
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
