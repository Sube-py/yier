import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CodexConversation from '../components/CodexConversation.vue'

import type { CodexConversationState, CodexTurnState } from '../types'

function createState(
  items: Record<string, unknown>[],
  turnOverrides: Partial<CodexTurnState> = {},
): CodexConversationState {
  return {
    id: 'thread-1',
    turns: [
      {
        turnId: 'turn-1',
        status: 'completed',
        items,
        ...turnOverrides,
      },
    ],
  }
}

function mountConversation(
  items: Record<string, unknown>[],
  turnOverrides: Partial<CodexTurnState> = {},
) {
  return mount(CodexConversation, {
    props: {
      state: createState(items, turnOverrides),
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
    vi.useRealTimers()
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

    const row = wrapper.get('[data-codex-user-message]')
    const bubble = wrapper.get('[data-codex-bubble]')
    const prose = wrapper.get('.markdown-prose')

    expect(row.classes()).toEqual(
      expect.arrayContaining(['flex', 'min-w-0', 'w-full', 'justify-end']),
    )
    expect(bubble.classes()).toEqual(
      expect.arrayContaining([
        'min-w-0',
        'overflow-hidden',
        'w-fit',
        'max-w-[min(40rem,88%)]',
        'max-sm:max-w-[96%]',
      ]),
    )
    expect(prose.classes()).toContain('[overflow-wrap:anywhere]')
    expect(wrapper.find('section').classes()).toEqual(
      expect.arrayContaining(['min-w-0', 'overflow-x-clip']),
    )
  })

  it('keeps expanded work details mobile-safe', async () => {
    const wrapper = mountConversation([
      {
        id: 'command-1',
        type: 'commandExecution',
        command: 'pnpm test',
        aggregatedOutput: 'ok',
      },
    ])

    await wrapper.get('[data-codex-work-toggle]').trigger('click')
    await wrapper.get('[data-codex-work-item] button').trigger('click')

    expect(wrapper.get('[data-codex-work-items]').classes()).toEqual(
      expect.arrayContaining(['min-w-0', 'max-sm:pl-2']),
    )
    expect(wrapper.get('[data-codex-work-detail]').classes()).toEqual(
      expect.arrayContaining(['min-w-0', 'max-sm:pl-0']),
    )
    expect(wrapper.get('[data-codex-command-output]').classes()).toContain('min-w-0')
  })

  it('keeps command output in the collapsed work section until expanded', async () => {
    const wrapper = mountConversation([
      {
        id: 'command-1',
        type: 'commandExecution',
        command: 'printf "**raw**"',
        aggregatedOutput: '**raw** output',
      },
      {
        id: 'agent-1',
        type: 'agentMessage',
        text: 'Done',
      },
    ])

    expect(wrapper.get('[data-codex-work-toggle]').text()).toContain('Worked')
    expect(wrapper.find('[data-codex-command-output]').exists()).toBe(false)
    expect(wrapper.find('[data-codex-work-items]').exists()).toBe(false)
    expect(wrapper.find('[data-codex-work-section] .markdown-prose').exists()).toBe(false)

    await wrapper.get('[data-codex-work-toggle]').trigger('click')
    await wrapper.get('[data-codex-work-item] button').trigger('click')

    const output = wrapper.get('[data-codex-command-output]')
    expect(output.text()).toContain('$ printf "**raw**"')
    expect(output.text()).toContain('**raw** output')
    expect(wrapper.html()).not.toContain('<strong>raw</strong>')
  })

  it('shows completed turn work duration before the final assistant response', () => {
    const wrapper = mountConversation(
      [
        {
          id: 'command-1',
          type: 'commandExecution',
          command: 'pnpm test',
          aggregatedOutput: 'ok',
        },
        {
          id: 'agent-1',
          type: 'agentMessage',
          text: 'Ready',
        },
      ],
      {
        durationMs: 12_000,
      },
    )

    expect(wrapper.text()).toContain('Worked for 12s')
    expect(wrapper.get('[data-codex-work-toggle]').attributes('aria-expanded')).toBe('false')
    expect(wrapper.get('[data-codex-assistant-message]').text()).toContain('Ready')
  })

  it('keeps in-progress work expanded and shows working elapsed time', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-21T00:00:03.000Z'))

    const wrapper = mountConversation(
      [
        {
          id: 'reasoning-1',
          type: 'reasoning',
          status: 'inProgress',
          content: 'Inspecting the code',
        },
      ],
      {
        status: 'inProgress',
        turnStartedAtMs: new Date('2026-05-21T00:00:00.000Z').getTime(),
      },
    )

    expect(wrapper.text()).toContain('Working for 3s')
    expect(wrapper.get('[data-codex-work-toggle]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('[data-codex-work-item]').text()).toContain('Thinking')

    vi.useRealTimers()
  })

  it('renders dynamic tool calls as lightweight work rows with detail on demand', async () => {
    const wrapper = mountConversation([
      {
        id: 'tool-1',
        type: 'dynamicToolCall',
        tool: 'generate_diagram',
        status: 'completed',
        aggregatedOutput: '{"ok":true}',
      },
      {
        id: 'agent-1',
        type: 'agentMessage',
        text: 'Done',
      },
    ])

    await wrapper.get('[data-codex-work-toggle]').trigger('click')

    expect(wrapper.get('[data-codex-work-item]').text()).toContain('Called Generate Diagram')
    expect(wrapper.find('[data-codex-raw]').exists()).toBe(false)

    await wrapper.get('[data-codex-work-item] button').trigger('click')

    expect(wrapper.get('[data-codex-raw]').text()).toContain('{"ok":true}')
  })

  it('labels shell, edit, create, and tool activities with compact metadata', async () => {
    const wrapper = mountConversation([
      {
        id: 'command-1',
        type: 'commandExecution',
        command: '/bin/zsh -lc "pnpm test"',
        aggregatedOutput: 'ok',
        durationMs: 1530,
        output: { exitCode: 0 },
      },
      {
        id: 'file-create',
        type: 'fileChange',
        changes: {
          'src/NewFile.ts': { type: 'create', linesAdded: 12 },
        },
      },
      {
        id: 'file-edit',
        type: 'fileChange',
        changes: {
          'src/App.vue': { type: 'update', linesAdded: 2, linesRemoved: 1 },
        },
      },
      {
        id: 'tool-1',
        type: 'mcpToolCall',
        server: 'figma',
        tool: 'get_design_context',
        status: 'completed',
        durationMs: 425,
      },
    ])

    await wrapper.get('[data-codex-work-toggle]').trigger('click')
    const rows = wrapper.findAll('[data-codex-work-row]')
    expect(rows).toHaveLength(4)
    const [shellRow, createdRow, editedRow, toolRow] = rows

    expect(shellRow?.text()).toContain('Ran shell')
    expect(shellRow?.text()).toContain('pnpm test')
    expect(shellRow?.text()).toContain('1.53s')
    expect(shellRow?.text()).toContain('exit 0')
    expect(createdRow?.text()).toContain('Created')
    expect(createdRow?.text()).toContain('src/NewFile.ts')
    expect(editedRow?.text()).toContain('Edited')
    expect(editedRow?.text()).toContain('src/App.vue')
    expect(toolRow?.text()).toContain('Called figma / Get Design Context')
    expect(toolRow?.text()).toContain('get_design_context')
    expect(toolRow?.text()).toContain('425ms')
  })

  it('renders final turn work as a report card after the assistant response', () => {
    const wrapper = mountConversation([
      {
        id: 'files-1',
        type: 'fileChange',
        changes: {
          'src/App.vue': { type: 'update', linesAdded: 12, linesRemoved: 3 },
          'src/main.ts': { type: 'update', linesAdded: 2, linesRemoved: 0 },
        },
      },
      {
        id: 'command-1',
        type: 'commandExecution',
        command: 'pnpm test',
        aggregatedOutput: 'ok',
      },
      {
        id: 'agent-1',
        type: 'agentMessage',
        text: 'Done.',
      },
    ])

    const report = wrapper.get('[data-codex-turn-report]')
    expect(report.classes()).toEqual(
      expect.arrayContaining([
        'group/turn-diff-header',
        'flex',
        'max-w-full',
        'flex-col',
        'overflow-hidden',
        'rounded-lg',
        '[--thread-resource-card-row-padding-x:0.75rem]',
        '[--turn-diff-row-padding-y:0.25rem]',
      ]),
    )
    expect(report.text()).toContain('Turn report')
    expect(wrapper.get('[data-codex-turn-report-files]').text()).toContain('2 files')
    expect(wrapper.get('[data-codex-turn-report-lines]').text()).toContain('+14 / -3')
    expect(wrapper.get('[data-codex-turn-report-commands]').text()).toContain('1 command')
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
