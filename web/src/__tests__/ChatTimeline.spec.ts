import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount, type DOMWrapper } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import Aura from '@primeuix/themes/aura'

import ChatTimeline from '../components/ChatTimeline.vue'

import type { ChatActivity, UiChatMessage } from '../types/api'

function createMessage(overrides: Partial<UiChatMessage> = {}): UiChatMessage {
  return {
    id: 'message-1',
    role: 'assistant',
    content: 'Hello',
    sequence: 1,
    source: 'chat',
    channelMeta: null,
    draftId: null,
    ...overrides,
  }
}

function createActivity(overrides: Partial<ChatActivity> = {}): ChatActivity {
  return {
    id: 'activity-1',
    sequence: 1,
    kind: 'tool',
    title: 'Read file',
    detail: 'Read README.md',
    state: 'done',
    command: '',
    cwd: '',
    stdout: '',
    stderr: '',
    meta: [],
    shell: null,
    tool: null,
    approval: null,
    ...overrides,
  }
}

function mountTimeline(props: Partial<InstanceType<typeof ChatTimeline>['$props']> = {}) {
  return mount(ChatTimeline, {
    props: {
      messages: [],
      activities: [],
      isSending: false,
      sessionLabel: 'A1',
      sessionRuntime: null,
      projectPath: '/tmp/project',
      ...props,
    },
    global: {
      plugins: [[PrimeVue, { theme: { preset: Aura } }]],
    },
    attachTo: document.body,
  })
}

async function setDetailsOpen(wrapper: DOMWrapper<Element>, open = true) {
  const element = wrapper.element as HTMLDetailsElement
  element.open = open
  await wrapper.trigger('toggle')
  await flushPromises()
}

