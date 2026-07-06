import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PrimeVue from 'primevue/config'
import Popover from 'primevue/popover'

import CodexComposer from '../components/CodexComposer.vue'
import CodexHostPathPicker from '../components/CodexHostPathPicker.vue'

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

async function selectComposerValue(
  wrapper: ReturnType<typeof mount>,
  selector: string,
  value: string,
) {
  const select = wrapper.findComponent(selector)
  expect(select.exists()).toBe(true)
  const selectVm = select as unknown as { vm: { $emit: (event: string, value: string) => void } }
  selectVm.vm.$emit('update:modelValue', value)
  await wrapper.vm.$nextTick()
}

async function chooseIntelligenceValue(
  wrapper: ReturnType<typeof mount>,
  selector: string,
  value: string,
) {
  await wrapper.get('[data-codex-intelligence-trigger]').trigger('click')
  await wrapper.vm.$nextTick()
  const button = Array.from(document.body.querySelectorAll<HTMLElement>(selector)).find((option) =>
    option.textContent?.includes(value),
  )
  expect(button).toBeTruthy()
  button?.click()
  await wrapper.vm.$nextTick()
}

describe('CodexComposer', () => {
  const buildMode: CodexWorkMode = 'build'

  beforeEach(() => {
    document.body.innerHTML = ''
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

    await chooseIntelligenceValue(wrapper, '[data-codex-model-option]', 'GPT-5.4-Mini')
    await chooseIntelligenceValue(wrapper, '[data-codex-reasoning-option]', 'High')
    await wrapper.get('[data-codex-primary-submit]').trigger('click')

    expect(wrapper.emitted('sendPrompt')?.[0]).toEqual([
      {
        prompt: 'Build the UI',
        model: 'gpt-5.4-mini',
        reasoningEffort: 'high',
        approvalPolicy: 'never',
        approvalsReviewer: 'user',
        sandbox: 'danger-full-access',
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

  it('uses latest app-server token usage for context progress', () => {
    const wrapper = mountCodexComposer({
      modelValue: '',
      disabled: false,
      busy: false,
      isWorking: false,
      mode: buildMode,
      queuedFollowups: [],
      state: {
        id: 'thread-1',
        turns: [],
        latestTokenUsageInfo: {
          threadId: 'thread-1',
          turnId: 'turn-1',
          tokenUsage: {
            last: {
              cachedInputTokens: 0,
              inputTokens: 0,
              outputTokens: 0,
              reasoningOutputTokens: 0,
              totalTokens: 32_000,
            },
            total: {
              cachedInputTokens: 0,
              inputTokens: 0,
              outputTokens: 0,
              reasoningOutputTokens: 0,
              totalTokens: 96_000,
            },
            modelContextWindow: 128_000,
          },
        },
      },
    })

    const progress = wrapper.get('[data-codex-context-window]')

    expect(progress.attributes('aria-label')).toContain('25% used')
    expect(progress.attributes('aria-label')).toContain('32k / 128k tokens')
    expect(wrapper.get('[data-codex-context-ring]').attributes('style')).toContain(
      'conic-gradient',
    )
    expect(wrapper.get('[data-codex-context-ring]').attributes('style')).toContain('25%')
    expect(wrapper.get('[data-codex-context-tooltip]').text()).toContain('25% used')
    expect(wrapper.get('[data-codex-context-tooltip]').classes()).toEqual(
      expect.arrayContaining(['opacity-0', 'group-hover/context:opacity-100']),
    )
  })

  it('shows the latest todo list as a floating panel above the composer', async () => {
    const wrapper = mountCodexComposer({
      modelValue: '',
      disabled: false,
      busy: false,
      isWorking: true,
      mode: buildMode,
      queuedFollowups: [],
      state: {
        id: 'thread-1',
        turns: [
          {
            turnId: 'turn-1',
            status: 'inProgress',
            items: [
              {
                id: 'todo-1',
                type: 'todo-list',
                plan: [
                  { step: 'Read the official renderer', status: 'completed' },
                  { step: 'Align the local UI', status: 'in_progress' },
                ],
              },
            ],
          },
        ],
      },
    })

    const panel = wrapper.get('[data-codex-floating-todo-list]')

    expect(panel.classes()).toEqual(
      expect.arrayContaining([
        'relative',
        'z-10',
        'w-fit',
        'max-w-(--thread-content-max-width)',
        'min-w-0',
        'overflow-hidden',
        'rounded-3xl',
      ]),
    )
    expect(panel.text()).toContain('1 out of 2 tasks completed')
    expect(panel.text()).toContain('1/2')
    expect(wrapper.find('[data-codex-floating-todo-items]').exists()).toBe(false)

    await wrapper.get('[data-codex-floating-todo-toggle]').trigger('click')

    const todos = wrapper.findAll('[data-codex-floating-todo-item]')
    expect(todos).toHaveLength(2)
    expect(todos[0]?.text()).toContain('Read the official renderer')
    expect(todos[1]?.text()).toContain('Align the local UI')
  })

  it('does not estimate context progress from local message JSON', () => {
    const wrapper = mountCodexComposer({
      modelValue: '',
      disabled: false,
      busy: false,
      isWorking: false,
      mode: buildMode,
      queuedFollowups: [],
      state: {
        id: 'thread-1',
        turns: [
          {
            turnId: 'turn-1',
            status: 'completed',
            items: [{ id: 'item-1', type: 'agentMessage', text: 'x'.repeat(1000) }],
          },
        ],
      },
    })

    expect(wrapper.get('[data-codex-context-window]').attributes('aria-label')).toBe(
      'Token usage unavailable',
    )
    expect(wrapper.get('[data-codex-context-tooltip]').text()).toBe('Token usage unavailable')
  })

  it('keeps the composer footer compact and horizontally scrollable on mobile', async () => {
    const wrapper = mountCodexComposer({
      modelValue: '',
      disabled: false,
      busy: false,
      isWorking: false,
      mode: buildMode,
      queuedFollowups: [],
      state: { id: 'thread-1', turns: [] },
    })

    expect(wrapper.get('[data-codex-composer-shell]').classes()).toEqual(
      expect.arrayContaining(['sticky', 'bottom-0', 'z-10', 'mt-auto', 'w-full', 'pt-4']),
    )
    expect(wrapper.get('[data-pip-obstacle="thread-footer"]').classes()).toEqual(
      expect.arrayContaining(['relative', 'z-10', 'mx-auto', 'flex', 'w-full']),
    )
    expect(wrapper.get('[data-codex-composer]').classes()).toEqual(
      expect.arrayContaining(['grid', 'min-w-0', 'rounded-xl']),
    )
    expect(wrapper.get('[data-codex-composer-footer]').classes()).toEqual(
      expect.arrayContaining(['flex', 'items-center', 'justify-between', 'max-sm:gap-1']),
    )
    expect(wrapper.get('[data-codex-composer-controls]').classes()).toEqual(
      expect.arrayContaining([
        'min-w-0',
        'flex-1',
        'overflow-visible',
        'max-sm:gap-0.5',
      ]),
    )
    expect(wrapper.find('[data-codex-add-menu]').exists()).toBe(false)
    expect(wrapper.find('[data-codex-menu-plan]').exists()).toBe(false)
    expect(wrapper.find('[data-codex-image-attach]').exists()).toBe(false)
    expect(wrapper.get('[data-codex-permission-pill]').text()).toContain('Full access')
    expect(wrapper.get('[data-codex-context-window]').classes()).toContain('h-8')
    expect(wrapper.get('[data-codex-context-window]').classes()).toContain('max-sm:w-7')
    expect(wrapper.get('[data-codex-permission-trigger]').classes()).toEqual(
      expect.arrayContaining([
        'codex-permission-trigger',
        'codex-permission-tone-full',
        'max-sm:max-w-28',
      ]),
    )
    expect(wrapper.get('[data-codex-primary-submit]').classes()).toContain('max-sm:h-9')
    expect(wrapper.get('[data-codex-intelligence-trigger]').text()).toContain('5.4 Med')
    expect(wrapper.get('[data-codex-intelligence-trigger]').classes()).toEqual(
      expect.arrayContaining(['codex-intelligence-trigger', 'max-sm:max-w-28']),
    )
    expect(wrapper.find('[data-codex-model-select]').exists()).toBe(false)
    expect(wrapper.find('[data-codex-reasoning-select]').exists()).toBe(false)
    expect(wrapper.findComponent(Popover).exists()).toBe(true)
    await wrapper.get('[data-codex-intelligence-trigger]').trigger('click')
    await wrapper.vm.$nextTick()
    expect(document.body.querySelector('[data-codex-reasoning-section]')).toBeTruthy()
    expect(document.body.querySelector('[data-codex-model-section]')).toBeTruthy()
    expect(document.body.querySelector('[data-codex-reasoning-section]')?.textContent).toContain('Light')
    expect(document.body.querySelector('[data-codex-reasoning-section]')?.textContent).toContain('Extra High')
    expect(document.body.querySelector('[data-codex-reasoning-section]')?.textContent).not.toContain('min')
    expect(document.body.querySelector('[data-codex-reasoning-section]')?.textContent).not.toContain('xhigh')
    expect(wrapper.find('[data-codex-composer-settings-trigger]').exists()).toBe(false)
  })

  it('keeps plan and goal inside the add menu instead of the main toolbar', async () => {
    const wrapper = mountCodexComposer({
      modelValue: '',
      disabled: false,
      busy: false,
      isWorking: false,
      mode: buildMode,
      queuedFollowups: [],
      state: { id: 'thread-1', turns: [] },
    })

    expect(wrapper.find('[data-codex-menu-plan]').exists()).toBe(false)
    expect(wrapper.find('[data-codex-menu-goal]').exists()).toBe(false)

    await wrapper.get('[data-codex-add-menu-trigger]').trigger('click')
    await wrapper.get('[data-codex-menu-plan]').trigger('click')

    expect(wrapper.emitted('setMode')?.[0]).toEqual(['plan'])
  })

  it('closes the add popover when clicking outside or pressing escape', async () => {
    const wrapper = mountCodexComposer({
      modelValue: '',
      disabled: false,
      busy: false,
      isWorking: false,
      mode: buildMode,
      queuedFollowups: [],
      state: { id: 'thread-1', turns: [] },
    })

    await wrapper.get('[data-codex-add-menu-trigger]').trigger('click')
    expect(wrapper.find('[data-codex-add-menu]').exists()).toBe(true)

    document.body.click()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-codex-add-menu]').exists()).toBe(false)

    await wrapper.get('[data-codex-add-menu-trigger]').trigger('click')
    expect(wrapper.find('[data-codex-add-menu]').exists()).toBe(true)

    document.body.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Escape' }))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-codex-add-menu]').exists()).toBe(false)
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

  it('creates and controls a thread goal from the main composer input', async () => {
    const wrapper = mountCodexComposer({
      modelValue: '',
      disabled: false,
      busy: false,
      isWorking: false,
      mode: buildMode,
      queuedFollowups: [],
      state: { id: 'thread-1', turns: [] },
    })

    expect(wrapper.find('[data-codex-goal-objective]').exists()).toBe(false)

    await wrapper.get('[data-codex-add-menu-trigger]').trigger('click')
    await wrapper.get('[data-codex-menu-goal]').trigger('click')
    expect(wrapper.get('textarea').attributes('placeholder')).toBe('Describe a goal for this thread...')

    await wrapper.get('textarea').setValue('Finish goal mode')
    await wrapper.get('[data-codex-goal-token-budget]').setValue('12000')
    await wrapper.get('[data-codex-primary-submit]').trigger('click')

    expect(wrapper.emitted('setThreadGoal')?.[0]).toEqual(['Finish goal mode', 12000])
    const modelUpdates = wrapper.emitted('update:modelValue') ?? []
    expect(modelUpdates[modelUpdates.length - 1]).toEqual([''])

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
    await wrapper.get('[data-codex-add-menu-trigger]').trigger('click')
    expect(wrapper.get('[data-codex-menu-goal]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-codex-goal-pause]').trigger('click')
    await wrapper.get('[data-codex-goal-complete]').trigger('click')
    await wrapper.get('[data-codex-goal-clear]').trigger('click')

    expect(wrapper.emitted('updateThreadGoalStatus')?.[0]).toEqual(['paused'])
    expect(wrapper.emitted('updateThreadGoalStatus')?.[1]).toEqual(['complete'])
    expect(wrapper.emitted('clearThreadGoal')).toHaveLength(1)
  })

  it('attaches pasted images without exposing the image button on the main toolbar', async () => {
    class MockFileReader {
      result: string | ArrayBuffer | null = 'data:image/png;base64,abc'
      private listeners = new Map<string, () => void>()

      addEventListener(event: string, listener: () => void) {
        this.listeners.set(event, listener)
      }

      readAsDataURL() {
        this.listeners.get('load')?.()
      }
    }
    vi.stubGlobal('FileReader', MockFileReader)
    const wrapper = mountCodexComposer({
      modelValue: 'Review this',
      disabled: false,
      busy: false,
      isWorking: false,
      mode: buildMode,
      queuedFollowups: [],
      state: { id: 'thread-1', turns: [] },
    })
    const file = new File(['image'], 'screen.png', { type: 'image/png' })
    const preventDefault = vi.fn()

    expect(wrapper.find('[data-codex-image-attach]').exists()).toBe(false)

    const pasteEvent = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(pasteEvent, 'clipboardData', {
      value: {
        files: [file],
        items: [],
      },
    })
    Object.defineProperty(pasteEvent, 'preventDefault', { value: preventDefault })
    wrapper.get('textarea').element.dispatchEvent(pasteEvent)
    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()
    await wrapper.get('[data-codex-primary-submit]').trigger('click')

    expect(preventDefault).toHaveBeenCalled()
    expect(wrapper.emitted('sendPrompt')?.[0]?.[0]).toEqual({
      prompt: 'Review this',
      model: 'gpt-5.4',
      reasoningEffort: 'medium',
      approvalPolicy: 'never',
      approvalsReviewer: 'user',
      sandbox: 'danger-full-access',
      attachments: [
        {
          type: 'image',
          imageUrl: 'data:image/png;base64,abc',
          name: 'screen.png',
          mimeType: 'image/png',
        },
      ],
    })
  })

  it('attaches host files and folders as mention inputs from the add menu', async () => {
    const wrapper = mountCodexComposer({
      modelValue: 'Review the selected file',
      disabled: false,
      busy: false,
      isWorking: false,
      mode: buildMode,
      queuedFollowups: [],
      state: { id: 'thread-1', cwd: '/tmp/project', turns: [] },
    })

    await wrapper.get('[data-codex-add-menu-trigger]').trigger('click')
    await wrapper.get('[data-codex-files-attach]').trigger('click')
    wrapper.findComponent(CodexHostPathPicker).vm.$emit('select', '/tmp/project/main.py')
    await wrapper.vm.$nextTick()

    expect(wrapper.get('[data-codex-file-attachment]').text()).toContain('main.py')
    await wrapper.get('[data-codex-primary-submit]').trigger('click')

    expect(wrapper.emitted('sendPrompt')?.[0]?.[0]).toEqual({
      prompt: 'Review the selected file',
      model: 'gpt-5.4',
      reasoningEffort: 'medium',
      approvalPolicy: 'never',
      approvalsReviewer: 'user',
      sandbox: 'danger-full-access',
      attachments: [
        {
          type: 'mention',
          name: 'main.py',
          path: '/tmp/project/main.py',
        },
      ],
    })
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
    await selectComposerValue(wrapper, '[data-codex-run-location-trigger]', 'remote-1')

    expect(apiPostMock).toHaveBeenCalledWith(
      '/api/codex/remote-connections/remote-1/activate',
      {},
    )
    expect(wrapper.emitted('remoteConnectionChanged')).toHaveLength(1)
  })
})
