import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import PrimeVue from 'primevue/config'
import Aura from '@primeuix/themes/aura'

import App from '../App.vue'
import { createTestRouter } from '../router'

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
    vi.stubGlobal(
      'ResizeObserver',
      class ResizeObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    )
  })

  it('shows the setup empty state when the llm is not configured', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
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
        if (path.endsWith('/api/chat/sessions')) {
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
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
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
      if (path.endsWith('/api/chat/sessions')) {
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
        if (path.endsWith('/api/chat/sessions')) {
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
  })

  it('renders settings in the shared workspace via routing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) => {
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
        if (path.endsWith('/api/chat/sessions')) {
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
        if (path.endsWith('/api/chat/sessions')) {
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