describe('ChatTimeline', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.stubGlobal('navigator', {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    })
    vi.stubGlobal(
      'ResizeObserver',
      class ResizeObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    )
  })

  it('groups activity updates between user and final assistant messages in render order', async () => {
    const wrapper = mountTimeline({
      messages: [
        createMessage({ id: 'user-1', role: 'user', content: 'Inspect the file', sequence: 1 }),
        createMessage({ id: 'assistant-1', role: 'assistant', content: 'Done', sequence: 3 }),
      ],
      activities: [
        createActivity({
          id: 'activity-1',
          sequence: 2,
          title: 'Read file',
          detail: 'Read README.md',
        }),
      ],
    })

    await flushPromises()

    const text = wrapper.text()
    expect(text.indexOf('Inspect the file')).toBeLessThan(text.indexOf('Worked through 1 update'))
    expect(text.indexOf('Worked through 1 update')).toBeLessThan(text.indexOf('Done'))
  })

  it('keeps activity updates before the final assistant message when activity sequences are missing', async () => {
    const wrapper = mountTimeline({
      messages: [
        createMessage({ id: 'user-1', role: 'user', content: 'Inspect the file', sequence: 10 }),
        createMessage({ id: 'assistant-1', role: 'assistant', content: 'Done', sequence: 20 }),
      ],
      activities: [
        createActivity({
          id: 'activity-1',
          sequence: undefined,
          title: 'Read file',
          detail: 'Read README.md',
        }),
      ],
    })

    await flushPromises()

    const text = wrapper.text()
    expect(text.indexOf('Inspect the file')).toBeLessThan(text.indexOf('Worked through 1 update'))
    expect(text.indexOf('Worked through 1 update')).toBeLessThan(text.indexOf('Done'))
  })

  it('shows the final message separator when the prior turn group is expanded', async () => {
    const wrapper = mountTimeline({
      messages: [
        createMessage({ id: 'user-1', role: 'user', content: 'Inspect the file', sequence: 1 }),
        createMessage({ id: 'assistant-1', role: 'assistant', content: 'Done', sequence: 3 }),
      ],
      activities: [
        createActivity({
          id: 'activity-1',
          sequence: 2,
          title: 'Read file',
          detail: 'Read README.md',
        }),
      ],
    })

    await flushPromises()
    expect(wrapper.text()).not.toContain('Final message')

    const turnGroup = wrapper.find('details:not(.activity-item)')
    await setDetailsOpen(turnGroup)

    expect(wrapper.text()).toContain('Final message')
  })

  it('renders expandable standalone activity cards and grouped activity items', async () => {
    const wrapper = mountTimeline({
      messages: [
        createMessage({ id: 'user-1', role: 'user', content: 'Run the command', sequence: 1 }),
        createMessage({ id: 'assistant-1', role: 'assistant', content: 'Ready', sequence: 3 }),
      ],
      activities: [
        createActivity({
          id: 'group-activity',
          sequence: 2,
          title: 'Command',
          detail: '',
          state: 'done',
          shell: {
            kind: 'shell_command',
            tool_name: 'shell',
            tool_call_id: 'tool-1',
            session_id: null,
            request: { command: 'printf "hello"' },
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
              { index: 0, timestamp: 1, type: 'started', command: 'printf "hello"' },
              { index: 1, timestamp: 2, type: 'stdout', text: 'hello', stream: 'stdout' },
            ],
            latest_event_index: 1,
            streams: {
              stdout: { text: 'hello', truncated: false },
              stderr: { text: '', truncated: false },
            },
            events_truncated: false,
            dropped_event_count: 0,
          },
        }),
        createActivity({
          id: 'standalone-activity',
          sequence: 4,
          title: 'Notes',
          detail: 'Updated changelog',
          stdout: 'line one',
        }),
      ],
    })

    await flushPromises()

    const turnGroup = wrapper.find('details:not(.activity-item)')
    await setDetailsOpen(turnGroup)
    expect(wrapper.text()).toContain('Output')

    const standaloneCard = wrapper.findAll('details.activity-item')[0]!
    await setDetailsOpen(standaloneCard)
    expect(wrapper.text()).toContain('Stdout')
    expect(wrapper.text()).toContain('line one')
  })

  it('submits structured approval responses and validates required fields', async () => {
    const wrapper = mountTimeline({
      activities: [
        createActivity({
          id: 'approval-activity',
          sequence: 1,
          kind: 'approval',
          title: 'Approval',
          detail: '',
          state: 'queued',
          approval: {
            requestId: 'approval-1',
            method: 'mcpServer/elicitation/request',
            kind: 'mcp_elicitation',
            options: [{ label: 'Approve', value: 'accept' }],
            payload: {
              request: {
                message: 'Pick a destination',
              },
            },
            formMode: 'structured',
            formFields: [
              {
                id: 'target',
                label: 'Target',
                prompt: 'Choose target',
                kind: 'select',
                required: true,
                value: '',
                options: [{ label: 'Prod', value: 'prod' }],
              },
            ],
            responseDraft: '',
            validationError: null,
            submittedDecision: null,
          },
        }),
      ],
    })

    await flushPromises()

    const approvalButton = wrapper.get('.approval-card button')

    await approvalButton.trigger('click')
    await flushPromises()
    expect(wrapper.emitted('approvalAction')).toBeUndefined()
    expect(wrapper.text()).toContain('Target is required.')

    await wrapper.get('.approval-select').setValue('prod')
    await flushPromises()
    await approvalButton.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('approvalAction')).toEqual([
      ['approval-1', 'accept', '{"target":"prod"}'],
    ])
  })

  it('submits JSON fallback approval responses', async () => {
    const wrapper = mountTimeline({
      activities: [
        createActivity({
          id: 'approval-json',
          sequence: 1,
          kind: 'approval',
          title: 'Approval',
          detail: '',
          state: 'queued',
          approval: {
            requestId: 'approval-2',
            method: 'mcpServer/elicitation/request',
            kind: 'mcp_elicitation',
            options: [{ label: 'Send', value: 'accept' }],
            payload: {
              request: {
                message: 'Return JSON',
              },
            },
            formMode: 'json',
            formFields: [],
            responseDraft: '{"ok":true}',
            validationError: null,
            submittedDecision: null,
          },
        }),
      ],
    })

    await flushPromises()

    const textarea = wrapper.get('textarea')
    await textarea.setValue('{"ok":false}')
    await flushPromises()
    await wrapper.get('.approval-card button').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('approvalAction')).toEqual([
      ['approval-2', 'accept', '{"ok":false}'],
    ])
  })

  it('copies fenced markdown code blocks from assistant messages', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', {
      clipboard: {
        writeText,
      },
    })

    const wrapper = mountTimeline({
      messages: [
        createMessage({
          id: 'assistant-code',
          role: 'assistant',
          content: '```ts\nconst value = 1\n```',
          sequence: 1,
        }),
      ],
    })

    await flushPromises()

    await wrapper.get('[data-copy-markdown-code]').trigger('click')

    expect(writeText).toHaveBeenCalledWith('const value = 1')
    expect(wrapper.get('[data-copy-markdown-code]').attributes('aria-label')).toBe('Copied')
  })

  it('renders markdown for user messages', async () => {
    const wrapper = mountTimeline({
      messages: [
        createMessage({
          id: 'user-markdown',
          role: 'user',
          content: '**Bold** and `code`',
          sequence: 1,
        }),
      ],
    })

    await flushPromises()

    const html = wrapper.html()
    expect(html).toContain('<strong>Bold</strong>')
    expect(html).toContain('<code>code</code>')
  })
})
