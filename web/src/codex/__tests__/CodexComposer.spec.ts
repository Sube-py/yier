import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Select from 'primevue/select'
import PrimeVue from 'primevue/config'

import CodexComposer from '../components/CodexComposer.vue'

import type { CodexConversationState, CodexWorkMode } from '../types'

const apiPostMock = vi.fn()

vi.mock('../../lib/api', () => ({
  apiPost: (...args: unknown[]) => apiPostMock(...args),
}))

type ComposerProps = InstanceType<typeof CodexComposer>['$props']

function mountCodexComposer(props: ComposerProps) {
  let wrapper: ReturnType<typeof mount>
  wrapper = mount(CodexComposer, {
    props: {
      ...props,
      'onUpdate:modelValue': (value: string) => wrapper.setProps({ modelValue: value }),
    },
    global: {
      plugins: [PrimeVue],
    },
  })
  return wrapper
}

describe('CodexComposer', () => {
  const buildMode: CodexWorkMode = 'build'

  beforeEach(() => {
    apiPostMock.mockReset()
    vi.stubGlobal('matchMedia', () => ({
      addEventListener: vi.fn(),
      addListener: vi.fn(),
      dispatchEvent: vi.fn(),
      matches: false,
      media: '',
      onchange: null,
      removeEventListener: vi.fn(),
      removeListener: vi.fn(),
    }))
  })

  it('sends the selected model and reasoning effort with the prompt', async () => {
    const wrapper = mountCodexComposer({
      modelValue: 'Build the UI',
      disabled: false,
      busy: false,
      isWorking: false,
      mode: buildMode,
      queuedFollowups: [],
      state: {
        id: 'thread-1',
        latestModel: 'gpt-5.4',
        latestReasoningEffort: 'medium',
        turns: [],
      },
    })

    const selects = wrapper.findAllComponents(Select)
    await selects[0]?.vm.$emit('update:modelValue', 'gpt-5.4-mini')
    await selects[1]?.vm.$emit('update:modelValue', 'high')
    await wrapper.get('[data-codex-primary-submit]').trigger('click')

    expect(wrapper.emitted('sendPrompt')?.[0]).toEqual([
      {
        prompt: 'Build the UI',
        model: 'gpt-5.4-mini',
        reasoningEffort: 'high',
      },
    ])
  })

  it('shows stop as the primary action while working with an empty draft', async () => {
    const wrapper = mountCodexComposer({
      modelValue: '',
      disabled: false,
      busy: false,
      isWorking: true,
      mode: buildMode,
      queuedFollowups: [],
      state: { id: 'thread-1', turns: [] },
    })

    const button = wrapper.get('[data-codex-primary-submit]')

    expect(button.attributes('aria-label')).toBe('Stop')

    await button.trigger('click')

    expect(wrapper.emitted('interruptTurn')).toHaveLength(1)
  })

  it('queues the draft from the primary send button while a turn is working', async () => {
    const wrapper = mountCodexComposer({
      modelValue: 'Run tests after this',
      disabled: false,
      busy: false,
      isWorking: true,
      mode: buildMode,
      queuedFollowups: [],
      state: { id: 'thread-1', turns: [] },
    })

    const button = wrapper.get('[data-codex-primary-submit]')

    expect(button.attributes('aria-label')).toBe('Send')

    await button.trigger('click')

    expect(wrapper.emitted('enqueueFollowup')?.[0]).toEqual(['Run tests after this'])
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([''])
  })

  it('shows queued follow-ups and context window progress', () => {
    const wrapper = mountCodexComposer({
      modelValue: '',
      disabled: false,
      busy: false,
      isWorking: true,
      mode: buildMode,
      queuedFollowups: [{ id: 'queued-1', prompt: 'Run tests next' }],
      state: {
        id: 'thread-1',
        turns: [],
        contextWindow: {
          usedTokens: 32_000,
          totalTokens: 128_000,
        },
      },
    })

    expect(wrapper.get('[data-codex-queued-followups]').text()).toContain('Run tests next')
    expect(wrapper.get('[data-codex-context-window]').text()).toContain('25%')
  })

  it('keeps secondary composer controls horizontally scrollable on mobile', () => {
    const wrapper = mountCodexComposer({
      modelValue: '',
      disabled: false,
      busy: false,
      isWorking: false,
      mode: buildMode,
      queuedFollowups: [],
      state: { id: 'thread-1', turns: [] },
    })

    expect(wrapper.get('[data-codex-composer]').classes()).toEqual(
      expect.arrayContaining(['min-w-0', 'max-sm:rounded-xl']),
    )
    expect(wrapper.get('[data-codex-composer-footer]').classes()).toEqual(
      expect.arrayContaining(['grid', 'grid-cols-[minmax(0,1fr)_auto]']),
    )
    expect(wrapper.get('[data-codex-composer-controls]').classes()).toEqual(
      expect.arrayContaining([
        'min-w-0',
        'flex-wrap',
        'max-sm:flex-nowrap',
        'max-sm:overflow-x-auto',
      ]),
    )
    expect(wrapper.get('[data-codex-mode-switch]').classes()).toEqual(
      expect.arrayContaining(['grid', 'w-[7.25rem]', 'grid-cols-2', 'shrink-0']),
    )
    const selects = wrapper.findAllComponents(Select)
    expect(selects[0]?.props('appendTo')).toBe('body')
    expect(selects[1]?.props('appendTo')).toBe('body')
    expect(wrapper.get('[data-codex-context-window]').classes()).toContain('max-sm:shrink-0')
  })

  it('does not add transient loading text while busy', () => {
    const wrapper = mountCodexComposer({
      modelValue: '',
      disabled: false,
      busy: true,
      isWorking: false,
      mode: buildMode,
      queuedFollowups: [],
      state: { id: 'thread-1', turns: [] },
    })

    expect(wrapper.text()).not.toContain('Working')
  })

  it('can steer or remove a queued follow-up from the attached queue', async () => {
    const wrapper = mountCodexComposer({
      modelValue: '',
      disabled: false,
      busy: false,
      isWorking: true,
      mode: buildMode,
      queuedFollowups: [{ id: 'queued-1', prompt: 'Focus on failing tests' }],
      state: { id: 'thread-1', turns: [] },
    })

    await wrapper.get('[data-codex-queued-steer]').trigger('click')

    expect(wrapper.emitted('steerPrompt')?.[0]).toEqual(['Focus on failing tests'])
    expect(wrapper.emitted('removeFollowup')?.[0]).toEqual(['queued-1'])

    await wrapper.get('[data-codex-queued-remove]').trigger('click')

    expect(wrapper.emitted('removeFollowup')?.[1]).toEqual(['queued-1'])
  })

  it('creates and controls a thread goal from the composer panel', async () => {
    const wrapper = mountCodexComposer({
      modelValue: '',
      disabled: false,
      busy: false,
      isWorking: false,
      mode: buildMode,
      queuedFollowups: [],
      state: { id: 'thread-1', turns: [] },
    })

    await wrapper.get('[data-codex-goal-objective]').setValue('Finish goal mode')
    await wrapper.get('[data-codex-goal-token-budget]').setValue('12000')
    await wrapper.get('[data-codex-goal-submit]').trigger('click')

    expect(wrapper.emitted('setThreadGoal')?.[0]).toEqual(['Finish goal mode', 12000])

    await wrapper.setProps({
      state: {
        id: 'thread-1',
        turns: [],
        threadGoal: {
          threadId: 'thread-1',
          objective: 'Finish goal mode',
          status: 'active',
          tokenBudget: 12000,
          tokensUsed: 3000,
          timeUsedSeconds: 90,
        },
      },
    })

    expect(wrapper.get('[data-codex-goal-status]').text()).toContain('Pursuing goal')
    expect(wrapper.get('[data-codex-goal-panel]').text()).toContain('3k / 12k tokens')

    await wrapper.get('[data-codex-goal-pause]').trigger('click')
    await wrapper.get('[data-codex-goal-complete]').trigger('click')
    await wrapper.get('[data-codex-goal-clear]').trigger('click')

    expect(wrapper.emitted('updateThreadGoalStatus')?.[0]).toEqual(['paused'])
    expect(wrapper.emitted('updateThreadGoalStatus')?.[1]).toEqual(['complete'])
    expect(wrapper.emitted('clearThreadGoal')).toHaveLength(1)
  })

  it('switches the run location from the composer footer', async () => {
    apiPostMock.mockResolvedValue({ ok: true })
    const wrapper = mountCodexComposer({
      modelValue: '',
      disabled: false,
      busy: false,
      isWorking: false,
      mode: buildMode,
      queuedFollowups: [],
      state: { id: 'thread-1', turns: [] },
      workspace: {
        projects: [],
        paired_editors: [],
        active_remote_connection_id: '',
        remote_connections: [
          {
            id: 'remote-1',
            display_name: 'Build host',
            ssh_host: 'user@host',
            ssh_port: 2222,
            ssh_alias: '',
            identity_file: '~/.ssh/build',
            remote_path: '/srv/app',
            auto_connect: false,
          },
        ],
      },
    })

    expect(wrapper.get('[data-codex-run-location-trigger]').text()).toContain('Local')
    await wrapper.get('[data-codex-run-location-trigger]').trigger('click')
    await wrapper.get('[data-codex-run-location-remote]').trigger('click')

    expect(apiPostMock).toHaveBeenCalledWith(
      '/api/codex/remote-connections/remote-1/activate',
      {},
    )
    expect(wrapper.emitted('remoteConnectionChanged')).toHaveLength(1)
  })
})
