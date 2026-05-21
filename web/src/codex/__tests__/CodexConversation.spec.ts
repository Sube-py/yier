import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CodexConversation from '../components/CodexConversation.vue'

import type { CodexConversationState } from '../types'

function createState(items: Record<string, unknown>[]): CodexConversationState {
  return {
    id: 'thread-1',
    turns: [
      {
        turnId: 'turn-1',
        status: 'completed',
        items,
      },
    ],
  }
}

function mountConversation(items: Record<string, unknown>[]) {
  return mount(CodexConversation, {
    props: {
      state: createState(items),
    },
    attachTo: document.body,
  })
}

async function flushScrollWatchers() {
  await nextTick()
  await nextTick()
}

function scrollContainer(wrapper: VueWrapper) {
  return wrapper.get('[data-codex-conversation-body]').element as HTMLElement
}

function setScrollMetrics(
  element: HTMLElement,
  { scrollHeight, clientHeight }: { scrollHeight: number; clientHeight: number },
) {
  Object.defineProperty(element, 'scrollHeight', {
    configurable: true,
    value: scrollHeight,
  })
  Object.defineProperty(element, 'clientHeight', {
    configurable: true,
    value: clientHeight,
  })
}

describe('CodexConversation', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.stubGlobal('navigator', {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    })
  })

  it('renders conversational agent messages as markdown with copyable code blocks', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', {
      clipboard: {
        writeText,
      },
    })

    const wrapper = mountConversation([
      {
        id: 'agent-1',
        type: 'agentMessage',
        text: '**Bold** and `code`\n\n```ts\nconst value = 1\n```',
      },
    ])

    expect(wrapper.html()).toContain('<strong>Bold</strong>')
    expect(wrapper.html()).toContain('<code>code</code>')
    expect(wrapper.find('[data-copy-markdown-code]').exists()).toBe(true)

    await wrapper.get('[data-copy-markdown-code]').trigger('click')

    expect(writeText).toHaveBeenCalledWith('const value = 1')
    expect(wrapper.get('[data-copy-markdown-code]').attributes('aria-label')).toBe('Copied')
  })

  it('keeps long user messages constrained to the right-aligned bubble', () => {
    const wrapper = mountConversation([
      {
        id: 'user-1',
        type: 'userMessage',
        content: 'x'.repeat(240),
      },
    ])

    const row = wrapper.get('article')
    const bubble = wrapper.get('[data-codex-bubble]')
    const prose = wrapper.get('.markdown-prose')

    expect(row.classes()).toEqual(expect.arrayContaining(['flex', 'min-w-0', 'w-full', 'justify-end']))
    expect(bubble.classes()).toEqual(
      expect.arrayContaining(['min-w-0', 'overflow-hidden', 'w-fit', 'max-w-[min(40rem,88%)]']),
    )
    expect(prose.classes()).toContain('[overflow-wrap:anywhere]')
    expect(wrapper.find('section').classes()).toEqual(
      expect.arrayContaining(['min-w-0', 'overflow-x-clip']),
    )
  })

  it('renders command output as contained raw text instead of markdown prose', () => {
    const wrapper = mountConversation([
      {
        id: 'command-1',
        type: 'commandExecution',
        command: 'printf "**raw**"',
        aggregatedOutput: '**raw** output',
      },
    ])

    const raw = wrapper.get('[data-codex-raw]')

    expect(raw.element.tagName).toBe('PRE')
    expect(raw.classes()).toEqual(expect.arrayContaining(['max-w-full', 'overflow-auto']))
    expect(raw.text()).toContain('printf "**raw**"')
    expect(raw.text()).toContain('**raw** output')
    expect(wrapper.find('.markdown-prose').exists()).toBe(false)
    expect(wrapper.html()).not.toContain('<strong>raw</strong>')
  })

  it('renders unknown items as contained raw json', () => {
    const wrapper = mountConversation([
      {
        id: 'unknown-1',
        type: 'newThing',
        payload: { ok: true },
      },
    ])

    const raw = wrapper.get('[data-codex-raw]')

    expect(raw.element.tagName).toBe('PRE')
    expect(raw.classes()).toEqual(expect.arrayContaining(['max-w-full', 'overflow-auto']))
    expect(raw.text()).toContain('"type": "newThing"')
    expect(raw.text()).toContain('"ok": true')
  })

  it('scrolls to the bottom when the conversation opens', async () => {
    const wrapper = mountConversation([
      {
        id: 'agent-1',
        type: 'agentMessage',
        text: 'Earlier output',
      },
    ])
    const element = scrollContainer(wrapper)
    setScrollMetrics(element, { scrollHeight: 1200, clientHeight: 320 })

    await flushScrollWatchers()

    expect(element.scrollTop).toBe(1200)
  })

  it('does not force-scroll new output after the user scrolls away from the bottom', async () => {
    const wrapper = mountConversation([
      {
        id: 'agent-1',
        type: 'agentMessage',
        text: 'Earlier output',
      },
    ])
    const element = scrollContainer(wrapper)
    setScrollMetrics(element, { scrollHeight: 1200, clientHeight: 320 })
    await flushScrollWatchers()

    element.scrollTop = 400
    await wrapper.get('[data-codex-conversation-body]').trigger('scroll')

    setScrollMetrics(element, { scrollHeight: 1400, clientHeight: 320 })
    await wrapper.setProps({
      state: createState([
        {
          id: 'agent-1',
          type: 'agentMessage',
          text: 'Earlier output',
        },
        {
          id: 'agent-2',
          type: 'agentMessage',
          text: 'New output',
        },
      ]),
    })
    await flushScrollWatchers()

    expect(element.scrollTop).toBe(400)
  })

  it('keeps following new output while the user is near the bottom', async () => {
    const wrapper = mountConversation([
      {
        id: 'agent-1',
        type: 'agentMessage',
        text: 'Earlier output',
      },
    ])
    const element = scrollContainer(wrapper)
    setScrollMetrics(element, { scrollHeight: 1200, clientHeight: 320 })
    await flushScrollWatchers()

    element.scrollTop = 840
    await wrapper.get('[data-codex-conversation-body]').trigger('scroll')

    setScrollMetrics(element, { scrollHeight: 1400, clientHeight: 320 })
    await wrapper.setProps({
      state: createState([
        {
          id: 'agent-1',
          type: 'agentMessage',
          text: 'Earlier output',
        },
        {
          id: 'agent-2',
          type: 'agentMessage',
          text: 'New output',
        },
      ]),
    })
    await flushScrollWatchers()

    expect(element.scrollTop).toBe(1400)
  })
})
